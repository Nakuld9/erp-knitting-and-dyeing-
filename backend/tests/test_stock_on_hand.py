"""Live stock-on-hand by warehouse (from open FIFO layers)."""
from __future__ import annotations

from decimal import Decimal

from app.models.inventory import ItemType
from app.services import inventory


def test_stock_on_hand_groups_by_item_and_warehouse(db, demo):
    wh = demo["warehouses"]["greige"]
    # receive two lots of the same chemical into one warehouse
    chem = demo["chemicals"]["RB-01"]
    inventory.receive(db, company_id=demo["company_id"], item_type=ItemType.CHEMICAL,
                      item_id=chem, qty=Decimal("100"), unit_cost=Decimal("50"), warehouse_id=wh)
    inventory.receive(db, company_id=demo["company_id"], item_type=ItemType.CHEMICAL,
                      item_id=chem, qty=Decimal("40"), unit_cost=Decimal("60"), warehouse_id=wh)
    db.commit()

    rows = inventory.stock_on_hand(db, demo["company_id"], item_type=ItemType.CHEMICAL)
    mine = [r for r in rows if r["warehouse_id"] == str(wh) and r["item_type"] == "CHEMICAL"
            and r["qty"] > 0]
    # the two lots collapse into one line: 140 qty, value 100*50 + 40*60 = 7400
    chem_row = next(r for r in mine if Decimal(str(r["qty"])) == Decimal("140"))
    assert chem_row["value"] == Decimal("7400.00")
    assert chem_row["warehouse"]                       # warehouse name resolved


def test_stock_on_hand_warehouse_filter(db, demo):
    chem = demo["chemicals"]["RB-01"]
    a = demo["warehouses"]["greige"]; b = demo["warehouses"]["fg"]
    inventory.receive(db, company_id=demo["company_id"], item_type=ItemType.CHEMICAL,
                      item_id=chem, qty=Decimal("10"), unit_cost=Decimal("50"), warehouse_id=a)
    inventory.receive(db, company_id=demo["company_id"], item_type=ItemType.CHEMICAL,
                      item_id=chem, qty=Decimal("5"), unit_cost=Decimal("50"), warehouse_id=b)
    db.commit()
    only_a = inventory.stock_on_hand(db, demo["company_id"], warehouse_id=a)
    assert all(r["warehouse_id"] == str(a) for r in only_a)


def test_stock_on_hand_api(client, auth_headers):
    r = client.get("/api/inventory/stock-on-hand", headers=auth_headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_stock_on_hand_bad_item_type(client, auth_headers):
    r = client.get("/api/inventory/stock-on-hand?item_type=BOGUS", headers=auth_headers)
    assert r.status_code == 422
