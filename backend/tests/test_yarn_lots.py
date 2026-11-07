"""Full yarn lot tracking: receipt by lot, FIFO stock per lot, lot-traceable
knitting, and GL consistency."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.inventory import ItemType
from app.models.manufacturing import KnittingProduction
from app.services import inventory
from app.services.accounting import stock_reconciliation as sr
from app.services.manufacturing.knitting import record_production


def test_demo_yarn_is_in_a_lot(db, demo):
    bal = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.YARN,
                            item_id=demo["yarn_id"], lot_id=demo["yarn_lot_id"])
    assert bal["qty_on_hand"] == Decimal("2000.000000")


def test_receive_yarn_lot_api_reconciles(client, auth_headers, demo):
    r = client.post("/api/masters/yarn-lots", headers=auth_headers, json={
        "yarn_id": str(demo["yarn_id"]), "internal_lot_no": "YLOT-2", "qty": "500",
        "unit_cost": "250", "supplier_id": str(demo["supplier_id"])})
    assert r.status_code == 201, r.text
    assert r.json()["voucher_number"]
    # the new lot shows on-hand stock
    lots = {l["internal_lot_no"]: l for l in client.get(
        f"/api/masters/yarn-lots?yarn_id={demo['yarn_id']}", headers=auth_headers).json()}
    assert lots["YLOT-2"]["on_hand"] == "500.000000"
    # yarn inventory still reconciles with the GL control account
    rec = client.get("/api/reports/stock-reconciliation", headers=auth_headers).json()
    yarn = next(row for row in rec["rows"] if row["account"] == "1060")
    assert yarn["difference"] == "0.00"
    # duplicate lot rejected
    assert client.post("/api/masters/yarn-lots", headers=auth_headers, json={
        "yarn_id": str(demo["yarn_id"]), "internal_lot_no": "YLOT-2", "qty": "1",
        "unit_cost": "1"}).status_code == 409


def test_knitting_consumes_from_specified_lot(db, demo):
    before = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.YARN,
                               item_id=demo["yarn_id"], lot_id=demo["yarn_lot_id"])["qty_on_hand"]
    kp = KnittingProduction(company_id=demo["company_id"], number="KP-LOT", machine_id=demo["machines"]["knit"],
                            yarn_id=demo["yarn_id"], yarn_lot_id=demo["yarn_lot_id"],
                            fabric_type_id=demo["fabric_type_id"], production_date=date(2026, 11, 7),
                            produced_kg=Decimal("250"), yarn_consumed_kg=Decimal("255"))
    db.add(kp)
    db.flush()
    result = record_production(db, kp, [{"roll_no": "R-LOT-1", "weight_kg": "250",
                                         "warehouse_id": demo["warehouses"]["greige"]}])
    db.commit()
    after = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.YARN,
                              item_id=demo["yarn_id"], lot_id=demo["yarn_lot_id"])["qty_on_hand"]
    assert before - after == Decimal("255.000000")          # drawn from that lot
    # the greige roll is traceable back to the yarn lot
    roll = result["rolls"][0]
    traced_kp = db.get(KnittingProduction, roll.knitting_production_id)
    assert traced_kp.yarn_lot_id == demo["yarn_lot_id"]
