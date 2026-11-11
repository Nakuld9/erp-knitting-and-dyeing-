"""Overhead allocation: absorb an overhead pool into the work centres."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models.manufacturing import KnittingProduction
from app.models.masters import CostCentre
from app.models.overhead import AllocationBasis, OverheadPool
from app.services import overhead as oh
from app.services.accounting.cost_centres import cost_centre_report
from app.services.manufacturing.knitting import record_production
from tests.helpers import L, post


def _pool(db, demo, *, code="OH-ELEC", source="5070", basis=AllocationBasis.OUTPUT_KG):
    p = OverheadPool(company_id=demo["company_id"], code=code, name="Electricity",
                     source_account_code=source, basis=basis)
    db.add(p); db.flush()
    return p


def _produce(db, demo, weight="250"):
    import uuid as _u
    kp = KnittingProduction(company_id=demo["company_id"], number=f"KP-OH-{_u.uuid4().hex[:6]}",
                            machine_id=demo["machines"]["knit"], yarn_id=demo["yarn_id"],
                            yarn_lot_id=demo["yarn_lot_id"], fabric_type_id=demo["fabric_type_id"],
                            production_date=date(2026, 11, 8), produced_kg=Decimal(weight),
                            yarn_consumed_kg=Decimal("255"))
    db.add(kp); db.flush()
    record_production(db, kp, [{"weight_kg": weight, "warehouse_id": demo["warehouses"]["greige"]}])
    db.commit()


def test_allocation_posts_balanced_and_loads_work_centre(db, demo):
    _produce(db, demo, "250")
    pool = _pool(db, demo)
    res = oh.allocate_overhead(db, company_id=demo["company_id"], pool=pool,
                               period_month=date(2026, 11, 30), amount=Decimal("10000"))
    db.commit()
    v = res["voucher"]
    assert v.total_debit == v.total_credit == Decimal("10000.00")       # balanced
    assert res["machines_allocated"] == 1                               # only K-01 had output

    # The work centre's loaded cost now includes the overhead (greige 61,200 + 10,000).
    rep = cost_centre_report(db, demo["company_id"], date(2026, 4, 1), date(2027, 3, 31))
    wc = [c for c in rep["centres"] if c["code"] == "WC-K-01"][0]
    assert wc["net_cost"] == Decimal("71200.00")


def test_allocation_is_idempotent(db, demo):
    _produce(db, demo, "250")
    pool = _pool(db, demo)
    r1 = oh.allocate_overhead(db, company_id=demo["company_id"], pool=pool,
                              period_month=date(2026, 11, 5), amount=Decimal("5000")); db.commit()
    r2 = oh.allocate_overhead(db, company_id=demo["company_id"], pool=pool,
                              period_month=date(2026, 11, 20), amount=Decimal("5000")); db.commit()
    assert r2["already_posted"] is True and r1["voucher"].id == r2["voucher"].id


def test_allocation_absorbs_source_account_balance(db, demo):
    _produce(db, demo, "250")
    # Book an electricity bill: Dr Electricity 5070 / Cr Bank-ish (use 2080 payable).
    post(db, demo["company_id"], [L("5070", debit="8000"), L("2080", credit="8000")], ref="ELEC-BILL")
    pool = _pool(db, demo)
    res = oh.allocate_overhead(db, company_id=demo["company_id"], pool=pool,
                               period_month=date(2026, 11, 30))   # no amount -> pull from 5070
    db.commit()
    assert res["total"] == Decimal("8000.00")
    # 5070 is now fully absorbed (8000 debit - 8000 credit = 0).
    v = res["voucher"]
    assert v.total_debit == v.total_credit == Decimal("8000.00")


def test_equal_basis_splits_across_machines(db, demo):
    pool = _pool(db, demo, code="OH-EQ", source="6040", basis=AllocationBasis.EQUAL)
    res = oh.allocate_overhead(db, company_id=demo["company_id"], pool=pool,
                               period_month=date(2026, 11, 30), amount=Decimal("1000"))
    db.commit()
    # demo has 2 active machines (K-01, JET-04) -> 500 each.
    assert res["machines_allocated"] == 2
    amounts = sorted(Decimal(str(li["amount"])) for li in res["lines"])
    assert amounts == [Decimal("500.00"), Decimal("500.00")]


def test_output_basis_with_no_production_errors(db, demo):
    pool = _pool(db, demo)   # OUTPUT_KG, but nothing produced
    try:
        oh.allocate_overhead(db, company_id=demo["company_id"], pool=pool,
                             period_month=date(2026, 11, 30), amount=Decimal("1000"))
        assert False, "expected OverheadError"
    except oh.OverheadError:
        db.rollback()
