"""Stage 4B-3A.1b.1 — trading dispatch API tests (HTTP, pagination, permissions)."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select

from app.core.security import hash_password
from app.models.accounting import Voucher
from app.models.core import Permission, Role, User
from app.models.hybrid import WarehouseCategory
from app.models.inventory import InventoryTransaction, StockLayer
from app.models.masters import Warehouse
from app.services import trading
from app.services import trading_sales as ts

OD = "2026-11-13"


def _trade_wh(db, cid, code="WH-APIDSP"):
    w = Warehouse(company_id=cid, code=code, name=code, kind="STORE",
                  category=WarehouseCategory.TRADING.value)
    db.add(w); db.flush()
    return w


def _item(db, cid, code):
    it = trading.create_trading_item(db, cid, code=code, name=code); db.flush()
    return it


def _approved_so(db, cid, customer_id, it, wh, qty="100", number="ASO-DSP"):
    so = ts.create_trading_so(db, cid, customer_id=customer_id, order_number=number, order_date=date(2026, 11, 13),
                              lines=[{"trading_item_id": it.id, "warehouse_id": wh.id, "qty_ordered": qty, "rate": "200"}])
    ts.approve_trading_so(db, cid, so.id); db.flush()
    return so


def _token(client, db, cid, username, perm_codes):
    perms = db.execute(select(Permission).where(Permission.code.in_(perm_codes))).scalars().all()
    role = Role(code=f"R_{username}", name=username)
    for p in perms:
        role.permissions.append(p)
    u = User(username=username, email=f"{username}@demo.local", full_name=username,
             hashed_password=hash_password("passpass123"), is_active=True, is_superuser=False, company_id=cid)
    u.roles.append(role); db.add(u); db.commit()
    return client.post("/api/auth/login", data={"username": username, "password": "passpass123"}).json()["access_token"]


def test_api_dispatch_lifecycle(client, auth_headers, db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "API-DSP"); wh = _trade_wh(db, cid)
    so = _approved_so(db, cid, demo["customer_id"], it, wh); db.commit()
    sol = so.lines[0].id
    r = client.post("/api/trading/dispatches", headers=auth_headers, json={
        "customer_id": str(demo["customer_id"]), "sales_order_id": str(so.id),
        "dispatch_number": "ADSP-1", "dispatch_date": OD, "vehicle_number": "PB-01-1234",
        "lines": [{"trading_item_id": str(it.id), "sales_order_line_id": str(sol),
                   "warehouse_id": str(wh.id), "quantity": "60"}]})
    assert r.status_code == 201, r.text
    did = r.json()["id"]
    assert r.json()["status"] == "DRAFT" and r.json()["vehicle_number"] == "PB-01-1234"
    # duplicate number -> 409
    dup = client.post("/api/trading/dispatches", headers=auth_headers, json={
        "customer_id": str(demo["customer_id"]), "sales_order_id": str(so.id),
        "dispatch_number": "ADSP-1", "dispatch_date": OD,
        "lines": [{"trading_item_id": str(it.id), "sales_order_line_id": str(sol),
                   "warehouse_id": str(wh.id), "quantity": "10"}]})
    assert dup.status_code == 409
    # get, list (paginated)
    assert client.get(f"/api/trading/dispatches/{did}", headers=auth_headers).status_code == 200
    lst = client.get("/api/trading/dispatches?limit=10", headers=auth_headers).json()
    assert "total" in lst and any(i["id"] == did for i in lst["items"])
    # submit + cancel
    assert client.post(f"/api/trading/dispatches/{did}/submit", headers=auth_headers).json()["status"] == "SUBMITTED"
    assert client.post(f"/api/trading/dispatches/{did}/cancel", headers=auth_headers).json()["status"] == "CANCELLED"


def test_api_approve_route_registered(client, auth_headers, db, demo):
    # 4B-3A.1b.2 adds the approve route (stock-moving approval lives there).
    schema = client.get("/openapi.json").json()
    assert "/api/trading/dispatches/{dispatch_id}/approve" in schema["paths"]


def test_api_qty_exceeds_outstanding_422(client, auth_headers, db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "API-EX"); wh = _trade_wh(db, cid, "WH-APIEX")
    so = _approved_so(db, cid, demo["customer_id"], it, wh, qty="50", number="ASO-EX"); db.commit()
    r = client.post("/api/trading/dispatches", headers=auth_headers, json={
        "customer_id": str(demo["customer_id"]), "sales_order_id": str(so.id),
        "dispatch_number": "ADSP-EX", "dispatch_date": OD,
        "lines": [{"trading_item_id": str(it.id), "sales_order_line_id": str(so.lines[0].id),
                   "warehouse_id": str(wh.id), "quantity": "80"}]})
    assert r.status_code == 422


def test_api_requires_permission(client, db, demo):
    cid = demo["company_id"]
    tok = _token(client, db, cid, "dreader", ["report.read"])
    h = {"Authorization": f"Bearer {tok}"}
    r = client.post("/api/trading/dispatches", headers=h, json={
        "customer_id": str(demo["customer_id"]), "sales_order_id": str(uuid.uuid4()),
        "dispatch_number": "NO", "dispatch_date": OD,
        "lines": [{"trading_item_id": str(uuid.uuid4()), "sales_order_line_id": str(uuid.uuid4()),
                   "warehouse_id": str(uuid.uuid4()), "quantity": "1"}]})
    assert r.status_code == 403


def test_api_creates_no_stock_or_voucher(client, auth_headers, db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "API-ISO"); wh = _trade_wh(db, cid, "WH-APIISOD")
    so = _approved_so(db, cid, demo["customer_id"], it, wh, number="ASO-ISO"); db.commit()
    v0 = db.execute(select(func.count()).select_from(Voucher).where(Voucher.company_id == cid)).scalar()
    l0 = db.execute(select(func.count()).select_from(StockLayer).where(StockLayer.company_id == cid)).scalar()
    t0 = db.execute(select(func.count()).select_from(InventoryTransaction).where(InventoryTransaction.company_id == cid)).scalar()
    r = client.post("/api/trading/dispatches", headers=auth_headers, json={
        "customer_id": str(demo["customer_id"]), "sales_order_id": str(so.id),
        "dispatch_number": "ADSP-ISO", "dispatch_date": OD,
        "lines": [{"trading_item_id": str(it.id), "sales_order_line_id": str(so.lines[0].id),
                   "warehouse_id": str(wh.id), "quantity": "50"}]})
    client.post(f"/api/trading/dispatches/{r.json()['id']}/submit", headers=auth_headers)
    assert db.execute(select(func.count()).select_from(Voucher).where(Voucher.company_id == cid)).scalar() == v0
    assert db.execute(select(func.count()).select_from(StockLayer).where(StockLayer.company_id == cid)).scalar() == l0
    assert db.execute(select(func.count()).select_from(InventoryTransaction).where(InventoryTransaction.company_id == cid)).scalar() == t0
