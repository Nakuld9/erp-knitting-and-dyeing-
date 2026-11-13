"""Fabric BOM: version control, cost roll-up, and knit→dye→finish production.

Exercises the real workflow end to end against the demo masters (yarn + chemical
opening stock, an APPROVED dyeing recipe):

* build a version-controlled fabric BOM (yarn blend + knitting + dyeing recipe
  link + finishing), approve it and verify the standard cost roll-up;
* create a production order that *stamps* the approved version, exploding the BOM
  into yarn + chemical reservations scaled with wastage / process loss;
* issue (consume) materials from FIFO stock and record actual consumption;
* produce a stage-wise planned-vs-actual variance report;
* prove historical preservation: a new approved version does not change an
  order already created against the previous version.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.fabric_bom import BomStatus, ProductionOrderStatus, ProductionStage
from app.models.manufacturing import KnittingProduction
from app.services.bom import production, service as bom
from app.services.inventory import service as inventory
from app.models.inventory import ItemType


def _build_bom(db, demo, *, code="FB-NAVY-SJ"):
    """A finished Navy single-jersey fabric BOM: one cotton yarn, dye recipe, compacting."""
    b = bom.create_bom(
        db, company_id=demo["company_id"], code=code, name="Navy SJ 160 GSM",
        fabric_type_id=demo["fabric_type_id"], shade_id=demo["shade_id"],
        finished_quality="Export A",
    )
    v = b.versions[0]
    bom.set_spec(db, v.id, construction="Single Jersey", composition="100% Cotton",
                 yarn_count="30s", gsm=Decimal("160"), width_cm=Decimal("180"),
                 gauge=Decimal("24"), diameter_inch=Decimal("34"), stitch_length_mm=Decimal("2.8"),
                 dye_recipe_version_id=demo["recipe_version_id"],
                 std_knitting_cost=Decimal("12"), std_dyeing_cost=Decimal("30"),
                 std_labour_cost=Decimal("8"), std_overhead_cost=Decimal("5"))
    bom.set_yarns(db, v.id, [{
        "yarn_id": demo["yarn_id"], "blend_percent": Decimal("100"), "feeder_allocation": "F1-F48",
        "std_consumption_per_kg": Decimal("1.00"), "wastage_percent": Decimal("2"),
        "process_loss_percent": Decimal("1"), "std_unit_cost": Decimal("240"),
    }])
    bom.set_knitting(db, v.id, machine_type="KNITTING", machine_id=demo["machines"]["knit"],
                     gauge=Decimal("24"), diameter_inch=Decimal("34"), feeders=48,
                     stitch_length_mm=Decimal("2.8"), rpm=28, efficiency_percent=Decimal("85"),
                     expected_output_kg_per_shift=Decimal("320"))
    bom.set_finishing(db, v.id, [
        {"process_type": "COMPACTING", "machine_type": "FINISHING", "target_gsm": Decimal("165"),
         "target_width_cm": Decimal("175"), "std_cost_per_kg": Decimal("6")},
    ])
    return b, v


def test_bom_version_control_and_cost_rollup(db, demo):
    b, v = _build_bom(db, demo)
    assert v.status == BomStatus.DRAFT

    # cost preview before approval
    roll = bom.compute_cost_rollup(db, v)
    # yarn: 1.00 * 1.02 * 1.01 * 240 = 247.248  -> 247.25
    assert roll["std_yarn_cost"] == Decimal("247.25")
    # dye/chem comes from the approved recipe (percent OWF * standard cost); > 0
    assert roll["std_dye_chem_cost"] > 0
    assert roll["std_finishing_cost"] == Decimal("6.00")
    # total = yarn + dye_chem + knit(12) + dye(30) + finish(6) + labour(8) + overhead(5)
    expected = (roll["std_yarn_cost"] + roll["std_dye_chem_cost"] + Decimal("12")
                + Decimal("30") + Decimal("6") + Decimal("8") + Decimal("5"))
    assert roll["std_total_cost_per_kg"] == expected

    av = bom.approve_version(db, version_id=v.id, approved_by=None, effective_from=date(2026, 4, 1))
    assert av.status == BomStatus.APPROVED
    assert av.std_total_cost_per_kg == expected       # snapshot persisted
    assert av.cost_rolled_at is not None

    # a DRAFT edit is now rejected (frozen)
    with pytest.raises(bom.BomError):
        bom.set_spec(db, v.id, gsm=Decimal("170"))


def test_cannot_approve_without_yarn(db, demo):
    b = bom.create_bom(db, company_id=demo["company_id"], code="FB-EMPTY", name="empty")
    with pytest.raises(bom.BomError):
        bom.approve_version(db, version_id=b.versions[0].id, approved_by=None)


def test_production_order_explodes_reservations_and_issues(db, demo):
    b, v = _build_bom(db, demo)
    bom.approve_version(db, version_id=v.id, approved_by=None, effective_from=date(2026, 4, 1))

    order = production.create_order(
        db, company_id=demo["company_id"], bom_version_id=v.id, planned_qty_kg=Decimal("100"),
        number="PO-26001", order_date=date(2026, 6, 1), warehouse_id=None,
    )
    assert order.status == ProductionOrderStatus.PLANNED
    assert str(order.bom_version_id) == str(v.id)

    reqs = production.material_requirements(db, order.id)
    yarn_req = next(r for r in reqs if r["item_type"] == "YARN")
    # 100 * 1.00 * 1.02 * 1.01 = 103.02 kg yarn
    assert yarn_req["planned_qty"] == Decimal("103.020000")
    assert yarn_req["on_hand"] == Decimal("2000.000000")   # from demo opening stock
    assert yarn_req["shortage"] == Decimal("0")
    # dyeing chemicals were exploded from the recipe
    assert any(r["item_type"] == "CHEMICAL" and r["stage"] == ProductionStage.DYEING.value for r in reqs)

    # issue the full yarn requirement from FIFO stock
    yarn_res_id = yarn_req["reservation_id"]
    cons = production.issue_material(db, order_id=order.id, reservation_id=yarn_res_id,
                                     qty=Decimal("103.02"), wastage_qty=Decimal("1.5"))
    assert cons.actual_cost > 0
    # yarn stock drawn down by 103.02
    bal = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.YARN, item_id=demo["yarn_id"])
    assert bal["qty_on_hand"] == Decimal("1896.980000")
    assert order.status == ProductionOrderStatus.IN_PROGRESS

    # issue every chemical reservation
    for r in reqs:
        if r["item_type"] == "CHEMICAL":
            production.issue_material(db, order_id=order.id, reservation_id=r["reservation_id"],
                                      qty=r["planned_qty"])

    production.complete_order(db, order_id=order.id, produced_qty_kg=Decimal("99"))

    var = production.variance_report(db, order.id)
    knit = next(s for s in var["stages"] if s["stage"] == "KNITTING")
    dye = next(s for s in var["stages"] if s["stage"] == "DYEING")
    # knitting actual material ≈ planned (issued the planned qty at the same FIFO cost)
    assert knit["actual_material_cost"] > 0
    assert knit["wastage_qty"] == Decimal("1.500000")
    # dyeing issued materials too
    assert dye["actual_material_cost"] > 0
    # material totals are genuine
    assert var["total_actual_material_cost"] > 0
    assert var["total_planned_material_cost"] > 0
    # conversion is standard; its ACTUAL is unavailable (not relabelled as actual)
    assert var["total_std_conversion_cost"] > 0
    assert var["conversion_actuals_available"] is False
    assert knit["actual_conversion_cost"] is None
    assert knit["conversion_actual_available"] is False
    # a full actual total is NOT fabricated while conversion actuals are missing
    assert var["total_actual_cost"] is None
    assert any("conversion" in n.lower() for n in var["notes"])
    # no actuals linked yet → no actual output quantity invented
    assert knit["actual_output_kg"] is None


def test_variance_reads_genuine_actual_output_not_fabricated(db, demo):
    """When a real KnittingProduction is linked to the order, the variance report
    surfaces its genuine output/wastage; conversion actual stays unavailable."""
    b, v = _build_bom(db, demo, code="FB-ACTUAL")
    bom.approve_version(db, version_id=v.id, approved_by=None, effective_from=date(2026, 4, 1))
    order = production.create_order(
        db, company_id=demo["company_id"], bom_version_id=v.id, planned_qty_kg=Decimal("100"),
        number="PO-ACT-1", order_date=date(2026, 6, 1))
    reqs = production.material_requirements(db, order.id)
    yarn = next(r for r in reqs if r["item_type"] == "YARN")
    production.issue_material(db, order_id=order.id, reservation_id=yarn["reservation_id"],
                             qty=yarn["planned_qty"])
    # record a GENUINE knitting actual linked to the order
    db.add(KnittingProduction(
        company_id=demo["company_id"], number="KN-ACT-1", machine_id=demo["machines"]["knit"],
        yarn_id=demo["yarn_id"], production_date=date(2026, 6, 2), produced_kg=Decimal("98"),
        yarn_consumed_kg=Decimal("101"), wastage_kg=Decimal("2"), rejection_kg=Decimal("1"),
        production_order_id=order.id))
    db.flush()
    # chemical reservations intentionally left outstanding here → override completion
    production.complete_order(db, order_id=order.id, produced_qty_kg=Decimal("98"),
                              allow_outstanding=True)

    var = production.variance_report(db, order.id)
    knit = next(s for s in var["stages"] if s["stage"] == "KNITTING")
    # genuine actual output came from the linked KnittingProduction
    assert knit["actual_output_kg"] == Decimal("98.000000")
    assert knit["actual_material_cost_per_kg"] is not None   # actual_material / actual_output
    # conversion actual is still explicitly unavailable (never substituted by std)
    assert knit["actual_conversion_cost"] is None
    assert var["conversion_actuals_available"] is False
    assert var["total_actual_cost"] is None


def test_historical_version_preserved_on_revision(db, demo):
    b, v1 = _build_bom(db, demo, code="FB-HIST")
    bom.approve_version(db, version_id=v1.id, approved_by=None, effective_from=date(2026, 4, 1))

    order = production.create_order(
        db, company_id=demo["company_id"], bom_id=b.id, planned_qty_kg=Decimal("50"),
        number="PO-HIST-1", order_date=date(2026, 5, 1),
    )
    assert str(order.bom_version_id) == str(v1.id)

    # revise: new version with a higher yarn standard cost, approved later
    v2 = bom.add_version(db, bom_id=b.id, clone_from_version_id=v1.id, revision_notes="price rise")
    bom.set_yarns(db, v2.id, [{
        "yarn_id": demo["yarn_id"], "blend_percent": Decimal("100"),
        "std_consumption_per_kg": Decimal("1.00"), "wastage_percent": Decimal("2"),
        "process_loss_percent": Decimal("1"), "std_unit_cost": Decimal("300"),
    }])
    bom.approve_version(db, version_id=v2.id, approved_by=None, effective_from=date(2026, 7, 1))

    db.refresh(v1)
    db.refresh(order)
    # v1 is superseded but the historical order still points at v1
    assert v1.status == BomStatus.SUPERSEDED
    assert v1.effective_to == date(2026, 6, 30)
    assert str(order.bom_version_id) == str(v1.id)

    # the effective version on a later date is v2; an earlier date still resolves v1
    assert str(bom.effective_version(db, b.id, date(2026, 8, 1)).id) == str(v2.id)
    assert str(bom.effective_version(db, b.id, date(2026, 5, 15)).id) == str(v1.id)

    # a new order dated after the revision uses v2
    order2 = production.create_order(
        db, company_id=demo["company_id"], bom_id=b.id, planned_qty_kg=Decimal("50"),
        number="PO-HIST-2", order_date=date(2026, 8, 1),
    )
    assert str(order2.bom_version_id) == str(v2.id)
