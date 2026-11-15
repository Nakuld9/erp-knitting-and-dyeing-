"""Stage 3B — physical warehouse-stage enforcement.

A new manufacturing receipt may only land in a warehouse of the correct stage
category (when the chosen warehouse is categorised): knitting -> GREIGE, dyeing
-> SEMI_FINISHED, finishing -> FINISHED_GOODS. Wrong *categorised* warehouses are
rejected; un-categorised/legacy warehouses still work. Roll location stays
authoritative and in sync through finishing and factory transfer.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.core import Branch, Company
from app.models.inventory import ItemType, StockLayer
from app.models.manufacturing import FabricRoll, KnittingProduction, RollKind
from app.models.masters import Warehouse
from app.models.transfers import FactoryStockTransfer, TransferStatus
from app.services import factory_transfer, quality
from app.services import hybrid
from app.services.bom import production, service as bom
from app.services.dyeing import service as dyeing
from app.services.hybrid import HybridConfigError
from app.services.inventory import service as inventory
from app.services.manufacturing.knitting import record_production

WHEN = date(2026, 11, 7)


def _wh(db, demo, code, category, branch_id=None):
    w = Warehouse(company_id=demo["company_id"], branch_id=branch_id, code=code, name=code,
                  kind="STORE", category=category)
    db.add(w); db.flush()
    return w


def _kp(db, demo, number, warehouse_id):
    kp = KnittingProduction(company_id=demo["company_id"], number=number, machine_id=demo["machines"]["knit"],
                            yarn_id=demo["yarn_id"], yarn_lot_id=demo["yarn_lot_id"],
                            fabric_type_id=demo["fabric_type_id"], production_date=WHEN,
                            produced_kg=Decimal("250"), yarn_consumed_kg=Decimal("255"))
    db.add(kp); db.flush()
    return record_production(db, kp, [{"weight_kg": "250", "warehouse_id": warehouse_id}])["rolls"][0]


def _grey_order(db, demo, *, code, qty="100"):
    b = bom.create_bom(db, company_id=demo["company_id"], code=code, name=code)
    v = b.versions[0]
    bom.set_spec(db, v.id, std_knitting_cost=Decimal("12"))
    bom.set_yarns(db, v.id, [{"yarn_id": demo["yarn_id"], "blend_percent": Decimal("100"),
                              "std_consumption_per_kg": Decimal("1.0"), "wastage_percent": Decimal("2"),
                              "std_unit_cost": Decimal("240")}])
    bom.approve_version(db, version_id=v.id, approved_by=None, effective_from=date(2026, 4, 1))
    order = production.create_order(db, company_id=demo["company_id"], bom_version_id=v.id,
                                    planned_qty_kg=Decimal(qty), number="PO-" + code, order_date=date(2026, 6, 1))
    res = order.reservations[0]
    production.issue_material(db, order_id=order.id, reservation_id=res.id, qty=res.planned_qty)
    production.complete_order(db, order_id=order.id, produced_qty_kg=Decimal(qty))
    production.record_actual_knitting_conversion_cost(db, order_id=order.id, amount=Decimal("500"))
    production.value_grey_output(db, order_id=order.id)
    return order


def _dye_to(db, demo, *, code, warehouse_id):
    order = _grey_order(db, demo, code=code)
    job = dyeing.create_job(db, company_id=demo["company_id"], number="DJ-" + code, mode="INTERNAL",
                            grey_production_order_id=order.id)
    dyeing.issue_grey(db, job_id=job.id, source_production_order_id=order.id, qty_kg=order.produced_qty_kg)
    dyeing.record_costs(db, job_id=job.id, dyeing_charge=Decimal("1000"))
    dyeing.record_output(db, job_id=job.id, lots=[{"lot_code": "L1", "qty_kg": Decimal("95")}])
    return dyeing.complete_job(db, job_id=job.id, warehouse_id=warehouse_id)


# ---- knitting -> GREIGE ----

def test_knitting_into_greige_ok(db, demo):
    roll = _kp(db, demo, "KP-G-OK", demo["warehouses"]["greige"])   # WH-GREIGE category GREIGE
    assert roll.warehouse_id == demo["warehouses"]["greige"]


def test_knitting_into_fg_rejected(db, demo):
    with pytest.raises(HybridConfigError):
        _kp(db, demo, "KP-G-BAD", demo["warehouses"]["fg"])         # FINISHED_GOODS != GREIGE


def test_knitting_into_uncategorised_allowed(db, demo):
    w = _wh(db, demo, "WH-LEGACY", None)                            # legacy, no category
    roll = _kp(db, demo, "KP-G-LEG", w.id)
    assert roll.warehouse_id == w.id


# ---- dyeing -> SEMI_FINISHED ----

def test_dyeing_into_semi_finished_ok(db, demo):
    semi = _wh(db, demo, "WH-SEMI", "SEMI_FINISHED")
    job = _dye_to(db, demo, code="DSEMI", warehouse_id=semi.id)
    from app.models.dyeing_cost import DyeingOutput
    out = db.execute(select(DyeingOutput).where(DyeingOutput.job_id == job.id)).scalar_one()
    roll = db.get(FabricRoll, out.dyed_item_id)
    assert roll.kind in (RollKind.DYED, RollKind.DYED.value)
    assert roll.warehouse_id == semi.id


def test_dyeing_into_finished_goods_rejected(db, demo):
    with pytest.raises(dyeing.DyeingError):
        _dye_to(db, demo, code="DFG", warehouse_id=demo["warehouses"]["fg"])  # FG != SEMI_FINISHED


# ---- finishing -> FINISHED_GOODS ----

def _greige_roll_with_stock(db, demo, number):
    # a greige roll received into the GREIGE warehouse (has FABRIC stock + value)
    return _kp(db, demo, number, demo["warehouses"]["greige"])


def test_finishing_into_fg_moves_roll(db, demo):
    roll = _greige_roll_with_stock(db, demo, "KP-FIN")
    fg = demo["warehouses"]["fg"]
    before = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC, item_id=roll.id)["qty_on_hand"]
    quality.record_finishing(db, demo["company_id"], process_type="COMPACTING", process_date=WHEN,
                             fabric_roll_id=roll.id, input_weight_kg=Decimal("250"),
                             output_weight_kg=Decimal("248"), fg_warehouse_id=fg)
    db.flush()
    db.refresh(roll)
    assert roll.kind in (RollKind.FINISHED, RollKind.FINISHED.value)
    assert roll.warehouse_id == fg
    # stock physically sits in FG now; total qty unchanged (no duplicate stock)
    fg_layers = db.execute(select(StockLayer).where(
        StockLayer.company_id == demo["company_id"], StockLayer.item_id == roll.id,
        StockLayer.warehouse_id == fg, StockLayer.qty_remaining > 0)).scalars().all()
    assert fg_layers
    after = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC, item_id=roll.id)["qty_on_hand"]
    assert after == before     # net-zero move, no duplication


def test_finishing_into_wrong_category_rejected(db, demo):
    roll = _greige_roll_with_stock(db, demo, "KP-FIN2")
    with pytest.raises(HybridConfigError):
        quality.record_finishing(db, demo["company_id"], process_type="COMPACTING", process_date=WHEN,
                                 fabric_roll_id=roll.id, fg_warehouse_id=demo["warehouses"]["greige"])


def test_finishing_rejected_roll_blocked_from_fg(db, demo):
    roll = _greige_roll_with_stock(db, demo, "KP-FIN3")
    roll.quality_status = "REJECT"
    db.flush()
    with pytest.raises(ValueError):
        quality.record_finishing(db, demo["company_id"], process_type="COMPACTING", process_date=WHEN,
                                 fabric_roll_id=roll.id, fg_warehouse_id=demo["warehouses"]["fg"])
    db.refresh(roll)
    assert roll.warehouse_id != demo["warehouses"]["fg"]   # not moved into FG


def test_finishing_without_fg_warehouse_is_legacy(db, demo):
    # no fg_warehouse_id -> unchanged legacy behaviour (no move, still finishes)
    roll = _greige_roll_with_stock(db, demo, "KP-FIN4")
    quality.record_finishing(db, demo["company_id"], process_type="COMPACTING", process_date=WHEN,
                             fabric_roll_id=roll.id)
    db.refresh(roll)
    assert roll.kind in (RollKind.FINISHED, RollKind.FINISHED.value)
    assert roll.warehouse_id == demo["warehouses"]["greige"]   # not moved


# ---- roll location sync on factory transfer ----

def test_factory_transfer_syncs_roll_location(db, demo):
    f1 = Branch(company_id=demo["company_id"], code="WF1", name="WF1")
    f2 = Branch(company_id=demo["company_id"], code="WF2", name="WF2")
    db.add_all([f1, f2]); db.flush()
    w1 = _wh(db, demo, "WSRC", "GREIGE", branch_id=f1.id)
    w2 = _wh(db, demo, "WDST", "GREIGE", branch_id=f2.id)
    roll = _kp(db, demo, "KP-XFER", w1.id)
    assert roll.warehouse_id == w1.id
    wt = Decimal(str(roll.weight_kg))
    t = factory_transfer.create_transfer_request(
        db, company_id=demo["company_id"], item_type=ItemType.FABRIC, item_id=roll.id,
        from_warehouse_id=w1.id, to_warehouse_id=w2.id, qty=wt, requested_by=uuid.uuid4())
    checker = uuid.uuid4()
    factory_transfer.authorize_transfer(db, t.id, demo["company_id"], checker)
    factory_transfer.dispatch_transfer(db, t.id, demo["company_id"], checker)
    factory_transfer.receive_transfer(db, t.id, demo["company_id"], checker)
    db.refresh(roll)
    assert roll.warehouse_id == w2.id   # header followed the stock -> no divergence


# ---- cross-company / cross-factory isolation ----

def test_cross_company_warehouse_rejected(db, demo):
    other = db.execute(select(Company).where(Company.code == "UATB")).scalar_one_or_none()
    if other is None:
        other = Company(code="UATB", name="UAT Weaves", legal_name="UAT Weaves Pvt Ltd",
                        gstin="27ABCDE9999F1Z2", state_code="27", currency="INR")
        db.add(other); db.flush()
    foreign_wh = Warehouse(company_id=other.id, code="WH-FOREIGN", name="Foreign", kind="STORE", category="GREIGE")
    db.add(foreign_wh); db.flush()
    with pytest.raises(HybridConfigError):   # belongs to another company
        hybrid.require_warehouse_stage(db, demo["company_id"], foreign_wh.id,
                                       hybrid.WarehouseCategory.GREIGE)


def test_cross_factory_warehouse_rejected(db, demo):
    f1 = Branch(company_id=demo["company_id"], code="XF1", name="XF1")
    f2 = Branch(company_id=demo["company_id"], code="XF2", name="XF2")
    db.add_all([f1, f2]); db.flush()
    w = _wh(db, demo, "WH-F1", "GREIGE", branch_id=f1.id)
    with pytest.raises(HybridConfigError):   # warehouse in f1, transaction in f2
        hybrid.require_warehouse_stage(db, demo["company_id"], w.id,
                                       hybrid.WarehouseCategory.GREIGE, factory_id=f2.id)
