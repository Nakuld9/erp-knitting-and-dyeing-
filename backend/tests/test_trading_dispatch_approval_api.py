"""Stage 4B-3A.1b.2 — trading dispatch approval API tests."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select

from app.core.security import hash_password
from app.models.accounting import AccountType, ChartOfAccount, PostingRule
from app.models.core import Permission, Role, User
from app.models.hybrid import WarehouseCategory
from app.models.masters import Warehouse
from app.services import inventory, trading
from app.services import trading_sales as ts
from app.services import trading_dispatch as td
from app.models.inventory import ItemType

OD = "2026-11-14"


def _trade_wh(db, cid, code):
    w = Warehouse(company_id=cid, code=code, name=code, kind="STORE",
                  category=WarehouseCategory.TRADING.value)
    db.add(w); db.flush()
    return w


def _item(db, cid, code):
    it = trading.create_trading_item(db, cid, code=code, name=code); db.flush()
    return it


def _configure(db, cid):
    for code, name, typ in [("5155", "Trading COGS", AccountType.EXPENSE.value),
                            ("1105", "Trading Fabric Inventory", AccountType.ASSET.value)]:
        if db.execute(select(ChartOfAccount).where(ChartOfAccount.company_id == cid,
                                                   ChartOfAccount.code == code)).scalar_one_or_none() is None:
            db.add(ChartOfAccount(company_id=cid, code=code, name=name, account_type=typ,
                                  is_group=False, allow_posting=True, is_active=True))
    for slot, code in {"cogs": "5155", "inventory": "1105"}.items():
        db.add(PostingRule(company_id=cid, event_type="TRADING_DELIVERY", role=slot, account_code=code))
    db.flush()


def _setup(db, cid, customer_id, code, qty="100", rate="100", recv="100"):
    it = _item(db, cid, code); wh = _trade_wh(db, cid, f"WH-{code}")
    inventory.receive(db, company_id=cid, item_type=ItemType.TRADING_FABRIC, item_id=it.id,
                      qty=recv, unit_cost=rate, warehouse_id=wh.id, event_type="GRN")
    so = ts.create_trading_so(db, cid, customer_id=customer_id, order_number=f"SO-{code}", order_date=date(2026, 11, 14),
                              lines=[{"trading_item_id": it.id, "warehouse_id": wh.id, "qty_ordered": qty, "rate": "300"}])
    ts.approve_trading_so(db, cid, so.id); db.flush()
    return it, wh, so


def _mk_submitted(client, headers, demo, so, it, wh, number, qty="60"):
    r = client.post("/api/trading/dispatches", headers=headers, json={
        "customer_id": str(demo["customer_id"]), "sales_order_id": str(so.id),
        "dispatch_number": number, "dispatch_date": OD,
        "lines": [{"trading_item_id": str(it.id), "sales_order_line_id": str(so.lines[0].id),
                   "warehouse_id": str(wh.id), "quantity": qty}]})
    did = r.json()["id"]
    client.post(f"/api/trading/dispatches/{did}/submit", headers=headers)
    return did


def test_api_approve_happy_path(client, auth_headers, db, demo):
    cid = demo["company_id"]; _configure(db, cid)
    it, wh, so = _setup(db, cid, demo["customer_id"], "APIAP"); db.commit()
    did = _mk_submitted(client, auth_headers, demo, so, it, wh, "DSP-APIAP")
    r = client.post(f"/api/trading/dispatches/{did}/approve", headers=auth_headers, json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "APPROVED" and r.json()["voucher_number"] is not None
    assert r.json()["cogs_value"] == "6000.00"       # 60 @ 100
    # FIFO audit evidence retrievable
    al = client.get(f"/api/trading/dispatches/{did}/allocations", headers=auth_headers).json()
    assert len(al["allocations"]) == 1 and al["allocations"][0]["total_cost"] == "6000.00"


def test_api_approve_blocked_without_mappings_409(client, auth_headers, db, demo):
    cid = demo["company_id"]   # NOT configured
    it, wh, so = _setup(db, cid, demo["customer_id"], "APINA"); db.commit()
    did = _mk_submitted(client, auth_headers, demo, so, it, wh, "DSP-APINA")
    r = client.post(f"/api/trading/dispatches/{did}/approve", headers=auth_headers, json={})
    assert r.status_code == 409
    assert "not configured" in r.json()["detail"].lower()
    assert client.get(f"/api/trading/dispatches/{did}", headers=auth_headers).json()["status"] == "SUBMITTED"


def test_api_approve_requires_voucher_post(client, auth_headers, db, demo):
    cid = demo["company_id"]; _configure(db, cid)
    it, wh, so = _setup(db, cid, demo["customer_id"], "APIPERM"); db.commit()
    did = _mk_submitted(client, auth_headers, demo, so, it, wh, "DSP-APIPERM")
    # a sales.manage-only user can create/submit but NOT approve (needs voucher.post)
    perms = db.execute(select(Permission).where(Permission.code == "sales.manage")).scalars().all()
    role = Role(code="SALESONLY", name="Sales Only"); [role.permissions.append(p) for p in perms]
    u = User(username="salesonly", email="so@demo.local", full_name="Sales Only",
             hashed_password=hash_password("passpass123"), is_active=True, is_superuser=False, company_id=cid)
    u.roles.append(role); db.add(u); db.commit()
    tok = client.post("/api/auth/login", data={"username": "salesonly", "password": "passpass123"}).json()["access_token"]
    r = client.post(f"/api/trading/dispatches/{did}/approve", headers={"Authorization": f"Bearer {tok}"}, json={})
    assert r.status_code == 403
