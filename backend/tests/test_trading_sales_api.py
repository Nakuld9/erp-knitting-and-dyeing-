"""Stage 4B-3A.1a — trading sales order API tests (HTTP, pagination, permissions)."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select

from app.core.security import hash_password
from app.models.accounting import Voucher
from app.models.core import Permission, Role, User
from app.models.hybrid import WarehouseCategory
from app.models.inventory import ItemType, StockLayer
from app.models.masters import Warehouse
from app.services import inventory, trading

OD = "2026-11-12"


def _trade_wh(db, cid, code="WH-APISELL"):
    w = Warehouse(company_id=cid, code=code, name=code, kind="STORE",
                  category=WarehouseCategory.TRADING.value)
    db.add(w); db.flush()
    return w


def _item(db, cid, code):
    it = trading.create_trading_item(db, cid, code=code, name=code); db.flush()
    return it


def _token(client, db, cid, username, perm_codes):
    perms = db.execute(select(Permission).where(Permission.code.in_(perm_codes))).scalars().all()
    role = Role(code=f"R_{username}", name=username)
    for p in perms:
        role.permissions.append(p)
    u = User(username=username, email=f"{username}@demo.local", full_name=username,
             hashed_password=hash_password("passpass123"), is_active=True, is_superuser=False, company_id=cid)
    u.roles.append(role); db.add(u); db.commit()
    return client.post("/api/auth/login", data={"username": username, "password": "passpass123"}).json()["access_token"]


def test_api_crud_lifecycle(client, auth_headers, db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "API-SO"); wh = _trade_wh(db, cid); db.commit()
    r = client.post("/api/trading/sales-orders", headers=auth_headers, json={
        "customer_id": str(demo["customer_id"]), "order_number": "ASO-1", "order_date": OD,
        "customer_po_ref": "CUST-PO-9", "lines": [
            {"trading_item_id": str(it.id), "warehouse_id": str(wh.id), "qty_ordered": "100",
             "rate": "200", "discount": "10"}]})
    assert r.status_code == 201, r.text
    so_id = r.json()["id"]
    assert r.json()["status"] == "DRAFT" and r.json()["customer_po_ref"] == "CUST-PO-9"
    # duplicate number -> 409
    dup = client.post("/api/trading/sales-orders", headers=auth_headers, json={
        "customer_id": str(demo["customer_id"]), "order_number": "ASO-1", "order_date": OD,
        "lines": [{"trading_item_id": str(it.id), "warehouse_id": str(wh.id), "qty_ordered": "1"}]})
    assert dup.status_code == 409
    # get, list (paginated), outstanding
    assert client.get(f"/api/trading/sales-orders/{so_id}", headers=auth_headers).status_code == 200
    lst = client.get("/api/trading/sales-orders?limit=10&offset=0", headers=auth_headers).json()
    assert "total" in lst and any(i["id"] == so_id for i in lst["items"])
    out = client.get(f"/api/trading/sales-orders/{so_id}/outstanding", headers=auth_headers).json()
    assert out["lines"][0]["outstanding"] == "100.000000"
    # approve, cancel blocked after approve? (cancel allowed from APPROVED w/o dispatch)
    assert client.post(f"/api/trading/sales-orders/{so_id}/approve", headers=auth_headers).json()["status"] == "APPROVED"
    assert client.post(f"/api/trading/sales-orders/{so_id}/cancel", headers=auth_headers).json()["status"] == "CANCELLED"


def test_api_availability_informational(client, auth_headers, db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "API-AV"); wh = _trade_wh(db, cid, "WH-APIAV")
    inventory.receive(db, company_id=cid, item_type=ItemType.TRADING_FABRIC, item_id=it.id,
                      qty="75", unit_cost="100", warehouse_id=wh.id, event_type="GRN")
    db.commit()
    r = client.get(f"/api/trading/availability?trading_item_id={it.id}&warehouse_id={wh.id}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["on_hand_qty"] == "75.000000"
    assert r.json()["informational_only"] is True and r.json()["is_guaranteed_allocation"] is False


def test_api_get_missing_404(client, auth_headers, db, demo):
    r = client.get(f"/api/trading/sales-orders/{uuid.uuid4()}", headers=auth_headers)
    assert r.status_code == 404


def test_api_requires_permission(client, db, demo):
    cid = demo["company_id"]
    tok = _token(client, db, cid, "readeronly", ["report.read"])
    h = {"Authorization": f"Bearer {tok}"}
    r = client.post("/api/trading/sales-orders", headers=h, json={
        "customer_id": str(demo["customer_id"]), "order_number": "NO", "order_date": OD,
        "lines": [{"trading_item_id": str(uuid.uuid4()), "warehouse_id": str(uuid.uuid4()), "qty_ordered": "1"}]})
    assert r.status_code == 403


def test_api_order_creates_no_voucher_or_stock(client, auth_headers, db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "API-ISO"); wh = _trade_wh(db, cid, "WH-APIISO"); db.commit()
    v0 = db.execute(select(func.count()).select_from(Voucher).where(Voucher.company_id == cid)).scalar()
    l0 = db.execute(select(func.count()).select_from(StockLayer).where(StockLayer.company_id == cid)).scalar()
    r = client.post("/api/trading/sales-orders", headers=auth_headers, json={
        "customer_id": str(demo["customer_id"]), "order_number": "ASO-ISO", "order_date": OD,
        "lines": [{"trading_item_id": str(it.id), "warehouse_id": str(wh.id), "qty_ordered": "100", "rate": "200"}]})
    client.post(f"/api/trading/sales-orders/{r.json()['id']}/approve", headers=auth_headers)
    v1 = db.execute(select(func.count()).select_from(Voucher).where(Voucher.company_id == cid)).scalar()
    l1 = db.execute(select(func.count()).select_from(StockLayer).where(StockLayer.company_id == cid)).scalar()
    assert v1 == v0 and l1 == l0            # nothing posted, no stock layer
