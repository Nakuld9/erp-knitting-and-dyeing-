"""Stage 5B — direct yarn sales API tests + manufactured/trading regression."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select

from app.core.security import hash_password
from app.models.accounting import AccountType, ChartOfAccount, PostingRule
from app.models.commercial import SalesInvoice
from app.models.core import Permission, Role, User
from app.models.hybrid import WarehouseCategory
from app.models.inventory import ItemType
from app.models.masters import Warehouse, YarnLot, YarnMaster
from app.services import inventory

OD = "2026-11-15"


def _yarn_wh(db, cid, code):
    w = Warehouse(company_id=cid, code=code, name=code, kind="STORE",
                  category=WarehouseCategory.YARN_STORE.value)
    db.add(w); db.flush()
    return w


def _yarn(db, cid, code):
    y = YarnMaster(company_id=cid, code=code, name=code, is_active=True); db.add(y); db.flush()
    return y


def _lot(db, cid, y, wh, *, internal, qty="100", cost="200"):
    lot = YarnLot(company_id=cid, yarn_id=y.id, internal_lot_no=internal, supplier_lot_no=internal + "-S",
                  warehouse_id=wh.id, received_date=OD, unit_cost=cost)
    db.add(lot); db.flush()
    inventory.receive(db, company_id=cid, item_type=ItemType.YARN, item_id=y.id, qty=qty,
                      unit_cost=cost, lot_id=lot.id, warehouse_id=wh.id, event_type="YARN_GRN")
    db.flush()
    return lot


def _configure(db, cid):
    for code, name, typ in [("5160", "Yarn COGS", AccountType.EXPENSE.value),
                            ("4012", "Yarn Sales", AccountType.INCOME.value)]:
        if db.execute(select(ChartOfAccount).where(ChartOfAccount.company_id == cid,
                      ChartOfAccount.code == code)).scalar_one_or_none() is None:
            db.add(ChartOfAccount(company_id=cid, code=code, name=name, account_type=typ,
                                  is_group=False, allow_posting=True, is_active=True))
    for slot, code in {"cogs": "5160", "inventory": "1060"}.items():
        db.add(PostingRule(company_id=cid, event_type="YARN_DELIVERY", role=slot, account_code=code))
    for slot, code in {"receivable": "1040", "sales": "4012", "output_cgst": "2041",
                       "output_sgst": "2042", "output_igst": "2043"}.items():
        db.add(PostingRule(company_id=cid, event_type="YARN_SALES", role=slot, account_code=code))
    db.flush()


def test_api_full_lifecycle(client, auth_headers, db, demo):
    cid = demo["company_id"]; _configure(db, cid)
    wh = _yarn_wh(db, cid, "YWHA-1"); y = _yarn(db, cid, "YA-1"); _lot(db, cid, y, wh, internal="LA1")
    db.commit()
    # create + approve order
    r = client.post("/api/yarn-sales/orders", headers=auth_headers, json={
        "customer_id": str(demo["customer_id"]), "order_number": "AYSO-1", "order_date": OD,
        "lines": [{"yarn_id": str(y.id), "warehouse_id": str(wh.id), "qty_ordered": "60", "rate": "300"}]})
    assert r.status_code == 201, r.text
    soid = r.json()["id"]; sol_id = r.json()["lines"][0]["id"]
    assert client.post(f"/api/yarn-sales/orders/{soid}/approve", headers=auth_headers).json()["status"] == "APPROVED"
    # lot availability shows the supplier lot
    av = client.get(f"/api/yarn-sales/lot-availability?yarn_id={y.id}", headers=auth_headers).json()
    assert av["lots"] and av["lots"][0]["supplier_lot_no"] == "LA1-S" and av["is_reservation"] is False
    # dispatch + submit + approve (COGS)
    rd = client.post("/api/yarn-sales/dispatches", headers=auth_headers, json={
        "customer_id": str(demo["customer_id"]), "sales_order_id": soid, "dispatch_number": "AYDSP-1",
        "dispatch_date": OD, "lines": [{"yarn_id": str(y.id), "sales_order_line_id": sol_id,
                                        "warehouse_id": str(wh.id), "quantity": "60"}]})
    assert rd.status_code == 201, rd.text
    did = rd.json()["id"]; dl_id = rd.json()["lines"][0]["id"]
    client.post(f"/api/yarn-sales/dispatches/{did}/submit", headers=auth_headers)
    ra = client.post(f"/api/yarn-sales/dispatches/{did}/approve", headers=auth_headers)
    assert ra.status_code == 200 and ra.json()["status"] == "APPROVED"
    assert ra.json()["voucher_number"] is not None and ra.json()["cogs_value"] == "12000.00"
    # allocations show the preserved supplier lot
    alloc = client.get(f"/api/yarn-sales/dispatches/{did}/allocations", headers=auth_headers).json()
    assert alloc["items"] and alloc["items"][0]["supplier_lot_no"] == "LA1-S"
    # invoice + post
    ri = client.post("/api/yarn-sales/sales-invoices", headers=auth_headers, json={
        "customer_id": str(demo["customer_id"]), "number": "AYINV-1", "invoice_date": OD,
        "lines": [{"dispatch_line_id": dl_id, "quantity": "60", "rate": "300",
                   "tax_code_id": str(demo["tax_id"])}]})
    assert ri.status_code == 201, ri.text
    iid = ri.json()["id"]; assert ri.json()["source"] == "YARN"
    rp = client.post(f"/api/yarn-sales/sales-invoices/{iid}/post", headers=auth_headers)
    assert rp.status_code == 200 and rp.json()["status"] == "POSTED"
    assert rp.json()["total_value"] == "21240.00" and rp.json()["voucher_number"] is not None


def test_api_post_fail_closed_409(client, auth_headers, db, demo):
    cid = demo["company_id"]
    # delivery only so dispatch can approve; YARN_SALES deliberately absent
    for code, name, typ in [("5160", "Yarn COGS", AccountType.EXPENSE.value)]:
        db.add(ChartOfAccount(company_id=cid, code=code, name=name, account_type=typ,
                              is_group=False, allow_posting=True, is_active=True))
    for slot, code in {"cogs": "5160", "inventory": "1060"}.items():
        db.add(PostingRule(company_id=cid, event_type="YARN_DELIVERY", role=slot, account_code=code))
    db.flush()
    wh = _yarn_wh(db, cid, "YWHA-FC"); y = _yarn(db, cid, "YA-FC"); _lot(db, cid, y, wh, internal="LFC")
    db.commit()
    r = client.post("/api/yarn-sales/orders", headers=auth_headers, json={
        "customer_id": str(demo["customer_id"]), "order_number": "AYSO-FC", "order_date": OD,
        "lines": [{"yarn_id": str(y.id), "warehouse_id": str(wh.id), "qty_ordered": "60", "rate": "300"}]})
    soid = r.json()["id"]; sol_id = r.json()["lines"][0]["id"]
    client.post(f"/api/yarn-sales/orders/{soid}/approve", headers=auth_headers)
    rd = client.post("/api/yarn-sales/dispatches", headers=auth_headers, json={
        "customer_id": str(demo["customer_id"]), "sales_order_id": soid, "dispatch_number": "AYDSP-FC",
        "dispatch_date": OD, "lines": [{"yarn_id": str(y.id), "sales_order_line_id": sol_id,
                                        "warehouse_id": str(wh.id), "quantity": "60"}]})
    did = rd.json()["id"]; dl_id = rd.json()["lines"][0]["id"]
    client.post(f"/api/yarn-sales/dispatches/{did}/submit", headers=auth_headers)
    client.post(f"/api/yarn-sales/dispatches/{did}/approve", headers=auth_headers)
    ri = client.post("/api/yarn-sales/sales-invoices", headers=auth_headers, json={
        "customer_id": str(demo["customer_id"]), "number": "AYINV-FC", "invoice_date": OD,
        "lines": [{"dispatch_line_id": dl_id, "quantity": "60", "rate": "300",
                   "tax_code_id": str(demo["tax_id"])}]})
    iid = ri.json()["id"]
    rp = client.post(f"/api/yarn-sales/sales-invoices/{iid}/post", headers=auth_headers)
    assert rp.status_code == 409, rp.text
    g = client.get(f"/api/yarn-sales/sales-invoices/{iid}", headers=auth_headers).json()
    assert g["status"] == "DRAFT" and g["posted"] is False


def test_api_dispatch_approve_requires_voucher_post(client, db, demo):
    cid = demo["company_id"]; _configure(db, cid)
    perm = db.execute(select(Permission).where(Permission.code == "sales.manage")).scalar_one()
    role = Role(code="YSMGR", name="YarnMgr"); role.permissions.append(perm)
    u = User(username="ysmgr", email="ys@demo.local", full_name="ys",
             hashed_password=hash_password("passpass123"), is_active=True,
             is_superuser=False, company_id=cid)
    u.roles.append(role); db.add(u)
    wh = _yarn_wh(db, cid, "YWHA-PM"); y = _yarn(db, cid, "YA-PM"); _lot(db, cid, y, wh, internal="LPM")
    db.commit()
    tok = client.post("/api/auth/login", data={"username": "ysmgr", "password": "passpass123"}).json()["access_token"]
    hdr = {"Authorization": f"Bearer {tok}"}
    r = client.post("/api/yarn-sales/orders", headers=hdr, json={
        "customer_id": str(demo["customer_id"]), "order_number": "AYSO-PM", "order_date": OD,
        "lines": [{"yarn_id": str(y.id), "warehouse_id": str(wh.id), "qty_ordered": "60", "rate": "300"}]})
    soid = r.json()["id"]; sol_id = r.json()["lines"][0]["id"]
    client.post(f"/api/yarn-sales/orders/{soid}/approve", headers=hdr)
    rd = client.post("/api/yarn-sales/dispatches", headers=hdr, json={
        "customer_id": str(demo["customer_id"]), "sales_order_id": soid, "dispatch_number": "AYDSP-PM",
        "dispatch_date": OD, "lines": [{"yarn_id": str(y.id), "sales_order_line_id": sol_id,
                                        "warehouse_id": str(wh.id), "quantity": "60"}]})
    did = rd.json()["id"]
    client.post(f"/api/yarn-sales/dispatches/{did}/submit", headers=hdr)
    assert client.post(f"/api/yarn-sales/dispatches/{did}/approve", headers=hdr).status_code == 403


def test_api_route_surface(client, auth_headers, db, demo):
    schema = client.get("/openapi.json").json()
    for p in ["/api/yarn-sales/orders", "/api/yarn-sales/lot-availability",
              "/api/yarn-sales/dispatches/{dispatch_id}/approve",
              "/api/yarn-sales/sales-invoices/{invoice_id}/post"]:
        assert p in schema["paths"], p


def test_manufactured_and_trading_invoice_routes_unchanged(client, auth_headers, db, demo):
    """Yarn work must not change the manufactured sales-invoice route."""
    r = client.post("/api/sales/invoices", headers=auth_headers, json={
        "customer_id": str(demo["customer_id"]), "number": "MINV-Y1", "invoice_date": OD,
        "lines": [{"description": "manufactured", "qty": "10", "rate": "500",
                   "tax_code_id": str(demo["tax_id"])}]})
    assert r.status_code == 201, r.text
    inv = db.execute(select(SalesInvoice).where(SalesInvoice.company_id == demo["company_id"],
                                                SalesInvoice.number == "MINV-Y1")).scalar_one()
    assert inv.source == "MANUFACTURED" and str(inv.status) in ("DocStatus.POSTED", "POSTED")
