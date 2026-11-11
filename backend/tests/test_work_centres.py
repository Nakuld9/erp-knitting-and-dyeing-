"""Machines as work centres: cost-centre-tagged production postings.

Issuing yarn to a machine and producing greige stamps the machine's work-centre
cost centre on the WIP/greige lines, so cost accumulates per machine in the
ledger and rolls up to the department (Knitting).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models.masters import CostCentre, CostCentreType, Machine
from app.models.manufacturing import KnittingProduction
from app.services import work_centres
from app.services.accounting.cost_centres import cost_centre_report
from app.services.manufacturing.knitting import record_production


def test_department_cost_centres_seeded(db, company):
    codes = {c.code for c in db.execute(select(CostCentre).where(
        CostCentre.company_id == company.id)).scalars().all()}
    assert {"KNIT", "DYE", "FINISH", "CHEMSTORE", "UTIL", "MAINT", "ADMIN", "PACK"} <= codes


def test_ensure_work_centre_links_machine_under_department(db, demo):
    machine = db.get(Machine, demo["machines"]["knit"])
    assert machine.cost_centre_id is None
    wc = work_centres.ensure_work_centre(db, demo["company_id"], machine)
    db.commit()
    assert wc.code == "WC-K-01" and wc.centre_type == CostCentreType.WORK
    # Parented under the Knitting department.
    dept = db.get(CostCentre, wc.parent_id)
    assert dept is not None and dept.code == "KNIT"
    db.refresh(machine)
    assert machine.cost_centre_id == wc.id


def _produce(db, demo):
    kp = KnittingProduction(company_id=demo["company_id"], number="KP-WC", machine_id=demo["machines"]["knit"],
                            yarn_id=demo["yarn_id"], yarn_lot_id=demo["yarn_lot_id"],
                            fabric_type_id=demo["fabric_type_id"], production_date=date(2026, 11, 8),
                            produced_kg=Decimal("250"), yarn_consumed_kg=Decimal("255"))
    db.add(kp); db.flush()
    record_production(db, kp, [{"weight_kg": "250", "warehouse_id": demo["warehouses"]["greige"]}])
    db.commit()


def test_production_posts_tag_the_work_centre(db, demo):
    _produce(db, demo)
    machine = db.get(Machine, demo["machines"]["knit"])
    assert machine.cost_centre_id is not None             # linked on first production

    rep = cost_centre_report(db, demo["company_id"], date(2026, 4, 1), date(2027, 3, 31))
    wc_rows = [c for c in rep["centres"] if c["code"] == "WC-K-01"]
    assert len(wc_rows) == 1 and wc_rows[0]["type"] == "WORK"
    # yarn_cost = 255 @ 240 = 61,200 accumulates on the machine's work centre.
    assert wc_rows[0]["net_cost"] == Decimal("61200.00")
    # Rolled up to the Knitting department.
    knit_dept = [d for d in rep["departments"] if d["code"] == "KNIT"]
    assert len(knit_dept) == 1 and knit_dept[0]["net_cost"] == Decimal("61200.00")


def test_report_empty_when_no_cost_centre_activity(db, company):
    rep = cost_centre_report(db, company.id, date(2026, 4, 1), date(2027, 3, 31))
    assert rep["centres"] == [] and rep["total_net_cost"] == Decimal("0.00")
