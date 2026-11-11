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


def test_split_roll_preserves_value(db, demo):
    parent = _roll(db, demo, "250")
    before = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC,
                               item_id=parent.id)["value"]
    res = roll_ops.split_roll(db, company_id=demo["company_id"], roll_id=parent.id,
                              parts=[{"weight_kg": "100"}, {"weight_kg": "150"}])
    db.commit()
    assert len(res["children"]) == 2
    db.refresh(parent)
    assert parent.status == "SPLIT" and parent.weight_kg == Decimal("0.000000")
    assert before > 0
    # The parent's stock is fully moved to the children.
    assert inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC,
                             item_id=parent.id)["value"] == Decimal("0.00")
    # Value is conserved: the two children together hold the parent's old value.
    from app.models.manufacturing import FabricRoll
    children = db.query(FabricRoll).filter(FabricRoll.parent_roll_id == parent.id).all()
    child_val = sum((inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC,
                     item_id=c.id)["value"] for c in children), Decimal("0"))
    assert child_val == before


def test_split_exceeding_weight_rejected(db, demo):
    parent = _roll(db, demo, "100")
    try:
        roll_ops.split_roll(db, company_id=demo["company_id"], roll_id=parent.id,
                            parts=[{"weight_kg": "80"}, {"weight_kg": "40"}])
        assert False, "expected RollOpError"
    except roll_ops.RollOpError:
        db.rollback()


def test_merge_rolls_combines_value(db, demo):
    r1 = _roll(db, demo, "120")
    r2 = _roll(db, demo, "130")
    v1 = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC, item_id=r1.id)["value"]
    v2 = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC, item_id=r2.id)["value"]
    res = roll_ops.merge_rolls(db, company_id=demo["company_id"], roll_ids=[r1.id, r2.id])
    db.commit()
    assert res["weight_kg"] == Decimal("250.000000")
    assert res["cost_value"] == v1 + v2                            # value conserved
    db.refresh(r1); db.refresh(r2)
    assert r1.status == "MERGED" and r2.status == "MERGED"
    assert inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC, item_id=r1.id)["value"] == Decimal("0.00")
