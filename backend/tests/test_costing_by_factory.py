"""Phase 9: factory-wise manufacturing throughput & efficiency."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models.core import Branch, Company
from app.models.manufacturing import KnittingProduction
from app.models.masters import Machine, YarnMaster
from app.services import costing


def _company(db):
    return db.execute(select(Company).where(Company.code == "DEMO")).scalar_one()


def _kp(db, company_id, factory_id, machine_id, yarn_id, number, produced, wastage, rejection):
    db.add(KnittingProduction(
        company_id=company_id, factory_id=factory_id, number=number, machine_id=machine_id,
        yarn_id=yarn_id, production_date=date(2026, 4, 15),
        produced_kg=produced, wastage_kg=wastage, rejection_kg=rejection, yarn_consumed_kg=produced))


def test_production_grouped_by_factory(db):
    a = _company(db)
    f1 = Branch(company_id=a.id, code="CF1", name="Cost F1")
    f2 = Branch(company_id=a.id, code="CF2", name="Cost F2")
    db.add_all([f1, f2])
    db.flush()
    m = Machine(company_id=a.id, machine_type="KNITTING", code="MK1", name="Knit 1")
    y = YarnMaster(company_id=a.id, code="Y1", name="Cotton 30s")
    db.add_all([m, y])
    db.flush()
    # F1: produced 90, wastage 5, rejection 5 -> efficiency 90/100 = 90%
    _kp(db, a.id, f1.id, m.id, y.id, "K-1", 90, 5, 5)
    # F2: produced 40, wastage 0, rejection 0 -> efficiency 100%
    _kp(db, a.id, f2.id, m.id, y.id, "K-2", 40, 0, 0)
    db.commit()

    rows = costing.production_by_factory(db, a.id)
    by_fid = {r["factory_id"]: r for r in rows}
    assert by_fid[str(f1.id)]["knitting_produced_kg"] == Decimal("90.000")
    assert by_fid[str(f1.id)]["knitting_efficiency_pct"] == Decimal("90.00")
    assert by_fid[str(f2.id)]["knitting_produced_kg"] == Decimal("40.000")
    assert by_fid[str(f2.id)]["knitting_efficiency_pct"] == Decimal("100.00")


def test_api_by_factory(client, auth_headers, db):
    a = _company(db)
    f1 = Branch(company_id=a.id, code="CFA", name="Cost FA")
    db.add(f1)
    db.flush()
    m = Machine(company_id=a.id, machine_type="KNITTING", code="MK2", name="Knit 2")
    y = YarnMaster(company_id=a.id, code="Y2", name="Cotton 40s")
    db.add_all([m, y])
    db.flush()
    _kp(db, a.id, f1.id, m.id, y.id, "K-A", 100, 0, 0)
    db.commit()
    r = client.get("/api/costing/by-factory", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert any(row["factory_id"] == str(f1.id) and Decimal(row["knitting_produced_kg"]) == Decimal("100")
               for row in r.json())
