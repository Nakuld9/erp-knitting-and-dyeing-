"""Cross-database inter-company posting as a recoverable saga.

Two company databases cannot be committed in one SQLAlchemy transaction, so an
inter-company charge is posted as a saga:

  1. record an ``IntercompanyTxn`` (central DB), keyed by an idempotency key;
  2. post the **seller** leg in company A's database (its own transaction);
  3. post the **buyer** leg in company B's database (its own transaction);
  4. on a buyer-leg failure, **compensate** by reversing the seller leg.

Idempotency: the saga is unique on ``idempotency_key``; each leg's
``posting_reference`` is derived from it, and ``post_voucher`` is idempotent on
the posting reference — so retries and re-runs never double-post. Non-terminal
sagas are completed or compensated by :func:`reconcile`.

Live cross-database posting is **disabled by default**
(``settings.intercompany_live_posting``) until the saga passes real-PostgreSQL
two-database tests.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db_router import company_session
from app.models.core import Company
from app.models.masters import TaxCode
from app.models.tenancy import IntercompanyTxn, IntercompanyTxnStatus
from app.services.accounting import posting_engine as pe
from app.services.accounting.intercompany import IntercompanyConfigError, get_approved_mapping
from app.services.accounting.posting_rules import resolve_account
from app.services.tax import compute_gst, resolve_intra_state

ZERO = Decimal("0")


class IntercompanySagaError(RuntimeError):
    pass


def _gst(control_db, seller, buyer, taxable, tax_code_id):
    tax_code = control_db.get(TaxCode, tax_code_id) if tax_code_id else None
    intra = resolve_intra_state(seller.state_code, buyer.state_code, context="Inter-company")
    g = compute_gst(tax_code, taxable, intra_state=intra)
    return g, intra


def _seller_lines(cdb, company_id, due_from_code, g, taxable, income_code=None):
    lines = [pe.PostingLine(due_from_code, debit=g["total"], narration="Due from related company"),
             pe.PostingLine(income_code or resolve_account(cdb, company_id, "INTERCOMPANY_SALE", "income"),
                            credit=taxable, narration="Inter-company income")]
    for slot, amt in (("output_cgst", g["cgst"]), ("output_sgst", g["sgst"]), ("output_igst", g["igst"])):
        if amt > 0:
            lines.append(pe.PostingLine(resolve_account(cdb, company_id, "INTERCOMPANY_SALE", slot),
                                        credit=amt, narration=slot.upper()))
    return lines


def _buyer_lines(cdb, company_id, due_to_code, g, taxable, expense_code=None):
    lines = [pe.PostingLine(expense_code or resolve_account(cdb, company_id, "INTERCOMPANY_PURCHASE", "expense"),
                            debit=taxable, narration="Inter-company purchase")]
    for slot, amt in (("input_cgst", g["cgst"]), ("input_sgst", g["sgst"]), ("input_igst", g["igst"])):
        if amt > 0:
            lines.append(pe.PostingLine(resolve_account(cdb, company_id, "INTERCOMPANY_PURCHASE", slot),
                                        debit=amt, narration=slot.upper()))
    lines.append(pe.PostingLine(due_to_code, credit=g["total"], narration="Due to related company"))
    return lines


def post_intercompany_saga(control_db: Session, *, seller_company_id: uuid.UUID,
                           buyer_company_id: uuid.UUID, taxable, idempotency_key: str,
                           voucher_date: date, tax_code_id: uuid.UUID | None = None,
                           created_by: uuid.UUID | None = None,
                           require_live_flag: bool = True) -> IntercompanyTxn:
    """Post an inter-company charge across two company databases as a saga.

    ``require_live_flag`` guards production; tests pass False to exercise the saga
    against the (single) test database via the router's fallback.
    """
    if require_live_flag and not settings.intercompany_live_posting:
        raise IntercompanySagaError(
            "Cross-database inter-company live posting is disabled "
            "(settings.intercompany_live_posting is false)")
    if seller_company_id == buyer_company_id:
        raise IntercompanyConfigError("Seller and buyer must be different companies")

    # idempotency: one saga per key
    txn = control_db.execute(select(IntercompanyTxn).where(
        IntercompanyTxn.idempotency_key == idempotency_key)).scalar_one_or_none()
    if txn is None:
        txn = IntercompanyTxn(idempotency_key=idempotency_key, seller_company_id=seller_company_id,
                              buyer_company_id=buyer_company_id, taxable=Decimal(str(taxable)),
                              tax_code_id=tax_code_id, voucher_date=voucher_date.isoformat(),
                              status=IntercompanyTxnStatus.PENDING, created_by=created_by)
        control_db.add(txn); control_db.commit()
    if txn.status == IntercompanyTxnStatus.COMPLETED:
        return txn

    # fail-closed config: both mappings approved; codes resolved per company DB
    seller_map = get_approved_mapping(control_db, seller_company_id, buyer_company_id)
    buyer_map = get_approved_mapping(control_db, buyer_company_id, seller_company_id)
    due_from_code = seller_map.due_from_account_code
    due_to_code = buyer_map.due_to_account_code
    if not due_from_code or not due_to_code:
        raise IntercompanyConfigError("Mapping is missing account codes (re-create the mapping)")

    seller = control_db.get(Company, seller_company_id)
    buyer = control_db.get(Company, buyer_company_id)
    g, _intra = _gst(control_db, seller, buyer, Decimal(str(taxable)), tax_code_id)
    taxable_amt = g["total"] - g["total_tax"]
    txn.attempts += 1; control_db.commit()

    # ---- seller leg (company A's database) ----
    try:
        with company_session(seller_company_id) as sdb:
            v = pe.post_voucher(sdb, pe.PostingRequest(
                company_id=seller_company_id, voucher_type_code="SI", voucher_date=voucher_date,
                posting_reference=f"ICSAGA:{idempotency_key}:SELL",
                lines=_seller_lines(sdb, seller_company_id, due_from_code, g, taxable_amt),
                source_module="intercompany_saga", source_id=idempotency_key, created_by=created_by))
            sdb.commit()
            seller_voucher = v.number
    except Exception as e:  # seller leg failed -> nothing committed in A; FAILED
        txn.status = IntercompanyTxnStatus.FAILED; txn.last_error = str(e)[:500]; control_db.commit()
        raise IntercompanySagaError(f"Seller leg failed: {e}")
    txn.status = IntercompanyTxnStatus.SELLER_POSTED; txn.seller_voucher_number = seller_voucher
    control_db.commit()

    # ---- buyer leg (company B's database) ----
    try:
        with company_session(buyer_company_id) as bdb:
            v = pe.post_voucher(bdb, pe.PostingRequest(
                company_id=buyer_company_id, voucher_type_code="PI", voucher_date=voucher_date,
                posting_reference=f"ICSAGA:{idempotency_key}:BUY",
                lines=_buyer_lines(bdb, buyer_company_id, due_to_code, g, taxable_amt),
                source_module="intercompany_saga", source_id=idempotency_key, created_by=created_by))
            bdb.commit()
            buyer_voucher = v.number
    except Exception as e:  # buyer leg failed -> COMPENSATE the seller leg
        _compensate_seller(seller_company_id, idempotency_key, g, due_from_code, taxable_amt,
                           seller_company_id, created_by)
        txn.status = IntercompanyTxnStatus.COMPENSATED; txn.last_error = str(e)[:500]; control_db.commit()
        raise IntercompanySagaError(f"Buyer leg failed; seller leg compensated: {e}")

    txn.status = IntercompanyTxnStatus.COMPLETED; txn.buyer_voucher_number = buyer_voucher
    control_db.commit()
    return txn


def _compensate_seller(seller_company_id, idempotency_key, g, due_from_code, taxable_amt,
                       income_company_id, created_by):
    """Reverse the seller leg (reversal-only correction). Idempotent on its own
    posting reference, so a retried compensation does not double-reverse."""
    with company_session(seller_company_id) as sdb:
        # reverse = swap debit/credit of the seller leg
        lines = []
        for ln in _seller_lines(sdb, seller_company_id, due_from_code, g, taxable_amt):
            lines.append(pe.PostingLine(ln.account_code, debit=ln.credit, credit=ln.debit,
                                        narration="Reversal: " + (ln.narration or "")))
        pe.post_voucher(sdb, pe.PostingRequest(
            company_id=seller_company_id, voucher_type_code="JV", voucher_date=date.today(),
            posting_reference=f"ICSAGA:{idempotency_key}:SELL-REV", lines=lines,
            source_module="intercompany_saga", source_id=idempotency_key, created_by=created_by))
        sdb.commit()


def reconcile(control_db: Session, *, require_live_flag: bool = True) -> dict:
    """Drive non-terminal sagas to a terminal state (retry buyer leg or report).

    Returns a summary. SELLER_POSTED sagas are retried (buyer leg idempotent);
    PENDING/FAILED are reported for operator attention.
    """
    rows = control_db.execute(select(IntercompanyTxn).where(
        IntercompanyTxn.status.in_([IntercompanyTxnStatus.SELLER_POSTED,
                                    IntercompanyTxnStatus.PENDING,
                                    IntercompanyTxnStatus.FAILED]))).scalars().all()
    completed, still_open = 0, 0
    for t in rows:
        if t.status == IntercompanyTxnStatus.SELLER_POSTED:
            try:
                post_intercompany_saga(
                    control_db, seller_company_id=t.seller_company_id,
                    buyer_company_id=t.buyer_company_id, taxable=t.taxable,
                    idempotency_key=t.idempotency_key,
                    voucher_date=date.fromisoformat(t.voucher_date), tax_code_id=t.tax_code_id,
                    require_live_flag=require_live_flag)
                completed += 1
            except Exception:
                still_open += 1
        else:
            still_open += 1
    return {"completed": completed, "still_open": still_open, "total_scanned": len(rows)}
