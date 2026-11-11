"""Legacy / Unassigned factory backfill for historical operational rows.

Safeguards honoured (business decision, section 2):
- Does NOT invent operational factory ownership. Historical rows with no
  establishable factory are assigned to an explicit, clearly-labelled
  **Legacy / Unassigned** factory per company (``is_legacy_unassigned=True``),
  which is not an operational site.
- Company ownership and historical transaction data are untouched; only the
  previously-NULL ``factory_id`` is filled.
- Rows assigned to a legacy factory are, by definition, **flagged for
  reconciliation** (queryable via :func:`reconciliation_report`).
- Nothing runs silently: :func:`backfill_legacy_factory` is a no-op preview
  unless ``commit=True`` (the CLI requires an explicit ``--commit``).
- Idempotent and reversible (:func:`revert_legacy_backfill`). ``factory_id``
  stays nullable; ownership is never made mandatory here.

The set of factory-owned tables mirrors migration 0003.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.core import Branch, Company
from app.models.manufacturing import DyeBatch, FabricRoll, KnittingProduction
from app.models.masters import Machine
from app.models.quality import FinishingTransaction, QualityInspection
from app.models.dispensing import DispensingTransaction

LEGACY_CODE = "LEGACY"
LEGACY_NAME = "Legacy / Unassigned (reconcile)"

# model classes that carry a nullable factory_id (migration 0003)
FACTORY_MODELS = [
    Machine,
    KnittingProduction,
    FabricRoll,
    DyeBatch,
    FinishingTransaction,
    QualityInspection,
    DispensingTransaction,
]


@dataclass
class BackfillReport:
    committed: bool
    legacy_factories: dict[str, str] = field(default_factory=dict)  # company_id -> branch_id
    per_table_assigned: dict[str, int] = field(default_factory=dict)
    total_assigned: int = 0


def _legacy_branch_for(db: Session, company_id: uuid.UUID, *, create: bool) -> Branch | None:
    br = db.execute(
        select(Branch).where(
            Branch.company_id == company_id,
            Branch.is_legacy_unassigned.is_(True),
        )
    ).scalars().first()
    if br is not None or not create:
        return br
    br = Branch(company_id=company_id, code=LEGACY_CODE, name=LEGACY_NAME,
               is_active=True, is_legacy_unassigned=True)
    db.add(br)
    db.flush()
    return br


def backfill_legacy_factory(db: Session, *, commit: bool = False) -> BackfillReport:
    """Assign NULL-factory historical rows to each company's Legacy/Unassigned
    factory. Preview-only unless ``commit=True``.

    Returns a reconciliation report of what was (or would be) assigned.
    """
    report = BackfillReport(committed=commit)
    companies = db.execute(select(Company.id)).scalars().all()

    # map each company to its legacy factory (created only when committing)
    legacy_by_company: dict[uuid.UUID, Branch] = {}
    for cid in companies:
        br = _legacy_branch_for(db, cid, create=commit)
        if br is not None:
            legacy_by_company[cid] = br
            report.legacy_factories[str(cid)] = str(br.id)

    for model in FACTORY_MODELS:
        table_name = model.__tablename__
        # count NULL-factory rows grouped by company
        rows = db.execute(
            select(model.company_id, func.count()).where(model.factory_id.is_(None))
            .group_by(model.company_id)
        ).all()
        assigned = 0
        for company_id, cnt in rows:
            assigned += int(cnt)
            if commit:
                legacy = legacy_by_company.get(company_id)
                if legacy is None:
                    # company has rows but was created mid-run; make its bucket now
                    legacy = _legacy_branch_for(db, company_id, create=True)
                    legacy_by_company[company_id] = legacy
                    report.legacy_factories[str(company_id)] = str(legacy.id)
                db.execute(
                    update(model)
                    .where(model.factory_id.is_(None), model.company_id == company_id)
                    .values(factory_id=legacy.id)
                )
        report.per_table_assigned[table_name] = assigned
        report.total_assigned += assigned

    if commit:
        db.commit()
    else:
        db.rollback()  # discard any created legacy branches in preview mode
    return report


def reconciliation_report(db: Session) -> dict:
    """Rows currently sitting on a Legacy/Unassigned factory, per table — i.e.
    the historical rows still pending reconciliation to a real factory."""
    legacy_ids = set(db.execute(
        select(Branch.id).where(Branch.is_legacy_unassigned.is_(True))
    ).scalars().all())
    out: dict[str, int] = {}
    total = 0
    for model in FACTORY_MODELS:
        if not legacy_ids:
            out[model.__tablename__] = 0
            continue
        n = db.execute(
            select(func.count()).where(model.factory_id.in_(legacy_ids))
        ).scalar_one()
        out[model.__tablename__] = int(n)
        total += int(n)
    return {"pending_reconciliation": out, "total": total,
            "legacy_factory_ids": [str(i) for i in legacy_ids]}


def revert_legacy_backfill(db: Session, *, commit: bool = False) -> dict:
    """Reverse the backfill: clear factory_id on rows that point at a legacy
    factory, and remove the (now empty) legacy factories. Preview unless commit."""
    legacy_ids = set(db.execute(
        select(Branch.id).where(Branch.is_legacy_unassigned.is_(True))
    ).scalars().all())
    cleared = 0
    for model in FACTORY_MODELS:
        if not legacy_ids:
            continue
        res = db.execute(
            select(func.count()).where(model.factory_id.in_(legacy_ids))
        ).scalar_one()
        cleared += int(res)
        if commit:
            db.execute(
                update(model).where(model.factory_id.in_(legacy_ids)).values(factory_id=None)
            )
    removed = 0
    if commit:
        for bid in legacy_ids:
            br = db.get(Branch, bid)
            if br is not None:
                db.delete(br)
                removed += 1
        db.commit()
    else:
        db.rollback()
    return {"committed": commit, "rows_cleared": cleared, "legacy_factories_removed": removed}
