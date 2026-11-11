"""Stores & spares: receive consumables, issue to a machine, expense on issue."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services import stores as svc
from app.services.accounting import trial_balance as tb


def _tb(db, demo, code):
    rows = tb.trial_balance(db, demo["company_id"])["rows"]
    return next((r for r in rows if r["account_code"] == code), None)


def _needle(db, demo, code="NDL-9G"):
    return svc.create_item(db, company_id=demo["company_id"], code=code,
                           name="9G Knitting Needle", category="NEEDLE", uom="PCS")


def test_receive_puts_stock_and_posts_inventory(db, demo):
    item = _needle(db, demo)
    res = svc.receive(db, company_id=demo["company_id"], store_item_id=item.id,
                      qty="500", unit_cost="20", warehouse_id=demo["warehouses"]["greige"])
    db.commit()
    assert res["value"] == "10000.00"
    # Dr Stores & Spares Inventory 1115
    assert _tb(db, demo, "1115")["period_debit"] == Decimal("10000.00")


def test_issue_expenses_at_fifo_cost(db, demo):
    item = _needle(db, demo, code="NDL-12G")
    wh = demo["warehouses"]["greige"]
    svc.receive(db, company_id=demo["company_id"], store_item_id=item.id, qty="100",
                unit_cost="20", warehouse_id=wh)
    svc.receive(db, company_id=demo["company_id"], store_item_id=item.id, qty="100",
                unit_cost="30", warehouse_id=wh)
    db.commit()
    # issue 150 → 100@20 + 50@30 = 3,500
    res = svc.issue(db, company_id=demo["company_id"], store_item_id=item.id, qty="150",
                    warehouse_id=wh, machine="KNIT-01")
    db.commit()
    assert res["cost"] == "3500.00"
    # received 100@20 + 100@30 = 5,000; issued 3,500; so 1,500 remains
    assert _tb(db, demo, "6035")["period_debit"] == Decimal("3500.00")
    assert _tb(db, demo, "1115")["closing_debit"] == Decimal("1500.00")


def test_store_item_shows_in_stock_on_hand(db, demo):
    from app.models.inventory import ItemType
    from app.services import inventory
    item = _needle(db, demo, code="NDL-14G")
    svc.receive(db, company_id=demo["company_id"], store_item_id=item.id, qty="200",
                unit_cost="15", warehouse_id=demo["warehouses"]["greige"])
    db.commit()
    rows = inventory.stock_on_hand(db, demo["company_id"], item_type=ItemType.STORE)
    row = next(r for r in rows if r["item_code"] == "NDL-14G")
    assert Decimal(str(row["qty"])) == Decimal("200") and row["value"] == Decimal("3000.00")


def test_duplicate_code_rejected(db, demo):
    _needle(db, demo, code="NDL-DUP"); db.flush()
    with pytest.raises(svc.StoreError):
        _needle(db, demo, code="NDL-DUP")


def test_issue_more_than_stock_rejected(db, demo):
    from app.services.inventory.service import InsufficientStockError
    item = _needle(db, demo, code="NDL-X")
    svc.receive(db, company_id=demo["company_id"], store_item_id=item.id, qty="10",
                unit_cost="20", warehouse_id=demo["warehouses"]["greige"])
    db.commit()
    with pytest.raises(InsufficientStockError):
        svc.issue(db, company_id=demo["company_id"], store_item_id=item.id, qty="50",
                  warehouse_id=demo["warehouses"]["greige"])


def test_stores_api_flow(client, auth_headers, demo):
    it = client.post("/api/stores/items", headers=auth_headers, json={
        "code": "SPARE-1", "name": "Sinker", "category": "SPARE"})
    assert it.status_code == 201, it.text
    sid = it.json()["id"]
    rc = client.post("/api/stores/receipts", headers=auth_headers, json={
        "store_item_id": sid, "qty": "40", "unit_cost": "25",
        "warehouse_id": str(demo["warehouses"]["greige"])})
    assert rc.status_code == 201 and rc.json()["value"] == "1000.00"
    iss = client.post("/api/stores/issues", headers=auth_headers, json={
        "store_item_id": sid, "qty": "10", "warehouse_id": str(demo["warehouses"]["greige"]),
        "machine": "KNIT-02"})
    assert iss.status_code == 201 and iss.json()["cost"] == "250.00"
