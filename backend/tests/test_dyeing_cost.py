"""Grey fabric actual cost → dyeing → actual dyed fabric cost (chemical-free).

Covers: FIFO yarn actual costing, independent actual knitting conversion cost,
grey fabric valuation into inventory, grey std-vs-actual variance, grey→dyeing
issue at genuine FIFO cost, dyeing input/output/loss, actual dyed cost/kg with
different saleable outputs, reprocessing/additional costs, incomplete-cost
warnings, and grey→yarn-lot traceability.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.inventory import ItemType
from app.models.dyeing_cost import DyeingStatus
from app.services.bom import production, service as bom
from app.services.dyeing import service as dyeing
from app.services.inventory import service as inventory


def _grey_order(db, demo, *, code, qty="100", conversion="500"):
    """An approved yarn-only grey BOM + a completed, valued grey production order."""
    b = bom.create_bom(db, company_id=demo["company_id"], code=code, name=code)
    v = b.versions[0]
    bom.set_spec(db, v.id, std_knitting_cost=Decimal("12"))
    bom.set_yarns(db, v.id, [{"yarn_id": demo["yarn_id"], "blend_percent": Decimal("100"),
                              "std_consumption_per_kg": Decimal("1.0"), "wastage_percent": Decimal("2"),
                              "std_unit_cost": Decimal("240")}])
    bom.approve_version(db, version_id=v.id, approved_by=None, effective_from=date(2026, 4, 1))
    order = production.create_order(db, company_id=demo["company_id"], bom_version_id=v.id,
                                    planned_qty_kg=Decimal(qty), number="PO-"+code, order_date=date(2026, 6, 1))
    res = order.reservations[0]
    production.issue_material(db, order_id=order.id, reservation_id=res.id, qty=res.planned_qty)
    production.complete_order(db, order_id=order.id, produced_qty_kg=Decimal(qty))
    if conversion is not None:
        production.record_actual_knitting_conversion_cost(db, order_id=order.id, amount=Decimal(conversion))
    production.value_grey_output(db, order_id=order.id)
    return order, v


# --- grey fabric actual cost ------------------------------------------------

def test_grey_actual_cost_fifo_and_independent_conversion(db, demo):
    order, v = _grey_order(db, demo, code="GR1", qty="100", conversion="600")
    rep = production.grey_actual_cost(db, order.id)
    # yarn: 100 kg * 1.0 * 1.02 wastage = 102 kg @ 240 = 24480 (FIFO genuine)
    assert rep["actual_yarn_cost"] == Decimal("24480.00")
    # conversion is the independently-entered actual (600), NOT the standard (12*100)
    assert rep["actual_knitting_conversion_cost"] == Decimal("600.00")
    assert rep["actual_grey_total_cost"] == Decimal("25080.00")
    assert rep["actual_grey_cost_per_kg"] == Decimal("250.80")   # 25080 / 100
    # standard grey = std_yarn(247.248→? snapshot) + std_knitting(12)
    assert rep["std_grey_cost_per_kg"] > 0
    assert rep["cost_complete"] is True
    assert rep["missing_cost_components"] == []
    # grey fabric was valued into FABRIC inventory at the actual cost
    bal = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC, item_id=order.id)
    assert bal["qty_on_hand"] == Decimal("100.000000")
    assert bal["value"] == Decimal("25080.00")


def test_grey_cost_incomplete_without_conversion(db, demo):
    order, v = _grey_order(db, demo, code="GR2", qty="50", conversion=None)
    rep = production.grey_actual_cost(db, order.id)
    assert rep["actual_knitting_conversion_cost"] is None
    assert "actual_knitting_conversion_cost" in rep["missing_cost_components"]
    assert rep["cost_complete"] is False
    # actual is yarn-only (conversion not substituted by standard)
    assert rep["actual_grey_total_cost"] == rep["actual_yarn_cost"]


def test_grey_output_valuation_is_idempotent(db, demo):
    order, v = _grey_order(db, demo, code="GR3", qty="30")
    with pytest.raises(production.ProductionError):
        production.value_grey_output(db, order_id=order.id)   # already valued


# --- dyeing + actual dyed cost ----------------------------------------------

def test_dyeing_full_flow_actual_dyed_cost(db, demo):
    order, v = _grey_order(db, demo, code="GR4", qty="100", conversion="600")
    job = dyeing.create_job(db, company_id=demo["company_id"], number="DJ-1",
                            mode="INTERNAL", grey_production_order_id=order.id, shade_id=demo["shade_id"])
    # issue 100 kg grey at genuine FIFO cost (= 25080)
    inp = dyeing.issue_grey(db, job_id=job.id, source_production_order_id=order.id, qty_kg=Decimal("100"))
    assert inp.cost == Decimal("25080.00")
    # record internal dyeing charge + chemical cost (manual) + finishing
    dyeing.record_costs(db, job_id=job.id, dyeing_charge=Decimal("3000"),
                        chemical_cost=Decimal("1500"), finishing_cost=Decimal("500"))
    # 95 kg saleable out (5 kg process loss)
    dyeing.record_output(db, job_id=job.id, lots=[{"lot_code": "DL-1", "qty_kg": Decimal("95")}])
    dyeing.complete_job(db, job_id=job.id)

    cost = dyeing.dyed_actual_cost(db, job.id)
    # total = grey 25080 + dyeing 3000 + chemical 1500 + finishing 500 = 30080
    assert cost["total_actual_cost"] == Decimal("30080.00")
    assert cost["saleable_output_kg"] == Decimal("95.000000")
    assert cost["actual_dyed_cost_per_kg"] == Decimal("316.63")   # 30080/95
    assert cost["loss_pct"] == Decimal("5.00")
    # all required components recorded → complete
    assert cost["cost_complete"] is True
    assert cost["missing_cost_components"] == []
    # dyed fabric valued into inventory; the perpetual value matches the sub-ledger
    # within per-kg rounding dust (authoritative actual cost is total_actual_cost).
    val = db.get(type(job), job.id).dyed_inventory_value
    assert abs(val - Decimal("30080.00")) <= Decimal("95") * Decimal("0.005")


def test_dyed_cost_incomplete_when_chemical_missing(db, demo):
    order, v = _grey_order(db, demo, code="GR5", qty="100", conversion="600")
    job = dyeing.create_job(db, company_id=demo["company_id"], number="DJ-2", mode="INTERNAL",
                            grey_production_order_id=order.id)
    dyeing.issue_grey(db, job_id=job.id, source_production_order_id=order.id, qty_kg=Decimal("100"))
    dyeing.record_costs(db, job_id=job.id, dyeing_charge=Decimal("3000"))   # no chemical cost
    dyeing.record_output(db, job_id=job.id, lots=[{"lot_code": "DL-1", "qty_kg": Decimal("96")}])
    dyeing.complete_job(db, job_id=job.id)
    cost = dyeing.dyed_actual_cost(db, job.id)
    assert cost["cost_complete"] is False
    assert "chemical_cost" in cost["missing_cost_components"]
    assert cost["note"] is not None and "INCOMPLETE" in cost["note"]


def test_dyed_cost_different_saleable_outputs_allocation(db, demo):
    order, v = _grey_order(db, demo, code="GR6", qty="100", conversion="600")
    job = dyeing.create_job(db, company_id=demo["company_id"], number="DJ-3", mode="INTERNAL",
                            grey_production_order_id=order.id)
    dyeing.issue_grey(db, job_id=job.id, source_production_order_id=order.id, qty_kg=Decimal("100"))
    dyeing.record_costs(db, job_id=job.id, dyeing_charge=Decimal("2000"), chemical_cost=Decimal("1000"))
    # two output lots of different sizes → cost allocated by weight
    dyeing.record_output(db, job_id=job.id, lots=[{"lot_code": "A", "qty_kg": Decimal("60")},
                                                  {"lot_code": "B", "qty_kg": Decimal("30")}])
    dyeing.complete_job(db, job_id=job.id, rejection_kg=Decimal("5"))
    cost = dyeing.dyed_actual_cost(db, job.id)
    # total 25080+2000+1000 = 28080 over 90 kg → 312.00/kg
    assert cost["saleable_output_kg"] == Decimal("90.000000")
    assert cost["actual_dyed_cost_per_kg"] == Decimal("312.00")
    lots = {l["lot_code"]: l for l in cost["output_lots"]}
    # allocation by weight; both lots at the same per-kg
    assert lots["A"]["allocated_cost"] + lots["B"]["allocated_cost"] == Decimal("28080.00")
    assert lots["A"]["cost_per_kg"] == Decimal("312.00")
    # input 100, output 90, rejection 5 → loss 5
    assert cost["process_loss_kg"] == Decimal("5.000000")


def test_jobwork_mode_requires_charge_for_completeness(db, demo):
    order, v = _grey_order(db, demo, code="GR7", qty="40", conversion="200")
    job = dyeing.create_job(db, company_id=demo["company_id"], number="DJ-4", mode="JOBWORK",
                            grey_production_order_id=order.id, jobwork_supplier_id=demo["supplier_id"])
    dyeing.issue_grey(db, job_id=job.id, source_production_order_id=order.id, qty_kg=Decimal("40"))
    dyeing.record_costs(db, job_id=job.id, chemical_cost=Decimal("400"))   # jobwork_charge missing
    dyeing.record_output(db, job_id=job.id, lots=[{"lot_code": "J1", "qty_kg": Decimal("38")}])
    dyeing.complete_job(db, job_id=job.id)
    cost = dyeing.dyed_actual_cost(db, job.id)
    assert "jobwork_charge" in cost["missing_cost_components"]
    assert cost["cost_complete"] is False


def test_reprocess_and_additional_costs(db, demo):
    order, v = _grey_order(db, demo, code="GR8", qty="100", conversion="600")
    job = dyeing.create_job(db, company_id=demo["company_id"], number="DJ-5", mode="INTERNAL",
                            grey_production_order_id=order.id)
    dyeing.issue_grey(db, job_id=job.id, source_production_order_id=order.id, qty_kg=Decimal("100"))
    dyeing.record_costs(db, job_id=job.id, dyeing_charge=Decimal("3000"), chemical_cost=Decimal("1500"),
                        reprocess_cost=Decimal("800"), testing_cost=Decimal("200"), other_cost=Decimal("100"))
    dyeing.record_output(db, job_id=job.id, lots=[{"lot_code": "R1", "qty_kg": Decimal("94")}])
    dyeing.complete_job(db, job_id=job.id, reprocess_qty_kg=Decimal("6"))
    cost = dyeing.dyed_actual_cost(db, job.id)
    # 25080 + 3000 + 1500 + 800 + 200 + 100 = 30680
    assert cost["total_actual_cost"] == Decimal("30680.00")
    assert cost["reprocess_qty_kg"] == Decimal("6.000000")
    assert cost["cost_complete"] is True


def test_grey_to_yarn_lot_traceability(db, demo):
    order, v = _grey_order(db, demo, code="GR9", qty="50", conversion="300")
    job = dyeing.create_job(db, company_id=demo["company_id"], number="DJ-6", mode="INTERNAL",
                            grey_production_order_id=order.id)
    dyeing.issue_grey(db, job_id=job.id, source_production_order_id=order.id, qty_kg=Decimal("50"))
    tr = dyeing.trace(db, job.id)
    assert len(tr["grey_sources"]) == 1
    g = tr["grey_sources"][0]
    assert g["production_order_id"] == str(order.id)
    assert g["grey_issued_kg"] == Decimal("50.000000")
    # drill-down reaches the yarn issues of the grey order
    assert len(g["yarn_issues"]) >= 1
    assert g["yarn_issues"][0]["cost"] > 0


def test_cannot_issue_grey_before_valuation(db, demo):
    # a grey order that is completed but NOT valued cannot be issued to dyeing
    b = bom.create_bom(db, company_id=demo["company_id"], code="GR10", name="x")
    v = b.versions[0]
    bom.set_yarns(db, v.id, [{"yarn_id": demo["yarn_id"], "blend_percent": Decimal("100"),
                              "std_consumption_per_kg": Decimal("1.0"), "std_unit_cost": Decimal("240")}])
    bom.approve_version(db, version_id=v.id, approved_by=None)
    order = production.create_order(db, company_id=demo["company_id"], bom_version_id=v.id,
                                    planned_qty_kg=Decimal("10"), number="PO-GR10")
    res = order.reservations[0]
    production.issue_material(db, order_id=order.id, reservation_id=res.id, qty=res.planned_qty)
    production.complete_order(db, order_id=order.id, produced_qty_kg=Decimal("10"))
    job = dyeing.create_job(db, company_id=demo["company_id"], number="DJ-7", mode="INTERNAL",
                            grey_production_order_id=order.id)
    with pytest.raises(dyeing.DyeingError):
        dyeing.issue_grey(db, job_id=job.id, source_production_order_id=order.id, qty_kg=Decimal("10"))
