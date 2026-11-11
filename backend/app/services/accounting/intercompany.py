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
