"""Inter-company account configuration (fail-closed).

Section 4 of the business decision:
- due-from / due-to control accounts are **configurable per counterparty**, not
  hardcoded and not assumed from the chart of accounts;
- an accountant must **approve** the mapping before any inter-company posting may
  use it — :func:`get_approved_mapping` is the fail-closed guard that every future
  posting path must call;
- GST treatment for an actual inter-company transaction is derived from the real
  transaction (seller/buyer GST registration + place of supply) via the existing
  ``tax`` service — never assumed to be uniform across group companies.

This module delivers the configuration + approval + reconciliation surface. It
does **not** post anything; wiring inter-company sales/purchase posting onto an
approved mapping is a later, separately-approved step.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.accounting import AccountType, ChartOfAccount
from app.models.core import Company
from app.models.tenancy import IntercompanyAccountMapping


class IntercompanyConfigError(ValueError):
    """Inter-company configuration is missing, unapproved, or invalid (fail-closed)."""


def _account_in_company(db: Session, account_id: uuid.UUID, company_id: uuid.UUID) -> ChartOfAccount:
    acct = db.get(ChartOfAccount, account_id)
    if acct is None or acct.company_id != company_id:
        raise IntercompanyConfigError("Account not found in this company's chart of accounts")
    if acct.is_group or not acct.allow_posting or not acct.is_active:
        raise IntercompanyConfigError(f"Account {acct.code} is not an active posting account")
    return acct


def create_mapping(db: Session, *, company_id: uuid.UUID, counterparty_company_id: uuid.UUID,
                   due_from_account_id: uuid.UUID, due_to_account_id: uuid.UUID,
                   created_by: uuid.UUID | None = None) -> IntercompanyAccountMapping:
    if company_id == counterparty_company_id:
        raise IntercompanyConfigError("A company cannot be its own counterparty")
    if db.get(Company, company_id) is None:
        raise IntercompanyConfigError("Company not found")
    if db.get(Company, counterparty_company_id) is None:
        raise IntercompanyConfigError("Counterparty company not found")

    due_from = _account_in_company(db, due_from_account_id, company_id)
    due_to = _account_in_company(db, due_to_account_id, company_id)
    # control-account nature is validated, but the specific codes are the user's
    if due_from.account_type != AccountType.ASSET.value:
        raise IntercompanyConfigError("due_from must be an ASSET (receivable) account")
    if due_to.account_type != AccountType.LIABILITY.value:
        raise IntercompanyConfigError("due_to must be a LIABILITY (payable) account")

    existing = db.execute(select(IntercompanyAccountMapping).where(
        IntercompanyAccountMapping.company_id == company_id,
        IntercompanyAccountMapping.counterparty_company_id == counterparty_company_id,
    )).scalar_one_or_none()
    if existing is not None:
        raise IntercompanyConfigError("A mapping for this counterparty already exists")

    m = IntercompanyAccountMapping(
        company_id=company_id, counterparty_company_id=counterparty_company_id,
        due_from_account_id=due_from_account_id, due_to_account_id=due_to_account_id,
        is_approved=False, is_active=True, created_by=created_by)
    db.add(m)
    db.commit()
    return m


def approve_mapping(db: Session, mapping_id: uuid.UUID, approver_id: uuid.UUID) -> IntercompanyAccountMapping:
    m = db.get(IntercompanyAccountMapping, mapping_id)
    if m is None:
        raise IntercompanyConfigError("Mapping not found")
    m.is_approved = True
    m.approved_by = approver_id
    m.approved_at = datetime.now(timezone.utc)
    db.commit()
    return m


def get_approved_mapping(db: Session, company_id: uuid.UUID,
                         counterparty_company_id: uuid.UUID) -> IntercompanyAccountMapping:
    """Fail-closed: return the APPROVED, active mapping or raise. Every future
    inter-company posting path must obtain its control accounts through here."""
    m = db.execute(select(IntercompanyAccountMapping).where(
        IntercompanyAccountMapping.company_id == company_id,
        IntercompanyAccountMapping.counterparty_company_id == counterparty_company_id,
    )).scalar_one_or_none()
    if m is None:
        raise IntercompanyConfigError(
            "No inter-company account mapping configured for this counterparty")
    if not m.is_active or not m.is_approved:
        raise IntercompanyConfigError(
            "Inter-company mapping exists but is not approved by an accountant")
    return m


def reconciliation(db: Session) -> list[dict]:
    """For each configured mapping, report whether the reciprocal (counterparty's
    mapping back to this company) also exists and is approved — so both legs of an
    inter-company balance can be reconciled/eliminated."""
    rows = db.execute(select(IntercompanyAccountMapping)).scalars().all()
    by_pair = {(m.company_id, m.counterparty_company_id): m for m in rows}
    out = []
    for m in rows:
        recip = by_pair.get((m.counterparty_company_id, m.company_id))
        out.append({
            "id": str(m.id),
            "company_id": str(m.company_id),
            "counterparty_company_id": str(m.counterparty_company_id),
            "approved": m.is_approved,
            "reciprocal_configured": recip is not None,
            "reciprocal_approved": bool(recip and recip.is_approved),
            "ready_for_elimination": bool(m.is_approved and recip and recip.is_approved),
        })
    return out


# --- inter-company posting (dual-leg, atomic, fail-closed) -------------------

def post_intercompany_charge(
    db: Session, *, seller_company_id: uuid.UUID, buyer_company_id: uuid.UUID,
    taxable, voucher_date, reference: str, tax_code_id: uuid.UUID | None = None,
    narration: str | None = None, created_by: uuid.UUID | None = None,
    seller_income_code: str | None = None, buyer_expense_code: str | None = None,
):
    """Post both legs of an inter-company charge atomically.

    Seller (A): Dr due-from(B) [control, from A's approved mapping];
                Cr income; Cr output GST.
    Buyer  (B): Dr expense; Dr input GST;
                Cr due-to(A) [control, from B's approved mapping].

    Fail-closed: both mappings must be approved (``get_approved_mapping``). GST is
    derived from each company's state code (place of supply) and the tax code —
    never assumed. Both vouchers are posted in one transaction (commit once), so
    the two legs are all-or-nothing.
    """
    from decimal import Decimal

    from app.models.accounting import ChartOfAccount
    from app.models.core import Company
    from app.models.masters import TaxCode
    from app.services.accounting import posting_engine as pe
    from app.services.accounting.posting_rules import resolve_account
    from app.services.tax import compute_gst, resolve_intra_state

    if seller_company_id == buyer_company_id:
        raise IntercompanyConfigError("Seller and buyer must be different companies")

    # fail-closed: both directions approved
    seller_map = get_approved_mapping(db, seller_company_id, buyer_company_id)
    buyer_map = get_approved_mapping(db, buyer_company_id, seller_company_id)

    seller = db.get(Company, seller_company_id)
    buyer = db.get(Company, buyer_company_id)
    tax_code = db.get(TaxCode, tax_code_id) if tax_code_id else None

    # GST from actual place of supply (fail-closed on missing state codes)
    intra = resolve_intra_state(seller.state_code, buyer.state_code, context="Inter-company")
    g = compute_gst(tax_code, taxable, intra_state=intra)
    taxable = g["total"] - g["total_tax"]
    gross = g["total"]

    due_from_code = db.get(ChartOfAccount, seller_map.due_from_account_id).code
    due_to_code = db.get(ChartOfAccount, buyer_map.due_to_account_id).code

    def _acct(company_id, event, slot):
        return resolve_account(db, company_id, event, slot)

    # ---- seller leg (A) ----
    income_code = seller_income_code or _acct(seller_company_id, "INTERCOMPANY_SALE", "income")
    seller_lines = [
        pe.PostingLine(due_from_code, debit=gross, narration=f"Due from {buyer.code}"),
        pe.PostingLine(income_code, credit=taxable, narration="Inter-company income"),
    ]
    if g["cgst"] > 0:
        seller_lines.append(pe.PostingLine(_acct(seller_company_id, "INTERCOMPANY_SALE", "output_cgst"), credit=g["cgst"], narration="Output CGST"))
    if g["sgst"] > 0:
        seller_lines.append(pe.PostingLine(_acct(seller_company_id, "INTERCOMPANY_SALE", "output_sgst"), credit=g["sgst"], narration="Output SGST"))
    if g["igst"] > 0:
        seller_lines.append(pe.PostingLine(_acct(seller_company_id, "INTERCOMPANY_SALE", "output_igst"), credit=g["igst"], narration="Output IGST"))

    # ---- buyer leg (B) ----
    expense_code = buyer_expense_code or _acct(buyer_company_id, "INTERCOMPANY_PURCHASE", "expense")
    buyer_lines = [
        pe.PostingLine(expense_code, debit=taxable, narration="Inter-company purchase"),
    ]
    if g["cgst"] > 0:
        buyer_lines.append(pe.PostingLine(_acct(buyer_company_id, "INTERCOMPANY_PURCHASE", "input_cgst"), debit=g["cgst"], narration="Input CGST"))
    if g["sgst"] > 0:
        buyer_lines.append(pe.PostingLine(_acct(buyer_company_id, "INTERCOMPANY_PURCHASE", "input_sgst"), debit=g["sgst"], narration="Input SGST"))
    if g["igst"] > 0:
        buyer_lines.append(pe.PostingLine(_acct(buyer_company_id, "INTERCOMPANY_PURCHASE", "input_igst"), debit=g["igst"], narration="Input IGST"))
    buyer_lines.append(pe.PostingLine(due_to_code, credit=gross, narration=f"Due to {seller.code}"))

    v_seller = pe.post_voucher(db, pe.PostingRequest(
        company_id=seller_company_id, voucher_type_code="SI", voucher_date=voucher_date,
        posting_reference=f"INTERCO:{reference}:SELL", lines=seller_lines,
        narration=narration or f"Inter-company charge to {buyer.code}",
        source_module="INTERCOMPANY", source_id=reference, created_by=created_by))
    v_buyer = pe.post_voucher(db, pe.PostingRequest(
        company_id=buyer_company_id, voucher_type_code="PI", voucher_date=voucher_date,
        posting_reference=f"INTERCO:{reference}:BUY", lines=buyer_lines,
        narration=narration or f"Inter-company charge from {seller.code}",
        source_module="INTERCOMPANY", source_id=reference, created_by=created_by))

    db.commit()
    return {
        "reference": reference,
        "intra_state": intra,
        "taxable": str(taxable), "total_tax": str(g["total_tax"]), "gross": str(gross),
        "seller_voucher": {"company_id": str(seller_company_id), "id": str(v_seller.id), "number": v_seller.number},
        "buyer_voucher": {"company_id": str(buyer_company_id), "id": str(v_buyer.id), "number": v_buyer.number},
    }
