"""Group (Organization) consolidated reporting — READ-ONLY.

Aggregates the independent trial balances of an organization's member companies
into a combined group trial balance, then applies **approved, report-only**
elimination adjustments.

Safeguards (business decision section 5):
- Nothing is posted to any company ledger; each company's statutory books are
  untouched. This module only reads.
- Eliminations live in ``consolidation_eliminations`` and apply ONLY to the
  consolidated figures here.
- Only **approved** eliminations are applied; unapproved ones are reported as
  pending and the consolidation is marked not-yet-approved.
- Member companies are those whose ``organization_id`` matches; companies outside
  the group are never included.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.core import Company
from app.models.tenancy import ConsolidationElimination, Organization
from app.services.accounting import trial_balance as tb

ZERO = Decimal("0")


def _split(net: Decimal) -> tuple[Decimal, Decimal]:
    return (net, ZERO) if net >= ZERO else (ZERO, -net)


def member_company_ids(db: Session, organization_id: uuid.UUID) -> list[uuid.UUID]:
    return list(db.execute(
        select(Company.id).where(
            Company.organization_id == organization_id,
            Company.is_active.is_(True),
        )
    ).scalars().all())


def consolidated_trial_balance(
    db: Session,
    organization_id: uuid.UUID,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    org = db.get(Organization, organization_id)
    if org is None:
        raise ValueError("Organization not found")

    companies = db.execute(
        select(Company).where(
            Company.organization_id == organization_id,
            Company.is_active.is_(True),
        ).order_by(Company.code)
    ).scalars().all()

    # aggregate each member company's trial balance by account code
    agg: dict[str, dict] = {}
    for c in companies:
        result = tb.trial_balance(db, c.id, date_from=date_from, date_to=date_to)
        for r in result["rows"]:
            code = r["account_code"]
            slot = agg.setdefault(code, {
                "account_code": code,
                "account_name": r["account_name"],
                "account_type": r["account_type"],
                "combined_net": ZERO,  # debit-positive
            })
            slot["combined_net"] += Decimal(str(r["closing_debit"])) - Decimal(str(r["closing_credit"]))

    # approved eliminations apply to the consolidated figures; unapproved pending
    elims = db.execute(
        select(ConsolidationElimination).where(
            ConsolidationElimination.organization_id == organization_id)
    ).scalars().all()
    elim_by_code: dict[str, Decimal] = {}
    applied, pending = [], []
    for e in elims:
        net = Decimal(str(e.debit or 0)) - Decimal(str(e.credit or 0))
        entry = {"account_code": e.account_code, "description": e.description,
                 "debit": str(e.debit or 0), "credit": str(e.credit or 0),
                 "counterparty_ref": e.counterparty_ref, "is_approved": e.is_approved}
        if e.is_approved:
            elim_by_code[e.account_code] = elim_by_code.get(e.account_code, ZERO) + net
            applied.append(entry)
        else:
            pending.append(entry)

    # build consolidated rows
    rows = []
    tot_comb_d = tot_comb_c = ZERO
    tot_elim_d = tot_elim_c = ZERO
    tot_cons_d = tot_cons_c = ZERO
    all_codes = set(agg) | set(elim_by_code)
    for code in sorted(all_codes):
        base = agg.get(code)
        combined_net = base["combined_net"] if base else ZERO
        elim_net = elim_by_code.get(code, ZERO)
        cons_net = combined_net + elim_net
        cd, cc = _split(combined_net)
        ed, ec = _split(elim_net)
        nd, nc = _split(cons_net)
        rows.append({
            "account_code": code,
            "account_name": base["account_name"] if base else code,
            "account_type": base["account_type"] if base else None,
            "combined_debit": cd, "combined_credit": cc,
            "elimination_debit": ed, "elimination_credit": ec,
            "consolidated_debit": nd, "consolidated_credit": nc,
        })
        tot_comb_d += cd; tot_comb_c += cc
        tot_elim_d += ed; tot_elim_c += ec
        tot_cons_d += nd; tot_cons_c += nc

    return {
        "organization": {"id": str(org.id), "code": org.code, "name": org.name},
        "companies": [{"id": str(c.id), "code": c.code, "name": c.name} for c in companies],
        "rows": rows,
        "totals": {
            "combined_debit": tot_comb_d, "combined_credit": tot_comb_c,
            "elimination_debit": tot_elim_d, "elimination_credit": tot_elim_c,
            "consolidated_debit": tot_cons_d, "consolidated_credit": tot_cons_c,
        },
        "eliminations_applied": applied,
        "eliminations_pending": pending,
        "is_balanced": tot_cons_d == tot_cons_c,
        "approved": len(pending) == 0,  # finance-approved only when none pending
        "note": "Read-only consolidation. No entries are posted to any company ledger.",
    }
