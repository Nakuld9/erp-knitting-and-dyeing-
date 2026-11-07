"""Operability: master creation, period close/reopen, user admin — all via API."""
from __future__ import annotations


def test_create_masters_via_api(client, auth_headers, demo):
    assert client.post("/api/masters/tax-codes", headers=auth_headers, json={
        "code": "GST5", "name": "GST 5%", "cgst_rate": "2.5", "sgst_rate": "2.5",
        "igst_rate": "5", "hsn_sac": "5208"}).status_code == 201
    assert client.post("/api/masters/chemicals", headers=auth_headers, json={
        "code": "AUX-01", "name": "Levelling Agent", "is_dye": False,
        "standard_cost": "80"}).status_code == 201
    assert client.post("/api/masters/machines", headers=auth_headers, json={
        "machine_type": "FINISHING", "code": "STENTER-1", "name": "Stenter 1"}).status_code == 201
    assert client.post("/api/masters/shades", headers=auth_headers, json={
        "code": "SH-RED", "name": "Crimson"}).status_code == 201
    assert client.post("/api/masters/fabric-types", headers=auth_headers, json={
        "code": "RIB-200", "name": "Rib 200", "gsm": "200"}).status_code == 201
    assert client.post("/api/masters/yarns", headers=auth_headers, json={
        "code": "Y-40S", "name": "40s Combed"}).status_code == 201

    # they show up in lookup lists
    assert any(c["code"] == "AUX-01" for c in client.get("/api/masters/chemicals", headers=auth_headers).json())
    assert any(m["code"] == "STENTER-1" for m in client.get("/api/masters/machines", headers=auth_headers).json())
    # duplicate rejected + bad enum rejected
    assert client.post("/api/masters/yarns", headers=auth_headers, json={"code": "Y-40S", "name": "x"}).status_code == 409
    assert client.post("/api/masters/machines", headers=auth_headers,
                       json={"machine_type": "NOPE", "code": "X", "name": "x"}).status_code == 422


def test_period_close_and_reopen(client, auth_headers, demo):
    periods = client.get("/api/admin/periods", headers=auth_headers).json()
    nov = next(p for p in periods if p["code"] == "2026-11")
    assert nov["status"] == "OPEN"
    closed = client.post(f"/api/admin/periods/{nov['id']}/close", headers=auth_headers).json()
    assert closed["status"] == "CLOSED"
    # posting into a closed period is blocked
    bad = client.post("/api/vouchers", headers=auth_headers, json={
        "voucher_type_code": "JV", "voucher_date": "2026-11-07", "posting_reference": "CLOSED:1",
        "lines": [{"account_code": "1100", "debit": "100", "credit": "0"},
                  {"account_code": "2010", "debit": "0", "credit": "100"}]})
    assert bad.status_code == 422
    reopened = client.post(f"/api/admin/periods/{nov['id']}/reopen", headers=auth_headers).json()
    assert reopened["status"] == "OPEN"


def test_user_and_role_admin(client, auth_headers, demo):
    roles = client.get("/api/admin/roles", headers=auth_headers).json()
    assert any(r["code"] == "ACCOUNTANT" for r in roles)
    created = client.post("/api/admin/users", headers=auth_headers, json={
        "username": "acct1", "email": "acct1@demo.local", "full_name": "Priya",
        "password": "acct12345", "role_codes": ["ACCOUNTANT"]})
    assert created.status_code == 201
    uid = created.json()["id"]
    assert created.json()["roles"] == ["ACCOUNTANT"]

    users = client.get("/api/admin/users", headers=auth_headers).json()
    assert any(u["username"] == "acct1" for u in users)

    updated = client.put(f"/api/admin/users/{uid}/roles", headers=auth_headers,
                         json={"role_codes": ["AUDITOR"]})
    assert updated.status_code == 200 and updated.json()["roles"] == ["AUDITOR"]

    # the new user can log in and is permission-limited (auditor can't post)
    tok = client.post("/api/auth/login", data={"username": "acct1", "password": "acct12345"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    post = client.post("/api/vouchers", headers=h, json={
        "voucher_type_code": "JV", "voucher_date": "2026-11-07", "posting_reference": "RBAC:2",
        "lines": [{"account_code": "1100", "debit": "100", "credit": "0"},
                  {"account_code": "2010", "debit": "0", "credit": "100"}]})
    assert post.status_code == 403


def test_voucher_create_and_list(client, auth_headers, demo):
    r = client.post("/api/vouchers", headers=auth_headers, json={
        "voucher_type_code": "JV", "voucher_date": "2026-11-07", "posting_reference": "UI:JV-1",
        "narration": "Manual journal",
        "lines": [{"account_code": "6020", "debit": "500", "credit": "0"},
                  {"account_code": "1010", "debit": "0", "credit": "500"}]})
    assert r.status_code == 201
    listed = client.get("/api/vouchers", headers=auth_headers).json()
    assert any(v["number"] == r.json()["number"] for v in listed)


def test_sqlite_pool_selection():
    """Regression: file-backed SQLite must NOT use StaticPool (single shared
    connection), or concurrent requests collide. In-memory keeps StaticPool."""
    from sqlalchemy.pool import NullPool, StaticPool
    from app.core.database import _make_engine
    assert isinstance(_make_engine("sqlite://").pool, StaticPool)          # in-memory (tests)
    file_eng = _make_engine("sqlite:////tmp/_pooltest_x.db")
    assert isinstance(file_eng.pool, NullPool)                             # file (dev/demo)
    file_eng.dispose()
