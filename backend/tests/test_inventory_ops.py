"""Stock transfers, FEFO/weighted-average valuation, and roll split/merge."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.inventory import ItemType, StockLayer
from app.models.manufacturing import KnittingProduction, RollKind
from app.services import inventory
from app.services.manufacturing import roll_ops
from app.services.manufacturing.knitting import record_production


# --------------------------- valuation methods ------------------------------

def _two_chem_layers(db, demo):
    """Add a second priced layer so FIFO/FEFO/WAVG differ."""
    inventory.receive(db, company_id=demo["company_id"], item_type=ItemType.CHEMICAL,
                      item_id=demo["chemicals"]["RB-01"], qty="100", unit_cost="1500",
                      lot_id=demo["lots"]["RB-01"], warehouse_id=demo["warehouses"]["chem"],
                      expiry_date=date(2027, 1, 1), event_type="GRN")
    db.commit()


def test_weighted_average_cost(db, demo):
    # opening 500 @ 1200 = 600000; add 100 @ 1500 = 150000 -> 600/700... avg
    _two_chem_layers(db, demo)
    w = inventory.weighted_average_cost(db, company_id=demo["company_id"],
                                        item_type=ItemType.CHEMICAL, item_id=demo["chemicals"]["RB-01"])
    # (500*1200 + 100*1500) / 600 = 750000/600 = 1250.00
    assert w["qty_on_hand"] == Decimal("600.000000")
    assert w["avg_cost"] == Decimal("1250.00")


def test_fefo_draws_earliest_expiry_first(db, demo):
    # opening layer has no expiry; add a layer expiring soon -> FEFO takes it first.
    inventory.receive(db, company_id=demo["company_id"], item_type=ItemType.CHEMICAL,
                      item_id=demo["chemicals"]["RB-01"], qty="50", unit_cost="2000",
                      lot_id=demo["lots"]["RB-01"], warehouse_id=demo["warehouses"]["chem"],
                      expiry_date=date(2026, 12, 1), event_type="GRN")
    db.commit()
    res = inventory.consume(db, company_id=demo["company_id"], item_type=ItemType.CHEMICAL,
                            item_id=demo["chemicals"]["RB-01"], qty="50", method="FEFO")
    db.commit()
    # The expiring layer (50 @ 2000) is taken first -> cost 100000.
    assert res["total_cost"] == Decimal("100000.00")


def test_fifo_draws_oldest_first(db, demo):
    inventory.receive(db, company_id=demo["company_id"], item_type=ItemType.CHEMICAL,
                      item_id=demo["chemicals"]["RB-01"], qty="50", unit_cost="2000",
                      lot_id=demo["lots"]["RB-01"], warehouse_id=demo["warehouses"]["chem"],
                      expiry_date=date(2026, 12, 1), event_type="GRN")
    db.commit()
    res = inventory.consume(db, company_id=demo["company_id"], item_type=ItemType.CHEMICAL,
                            item_id=demo["chemicals"]["RB-01"], qty="50", method="FIFO")
    db.commit()
    # FIFO takes the opening layer (50 @ 1200) -> cost 60000.
    assert res["total_cost"] == Decimal("60000.00")


# ----------------------------- stock transfers ------------------------------

def test_stock_transfer_moves_between_warehouses(db, demo):
    chem = demo["chemicals"]["RB-01"]; src = demo["warehouses"]["chem"]; dst = demo["warehouses"]["greige"]
    before_src = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.CHEMICAL,
                                   item_id=chem, warehouse_id=src)["qty_on_hand"]
    res = inventory.transfer_stock(db, company_id=demo["company_id"], item_type=ItemType.CHEMICAL,
                                   item_id=chem, from_warehouse_id=src, to_warehouse_id=dst, qty="100")
    db.commit()
    after_src = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.CHEMICAL,
                                  item_id=chem, warehouse_id=src)["qty_on_hand"]
    dst_bal = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.CHEMICAL,
                                item_id=chem, warehouse_id=dst)["qty_on_hand"]
    assert before_src - after_src == Decimal("100.000000")
    assert dst_bal == Decimal("100.000000")
    assert res["cost_value"] == Decimal("120000.00")               # 100 @ 1200


def test_transfer_same_warehouse_rejected(db, demo):
    chem = demo["chemicals"]["RB-01"]; wh = demo["warehouses"]["chem"]
    try:
        inventory.transfer_stock(db, company_id=demo["company_id"], item_type=ItemType.CHEMICAL,
                                 item_id=chem, from_warehouse_id=wh, to_warehouse_id=wh, qty="10")
        assert False, "expected ValueError"
    except ValueError:
        db.rollback()


# ------------------------------ roll split/merge ----------------------------

def _roll(db, demo, weight="250"):
    import uuid as _uuid
    kp = KnittingProduction(company_id=demo["company_id"], number=f"KP-RO-{_uuid.uuid4().hex[:6]}",
                            machine_id=demo["machines"]["knit"],
                            yarn_id=demo["yarn_id"], yarn_lot_id=demo["yarn_lot_id"],
                            fabric_type_id=demo["fabric_type_id"], production_date=date(2026, 11, 8),
                            produced_kg=Decimal(weight), yarn_consumed_kg=Decimal("255"))
    db.add(kp); db.flush()
    rolls = record_production(db, kp, [{"weight_kg": weight, "warehouse_id": demo["warehouses"]["greige"]}])["rolls"]
    db.commit()
    return rolls[0]


# Stage 4A: roll split/merge are RETIRED (unsafe legacy operations). They reject
# every call, create no derived Roll IDs, and change no inventory or genealogy.
import uuid as _uuid

import pytest
from sqlalchemy import func, select

from app.models.genealogy import RollGenealogyEdge
from app.models.manufacturing import FabricRoll


def _counts(db, demo):
    cid = demo["company_id"]
    rolls = db.execute(select(func.count()).select_from(FabricRoll).where(FabricRoll.company_id == cid)).scalar()
    edges = db.execute(select(func.count()).select_from(RollGenealogyEdge).where(
        RollGenealogyEdge.company_id == cid)).scalar()
    return rolls, edges


def test_split_service_rejected_no_side_effects(db, demo):
    parent = _roll(db, demo, "250")
    rolls0, edges0 = _counts(db, demo)
    bal0 = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC, item_id=parent.id)["value"]
    with pytest.raises(roll_ops.RollOpError):
        roll_ops.split_roll(db, company_id=demo["company_id"], roll_id=parent.id,
                            parts=[{"weight_kg": "100"}, {"weight_kg": "150"}])
    rolls1, edges1 = _counts(db, demo)
    assert rolls1 == rolls0 and edges1 == edges0          # no new roll / no genealogy
    db.refresh(parent)
    assert parent.status == "ACTIVE" and parent.weight_kg == Decimal("250.000000")   # unchanged
    assert inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC,
                             item_id=parent.id)["value"] == bal0                      # balance unchanged
    # no derived "-S" roll was created
    assert db.execute(select(func.count()).select_from(FabricRoll).where(
        FabricRoll.roll_no.like(f"{parent.roll_no}-S%"))).scalar() == 0


def test_merge_service_rejected_no_side_effects(db, demo):
    r1 = _roll(db, demo, "120"); r2 = _roll(db, demo, "130")
    rolls0, edges0 = _counts(db, demo)
    with pytest.raises(roll_ops.RollOpError):
        roll_ops.merge_rolls(db, company_id=demo["company_id"], roll_ids=[r1.id, r2.id])
    rolls1, edges1 = _counts(db, demo)
    assert rolls1 == rolls0 and edges1 == edges0
    db.refresh(r1); db.refresh(r2)
    assert r1.status == "ACTIVE" and r2.status == "ACTIVE"
    assert db.execute(select(func.count()).select_from(FabricRoll).where(
        FabricRoll.roll_no.like("%-MRG"))).scalar() == 0


def test_split_api_returns_410(client, auth_headers, db, demo):
    parent = _roll(db, demo, "250")
    r = client.post(f"/api/inventory/rolls/{parent.id}/split", headers=auth_headers,
                    json={"parts": [{"weight_kg": "100"}, {"weight_kg": "150"}]})
    assert r.status_code == 410
    assert "not supported" in r.json()["detail"].lower()


def test_merge_api_returns_410(client, auth_headers, db, demo):
    r1 = _roll(db, demo, "120"); r2 = _roll(db, demo, "130")
    r = client.post("/api/inventory/rolls/merge", headers=auth_headers,
                    json={"roll_ids": [str(r1.id), str(r2.id)]})
    assert r.status_code == 410


def test_custody_roll_cannot_be_split_or_merged(db, demo):
    from app.models.masters import Supplier
    from app.services import jobwork_custody as cust
    from app.services.hybrid import update_process_mode
    s = db.get(Supplier, demo["supplier_id"]); s.is_job_worker = True; db.flush()
    update_process_mode(db, demo["company_id"], {"dye_outsourced": True})
    roll = _roll(db, demo, "100")
    cust.issue_to_vendor(db, company_id=demo["company_id"], job_worker_id=demo["supplier_id"],
                         process="DYEING", item_type=ItemType.FABRIC, item_id=roll.id, qty=Decimal("100"),
                         source_warehouse_id=roll.warehouse_id, fabric_roll_id=roll.id, issue_date=date(2026, 11, 8))
    db.refresh(roll); custody_wh = roll.warehouse_id
    with pytest.raises(roll_ops.RollOpError):
        roll_ops.split_roll(db, company_id=demo["company_id"], roll_id=roll.id, parts=[{"weight_kg": "50"}])
    with pytest.raises(roll_ops.RollOpError):
        roll_ops.merge_rolls(db, company_id=demo["company_id"], roll_ids=[roll.id, _roll(db, demo, "60").id])
    db.refresh(roll)
    assert roll.warehouse_id == custody_wh               # still in custody, untouched


def test_historical_split_merge_records_readable(db, demo):
    # simulate a historical split: a parent closed SPLIT with a child pointing to it
    parent = _roll(db, demo, "250")
    child = FabricRoll(company_id=demo["company_id"], roll_no="HIST-S1", kind=RollKind.GREIGE,
                       weight_kg=Decimal("250"), parent_roll_id=parent.id, status="ACTIVE")
    parent.status = "SPLIT"; parent.weight_kg = Decimal("0")
    db.add(child); db.commit()
    # historical records remain readable (not deleted or renumbered)
    got = db.get(FabricRoll, child.id)
    assert got is not None and got.parent_roll_id == parent.id and got.roll_no == "HIST-S1"
    assert db.get(FabricRoll, parent.id).status == "SPLIT"
