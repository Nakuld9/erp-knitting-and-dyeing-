"""Legacy / Unassigned factory backfill — safe, idempotent, reversible.

Proves the section-2 safeguards:
- preview (commit=False) writes nothing;
- commit assigns NULL-factory historical rows to an explicit per-company
  Legacy/Unassigned factory (is_legacy_unassigned=True), flagged for
  reconciliation;
- company ownership is untouched; factory_id stays nullable;
- idempotent re-run assigns nothing new; revert clears it.
"""
from __future__ import annotations

from sqlalchemy import select

from app.models.core import Branch, Company
from app.models.masters import Machine
from app.services.tenancy_backfill import (
    backfill_legacy_factory,
    reconciliation_report,
    revert_legacy_backfill,
)


def _machine(db, company_id, code):
    m = Machine(company_id=company_id, machine_type="KNITTING", code=code, name=f"M {code}")
    db.add(m)
    db.flush()
    return m


def _setup(db):
    company_a = db.execute(select(Company).where(Company.code == "DEMO")).scalar_one()
    company_b = Company(code="BK-B", name="Backfill Co B")
    db.add(company_b)
    db.flush()
    _machine(db, company_a.id, "A-1")
    _machine(db, company_a.id, "A-2")
    _machine(db, company_b.id, "B-1")
    db.commit()
    return company_a, company_b


def test_preview_writes_nothing(db):
    company_a, company_b = _setup(db)
    report = backfill_legacy_factory(db, commit=False)
    assert report.committed is False
    assert report.total_assigned == 3  # 2 in A, 1 in B
    # nothing persisted: no legacy branch, machines still NULL
    assert db.execute(select(Branch).where(Branch.is_legacy_unassigned.is_(True))).first() is None
    assert db.execute(select(Machine).where(Machine.factory_id.isnot(None))).first() is None


def test_commit_assigns_to_legacy_factory(db):
    company_a, company_b = _setup(db)
    report = backfill_legacy_factory(db, commit=True)
    assert report.committed is True
    assert report.total_assigned == 3
    assert report.per_table_assigned["machines"] == 3

    # a legacy factory exists per company and is flagged
    legacy = db.execute(select(Branch).where(Branch.is_legacy_unassigned.is_(True))).scalars().all()
    assert len(legacy) == 2
    legacy_ids = {b.id for b in legacy}
    # every machine now points at its company's legacy factory
    for m in db.execute(select(Machine)).scalars().all():
        assert m.factory_id in legacy_ids
        owner_legacy = db.execute(select(Branch).where(
            Branch.company_id == m.company_id, Branch.is_legacy_unassigned.is_(True))).scalar_one()
        assert m.factory_id == owner_legacy.id  # assigned within the SAME company

    # company ownership untouched
    assert db.execute(select(Machine).where(Machine.code == "A-1")).scalar_one().company_id == company_a.id


def test_commit_is_idempotent(db):
    _setup(db)
    backfill_legacy_factory(db, commit=True)
    again = backfill_legacy_factory(db, commit=True)
    assert again.total_assigned == 0  # nothing left with NULL factory


def test_reconciliation_report_lists_pending(db):
    _setup(db)
    backfill_legacy_factory(db, commit=True)
    rep = reconciliation_report(db)
    assert rep["pending_reconciliation"]["machines"] == 3
    assert rep["total"] == 3
    assert len(rep["legacy_factory_ids"]) == 2


def test_revert_clears_backfill(db):
    _setup(db)
    backfill_legacy_factory(db, commit=True)
    result = revert_legacy_backfill(db, commit=True)
    assert result["rows_cleared"] == 3
    assert result["legacy_factories_removed"] == 2
    # machines are NULL again; no legacy factories remain
    assert db.execute(select(Machine).where(Machine.factory_id.isnot(None))).first() is None
    assert db.execute(select(Branch).where(Branch.is_legacy_unassigned.is_(True))).first() is None


def test_reconciliation_endpoint(client, auth_headers, db):
    """The admin reconciliation endpoint surfaces pending rows (read-only)."""
    _setup(db)
    backfill_legacy_factory(db, commit=True)
    r = client.get("/api/admin/factory-reconciliation", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["pending_reconciliation"]["machines"] == 3
    assert r.json()["total"] == 3
