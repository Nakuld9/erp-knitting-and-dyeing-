"""Stage 4B-2 — trading purchase/GRN API tests (HTTP flow, permissions, scope)."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select

from app.core.security import hash_password
from app.models.accounting import AccountType, ChartOfAccount, PostingRule
from app.models.core import Permission, Role, User
from app.models.hybrid import WarehouseCategory
from app.models.masters import Warehouse
from app.services import trading

GRN_DATE = "2026-11-10"


def _trade_wh(db, cid, code="WH-API-T"):
    w = Warehouse(company_id=cid, code=code, name=code, kind="STORE",
                  category=WarehouseCategory.TRADING.value)
    db.add(w); db.flush()
    return w


def _item(db, cid, code):
    it = trading.create_trading_item(db, cid, code=code, name=code); db.flush()
    return it


def _configure(db, cid):
    if db.execute(select(ChartOfAccount).where(
            ChartOfAccount.company_id == cid, ChartOfAccount.code == "1105")).scalar_one_or_none() is None:
        db.add(ChartOfAccount(company_id=cid, code="1105", name="Trading Fabric Inventory",
                              account_type=AccountType.ASSET.value, is_group=False,
                              allow_posting=True, is_active=True, is_control=True))
    for slot, code in {"inventory": "1105", "input_cgst": "1121", "input_sgst": "1122",
                       "input_igst": "1123", "payable": "2010"}.items():
        db.add(PostingRule(company_id=cid, event_type="TRADING_GRN", role=slot, account_code=code))
    db.flush()


def _user_token(client, db, cid, username, perm_codes):
    perms = db.execute(select(Permission).where(Permission.code.in_(perm_codes))).scalars().all()
    role = Role(code=f"R_{username}", name=username)
    for p in perms:
        role.permissions.append(p)
    u = User(username=username, email=f"{username}@demo.local", full_name=username,
             hashed_password=hash_password("passpass123"), is_active=True, is_superuser=False, company_id=cid)
    u.roles.append(role); db.add(u); db.commit()
    return client.post("/api/auth/login", data={"username": username, "password": "passpass123"}).json()["access_token"]


def test_direct_grn_full_flow(client, auth_headers, db, demo):
    cid = demo["company_id"]; _configure(db, cid)
    it = _item(db, cid, "API-D"); wh = _trade_wh(db, cid); db.commit()
    r = client.post("/api/trading/grns", headers=auth_headers, json={
        "supplier_id": str(demo["supplier_id"]), "number": "AGRN-1", "grn_date": GRN_DATE,
        "supplier_invoice_no": "AINV-1", "lines": [
            {"trading_item_id": str(it.id), "warehouse_id": str(wh.id), "qty_received": "100",
             "rate": "100", "tax_code_id": str(demo["tax_id"])}]})
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    assert r.json()["source_type"] == "DIRECT"
    assert client.post(f"/api/trading/grns/{gid}/submit", headers=auth_headers).status_code == 200
    ap = client.post(f"/api/trading/grns/{gid}/approve", headers=auth_headers)
    assert ap.status_code == 200 and ap.json()["status"] == "APPROVED"
    assert ap.json()["voucher_number"] is not None


def test_po_based_flow_and_outstanding(client, auth_headers, db, demo):
    cid = demo["company_id"]; _configure(db, cid)
    it = _item(db, cid, "API-PO"); wh = _trade_wh(db, cid, "WH-API-PO"); db.commit()
    po = client.post("/api/trading/purchase-orders", headers=auth_headers, json={
        "supplier_id": str(demo["supplier_id"]), "number": "APO-1", "order_date": GRN_DATE,
        "lines": [{"trading_item_id": str(it.id), "qty_ordered": "100", "rate": "50"}]}).json()
    po_id = po["id"]; pol_id = po["lines"][0]["id"]
    assert client.post(f"/api/trading/purchase-orders/{po_id}/approve", headers=auth_headers).status_code == 200
    g = client.post("/api/trading/grns", headers=auth_headers, json={
        "supplier_id": str(demo["supplier_id"]), "number": "APO-GRN-1", "grn_date": GRN_DATE,
        "supplier_invoice_no": "APO-INV-1", "purchase_order_id": po_id, "lines": [
            {"trading_item_id": str(it.id), "warehouse_id": str(wh.id), "qty_received": "60",
             "rate": "50", "purchase_order_line_id": pol_id}]})
    assert g.status_code == 201 and g.json()["source_type"] == "PO"
    gid = g.json()["id"]
    client.post(f"/api/trading/grns/{gid}/submit", headers=auth_headers)
    client.post(f"/api/trading/grns/{gid}/approve", headers=auth_headers)
    out = client.get(f"/api/trading/purchase-orders/{po_id}/outstanding", headers=auth_headers).json()
    assert out["lines"][0]["outstanding"] == "40.000000" and out["status"] == "PARTIAL"


def test_approve_blocked_without_mappings_returns_409(client, auth_headers, db, demo):
    cid = demo["company_id"]   # NOT configured
    it = _item(db, cid, "API-NA"); wh = _trade_wh(db, cid, "WH-API-NA"); db.commit()
    g = client.post("/api/trading/grns", headers=auth_headers, json={
        "supplier_id": str(demo["supplier_id"]), "number": "AGRN-NA", "grn_date": GRN_DATE,
        "supplier_invoice_no": "AINV-NA", "lines": [
            {"trading_item_id": str(it.id), "warehouse_id": str(wh.id), "qty_received": "10", "rate": "5"}]})
    gid = g.json()["id"]
    client.post(f"/api/trading/grns/{gid}/submit", headers=auth_headers)
    ap = client.post(f"/api/trading/grns/{gid}/approve", headers=auth_headers)
    assert ap.status_code == 409
    assert "not configured" in ap.json()["detail"].lower()


def test_duplicate_invoice_returns_409(client, auth_headers, db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "API-DUP"); wh = _trade_wh(db, cid, "WH-API-DUP"); db.commit()
    body = {"supplier_id": str(demo["supplier_id"]), "number": "AGRN-DUP1", "grn_date": GRN_DATE,
            "supplier_invoice_no": "DUPINV", "lines": [
                {"trading_item_id": str(it.id), "warehouse_id": str(wh.id), "qty_received": "5", "rate": "1"}]}
    assert client.post("/api/trading/grns", headers=auth_headers, json=body).status_code == 201
    body2 = dict(body, number="AGRN-DUP2")
    assert client.post("/api/trading/grns", headers=auth_headers, json=body2).status_code == 409


def test_over_receipt_without_permission_blocked(client, db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "API-OVR"); wh = _trade_wh(db, cid, "WH-API-OVR"); db.commit()
    # a purchase.manage user WITHOUT trading.over_receive
    tok = _user_token(client, db, cid, "pman", ["purchase.manage", "master.read"])
    h = {"Authorization": f"Bearer {tok}"}
    po = client.post("/api/trading/purchase-orders", headers=h, json={
        "supplier_id": str(demo["supplier_id"]), "number": "OVR-PO", "order_date": GRN_DATE,
        "lines": [{"trading_item_id": str(it.id), "qty_ordered": "100", "rate": "50"}]}).json()
    client.post(f"/api/trading/purchase-orders/{po['id']}/approve", headers=h)
    r = client.post("/api/trading/grns", headers=h, json={
        "supplier_id": str(demo["supplier_id"]), "number": "OVR-GRN", "grn_date": GRN_DATE,
        "supplier_invoice_no": "OVR-INV", "purchase_order_id": po["id"], "lines": [
            {"trading_item_id": str(it.id), "warehouse_id": str(wh.id), "qty_received": "150",
             "rate": "50", "purchase_order_line_id": po["lines"][0]["id"]}]})
    assert r.status_code == 422          # over-receipt not authorised for this user


def test_create_requires_permission(client, db, demo):
    cid = demo["company_id"]
    tok = _user_token(client, db, cid, "reader", ["report.read"])
    h = {"Authorization": f"Bearer {tok}"}
    r = client.post("/api/trading/grns", headers=h, json={
        "supplier_id": str(demo["supplier_id"]), "number": "X", "grn_date": GRN_DATE,
        "supplier_invoice_no": "X", "lines": [
            {"trading_item_id": str(uuid.uuid4()), "warehouse_id": str(uuid.uuid4()), "qty_received": "1"}]})
    assert r.status_code == 403
