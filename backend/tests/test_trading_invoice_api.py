"""Stage 4B-3A.1c.1 — trading invoice API tests + manufactured-invoice regression."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select

from app.core.security import hash_password
from app.models.accounting import AccountType, ChartOfAccount, PostingRule
from app.models.commercial import SalesInvoice
from app.models.core import Permission, Role, User
from app.models.hybrid import WarehouseCategory
from app.models.masters import Warehouse
from app.services import inventory, trading
from app.services import trading_sales as ts
from app.services import trading_dispatch as td
from app.models.inventory import ItemType

OD = "2026-11-15"


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


def _approved_dispatch(db, cid, customer_id, it, wh, qty="60", suffix="A"):
    inventory.receive(db, company_id=cid, item_type=ItemType.TRADING_FABRIC, item_id=it.id,
                      qty="100", unit_cost="100", warehouse_id=wh.id, event_type="GRN")
    so = ts.create_trading_so(db, cid, customer_id=customer_id, order_number=f"AISO-{suffix}", order_date=date(2026, 11, 15),
                              lines=[{"trading_item_id": it.id, "warehouse_id": wh.id, "qty_ordered": "100", "rate": "300"}])
    ts.approve_trading_so(db, cid, so.id)
    d = td.create_trading_dispatch(db, cid, customer_id=customer_id, sales_order_id=so.id,
                                   dispatch_number=f"AIDSP-{suffix}", dispatch_date=date(2026, 11, 15),
                                   lines=[{"trading_item_id": it.id, "sales_order_line_id": so.lines[0].id,
                                           "warehouse_id": wh.id, "quantity": qty}])
    td.submit_trading_dispatch(db, cid, d.id); td.approve_trading_dispatch(db, cid, d.id); db.flush()
    return d


def test_api_invoice_lifecycle(client, auth_headers, db, demo):
    cid = demo["company_id"]; _configure(db, cid)
    it = _item(db, cid, "API-IV"); wh = _trade_wh(db, cid, "WH-APIIV")
    d = _approved_dispatch(db, cid, demo["customer_id"], it, wh, qty="60", suffix="LC"); db.commit()
    body = {"customer_id": str(demo["customer_id"]), "number": "AINV-1", "invoice_date": OD,
            "lines": [{"dispatch_line_id": str(d.lines[0].id), "trading_item_id": str(it.id),
                       "quantity": "60", "rate": "300", "discount": "0"}]}
    r = client.post("/api/trading/sales-invoices", headers=auth_headers, json=body)
    assert r.status_code == 201, r.text
    iid = r.json()["id"]
    assert r.json()["source"] == "TRADING" and r.json()["status"] == "DRAFT"
    assert r.json()["taxable_value"] == "18000.00" and r.json()["posted"] is False
    # duplicate number -> 409
    assert client.post("/api/trading/sales-invoices", headers=auth_headers, json=body).status_code == 409
    # get + list
    assert client.get(f"/api/trading/sales-invoices/{iid}", headers=auth_headers).status_code == 200
    lst = client.get("/api/trading/sales-invoices?limit=10", headers=auth_headers).json()
    assert lst["total"] >= 1 and any(i["id"] == iid for i in lst["items"])
    # cancel
    assert client.post(f"/api/trading/sales-invoices/{iid}/cancel", headers=auth_headers).json()["status"] == "CANCELLED"


def test_api_route_surface(client, auth_headers, db, demo):
    schema = client.get("/openapi.json").json()
    # No stand-alone approve step for invoices; posting exists as of 4B-3A.1c.2.
    assert "/api/trading/sales-invoices/{invoice_id}/approve" not in schema["paths"]
    assert "/api/trading/sales-invoices/{invoice_id}/post" in schema["paths"]


def test_api_over_invoice_422(client, auth_headers, db, demo):
    cid = demo["company_id"]; _configure(db, cid)
    it = _item(db, cid, "API-OV"); wh = _trade_wh(db, cid, "WH-APIOV")
    d = _approved_dispatch(db, cid, demo["customer_id"], it, wh, qty="40", suffix="OV"); db.commit()
    r = client.post("/api/trading/sales-invoices", headers=auth_headers, json={
        "customer_id": str(demo["customer_id"]), "number": "AINV-OV", "invoice_date": OD,
        "lines": [{"dispatch_line_id": str(d.lines[0].id), "trading_item_id": str(it.id), "quantity": "80", "rate": "300"}]})
    assert r.status_code == 422


def test_api_requires_permission(client, db, demo):
    cid = demo["company_id"]
    perm = db.execute(select(Permission).where(Permission.code == "report.read")).scalar_one()
    role = Role(code="IVRDR", name="Reader"); role.permissions.append(perm)
    u = User(username="ivreader", email="iv@demo.local", full_name="iv", hashed_password=hash_password("passpass123"),
             is_active=True, is_superuser=False, company_id=cid)
    u.roles.append(role); db.add(u); db.commit()
    tok = client.post("/api/auth/login", data={"username": "ivreader", "password": "passpass123"}).json()["access_token"]
    r = client.post("/api/trading/sales-invoices", headers={"Authorization": f"Bearer {tok}"}, json={
        "customer_id": str(demo["customer_id"]), "number": "X", "invoice_date": OD,
        "lines": [{"dispatch_line_id": str(uuid.uuid4()), "trading_item_id": str(uuid.uuid4()), "quantity": "1"}]})
    assert r.status_code == 403


def _configure_sales(db, cid):
    """Synthetic TEST TRADING_SALES mapping (dedicated revenue 4011 + existing
    receivable/output-GST accounts)."""
    if db.execute(select(ChartOfAccount).where(ChartOfAccount.company_id == cid,
                                               ChartOfAccount.code == "4011")).scalar_one_or_none() is None:
        db.add(ChartOfAccount(company_id=cid, code="4011", name="Trading Fabric Sales",
                              account_type=AccountType.INCOME.value, is_group=False,
                              allow_posting=True, is_active=True))
    for slot, code in {"receivable": "1040", "sales": "4011", "output_cgst": "2041",
                       "output_sgst": "2042", "output_igst": "2043"}.items():
        db.add(PostingRule(company_id=cid, event_type="TRADING_SALES", role=slot, account_code=code))
    db.flush()


def _draft_via_api(client, auth_headers, db, cid, customer_id, *, number, suffix, tax_id=None):
    it = _item(db, cid, f"API-{suffix}"); wh = _trade_wh(db, cid, f"WH-{suffix}")
    d = _approved_dispatch(db, cid, customer_id, it, wh, qty="60", suffix=suffix); db.commit()
    line = {"dispatch_line_id": str(d.lines[0].id), "trading_item_id": str(it.id),
            "quantity": "60", "rate": "300", "discount": "0"}
    if tax_id is not None:
        line["tax_code_id"] = str(tax_id)
    r = client.post("/api/trading/sales-invoices", headers=auth_headers, json={
        "customer_id": str(customer_id), "number": number, "invoice_date": OD, "lines": [line]})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_api_post_success(client, auth_headers, db, demo):
    cid = demo["company_id"]; _configure(db, cid); _configure_sales(db, cid)
    iid = _draft_via_api(client, auth_headers, db, cid, demo["customer_id"],
                         number="APINV-OK", suffix="POK", tax_id=demo["tax_id"])
    r = client.post(f"/api/trading/sales-invoices/{iid}/post", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "POSTED" and body["posted"] is True
    assert body["voucher_number"] is not None and body["already_posted"] is False
    assert body["total_value"] == "21240.00"      # 18000 + 18% GST (intra 9+9)
    # idempotent re-post via API
    r2 = client.post(f"/api/trading/sales-invoices/{iid}/post", headers=auth_headers)
    assert r2.status_code == 200 and r2.json()["already_posted"] is True


def test_api_post_fail_closed_409(client, auth_headers, db, demo):
    cid = demo["company_id"]; _configure(db, cid)   # TRADING_SALES NOT configured
    iid = _draft_via_api(client, auth_headers, db, cid, demo["customer_id"],
                         number="APINV-NA", suffix="PNA", tax_id=demo["tax_id"])
    r = client.post(f"/api/trading/sales-invoices/{iid}/post", headers=auth_headers)
    assert r.status_code == 409, r.text
    # invoice stays DRAFT, unposted
    g = client.get(f"/api/trading/sales-invoices/{iid}", headers=auth_headers).json()
    assert g["status"] == "DRAFT" and g["posted"] is False


def test_api_post_requires_voucher_post_permission(client, db, demo):
    cid = demo["company_id"]; _configure(db, cid); _configure_sales(db, cid)
    # a user with sales.manage (can create invoices) but NOT voucher.post
    perm = db.execute(select(Permission).where(Permission.code == "sales.manage")).scalar_one()
    role = Role(code="IVMGR", name="InvMgr"); role.permissions.append(perm)
    u = User(username="ivmgr", email="ivm@demo.local", full_name="ivm",
             hashed_password=hash_password("passpass123"), is_active=True,
             is_superuser=False, company_id=cid)
    u.roles.append(role); db.add(u); db.commit()
    tok = client.post("/api/auth/login", data={"username": "ivmgr", "password": "passpass123"}).json()["access_token"]
    hdr = {"Authorization": f"Bearer {tok}"}
    iid = _draft_via_api(client, hdr, db, cid, demo["customer_id"],
                         number="APINV-PERM", suffix="PPM", tax_id=demo["tax_id"])
    r = client.post(f"/api/trading/sales-invoices/{iid}/post", headers=hdr)
    assert r.status_code == 403


def test_manufactured_invoice_cannot_post_via_trading_route(client, auth_headers, db, demo):
    """A manufactured invoice is invisible to the trading post route (source scope)."""
    r = client.post("/api/sales/invoices", headers=auth_headers, json={
        "customer_id": str(demo["customer_id"]), "number": "MINV-X", "invoice_date": OD,
        "lines": [{"description": "mfg", "qty": "10", "rate": "500", "tax_code_id": str(demo["tax_id"])}]})
    assert r.status_code == 201, r.text
    mid = db.execute(select(SalesInvoice).where(SalesInvoice.company_id == demo["company_id"],
                                                SalesInvoice.number == "MINV-X")).scalar_one().id
    assert client.post(f"/api/trading/sales-invoices/{mid}/post", headers=auth_headers).status_code == 404


def test_manufactured_invoice_route_unchanged(client, auth_headers, db, demo):
    """The existing manufactured /api/sales/invoices route still posts a revenue
    invoice; new rows default source=MANUFACTURED without any route change."""
    r = client.post("/api/sales/invoices", headers=auth_headers, json={
        "customer_id": str(demo["customer_id"]), "number": "MINV-1", "invoice_date": OD,
        "lines": [{"description": "manufactured fabric", "qty": "10", "rate": "500",
                   "tax_code_id": str(demo["tax_id"])}]})
    assert r.status_code == 201, r.text
    assert r.json()["voucher_number"] is not None            # manufactured invoice still posts
    inv = db.execute(select(SalesInvoice).where(SalesInvoice.company_id == demo["company_id"],
                                                SalesInvoice.number == "MINV-1")).scalar_one()
    assert inv.source == "MANUFACTURED" and str(inv.status) in ("DocStatus.POSTED", "POSTED")
