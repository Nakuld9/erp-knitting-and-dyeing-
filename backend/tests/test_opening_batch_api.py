"""Stage 6F-P1 — opening-balance batch API tests.

Covers the endpoint surface, strict request schemas (no client-supplied status /
company / approver), permission enforcement (opening.manage vs opening.read),
maker-checker across two real users, self-approval rejection, and factory
authorization.
"""
from __future__ import annotations

from sqlalchemy import select

from app.core.security import hash_password
from app.models.core import Branch, Permission, Role, User

ED = "2026-11-15"


def _mk_user(db, cid, username, perms):
    role = Role(code=f"R_{username}", name=username)
    for code in perms:
        p = db.execute(select(Permission).where(Permission.code == code)).scalar_one()
        role.permissions.append(p)
    u = User(username=username, email=f"{username}@demo.local", full_name=username.title(),
             hashed_password=hash_password("passpass123"), is_active=True,
             is_superuser=False, company_id=cid)
    u.roles.append(role)
    db.add(u)
    db.flush()
    return u


def _login(client, username):
    tok = client.post("/api/auth/login",
                      data={"username": username, "password": "passpass123"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


# ------------------------------ happy path ----------------------------------

def test_create_list_get_update(client, db, company, auth_headers):
    r = client.post("/api/opening/batches", headers=auth_headers,
                    json={"batch_type": "YARN", "effective_date": ED, "description": "opening yarn"})
    assert r.status_code == 201, r.text
    bid = r.json()["id"]
    assert r.json()["status"] == "DRAFT" and r.json()["batch_number"]

    lst = client.get("/api/opening/batches", headers=auth_headers).json()
    assert lst["total"] == 1 and lst["items"][0]["id"] == bid

    got = client.get(f"/api/opening/batches/{bid}", headers=auth_headers)
    assert got.status_code == 200 and got.json()["description"] == "opening yarn"

    upd = client.patch(f"/api/opening/batches/{bid}", headers=auth_headers,
                       json={"description": "edited"})
    assert upd.status_code == 200 and upd.json()["description"] == "edited"


def test_transitions_via_endpoints(client, db, company, auth_headers):
    maker = _mk_user(db, company.id, "ob_maker", ["opening.manage", "opening.read"])
    checker = _mk_user(db, company.id, "ob_checker", ["opening.manage", "opening.read"])
    db.commit()
    mh, ch = _login(client, "ob_maker"), _login(client, "ob_checker")

    bid = client.post("/api/opening/batches", headers=mh,
                      json={"batch_type": "CUSTOMER_BALANCES", "effective_date": ED}).json()["id"]
    assert client.post(f"/api/opening/batches/{bid}/submit", headers=mh).status_code == 200
    # Checker (a different user) approves and closes.
    ap = client.post(f"/api/opening/batches/{bid}/approve", headers=ch)
    assert ap.status_code == 200 and ap.json()["status"] == "APPROVED"
    cl = client.post(f"/api/opening/batches/{bid}/close", headers=ch)
    assert cl.status_code == 200 and cl.json()["status"] == "CLOSED"


def test_cancel_endpoint(client, db, company, auth_headers):
    bid = client.post("/api/opening/batches", headers=auth_headers,
                      json={"batch_type": "WIP", "effective_date": ED}).json()["id"]
    r = client.post(f"/api/opening/batches/{bid}/cancel", headers=auth_headers,
                    json={"reason": "created in error"})
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"


# --------------------------- maker-checker ----------------------------------

def test_self_approval_rejected(client, db, company, auth_headers):
    # A non-stock batch type exercises the pure P1 lifecycle (no P2 line
    # requirement). P2 self-approval on a stock batch is covered separately in
    # test_opening_stock_api.py::test_self_approval_blocked_api.
    bid = client.post("/api/opening/batches", headers=auth_headers,
                      json={"batch_type": "CUSTOMER_BALANCES", "effective_date": ED}).json()["id"]
    client.post(f"/api/opening/batches/{bid}/submit", headers=auth_headers)
    # Same user tries to approve their own submission.
    r = client.post(f"/api/opening/batches/{bid}/approve", headers=auth_headers)
    assert r.status_code == 409, r.text


# ----------------------------- strict schema --------------------------------

def test_client_cannot_supply_status_or_company(client, db, company, auth_headers):
    r = client.post("/api/opening/batches", headers=auth_headers,
                    json={"batch_type": "YARN", "effective_date": ED, "status": "APPROVED"})
    assert r.status_code == 422, r.text
    r2 = client.post("/api/opening/batches", headers=auth_headers,
                     json={"batch_type": "YARN", "effective_date": ED,
                           "company_id": str(company.id)})
    assert r2.status_code == 422, r2.text
    r3 = client.post("/api/opening/batches", headers=auth_headers,
                     json={"batch_type": "YARN", "effective_date": ED, "approved_by": "x"})
    assert r3.status_code == 422, r3.text


def test_patch_rejects_unexpected_fields(client, db, company, auth_headers):
    bid = client.post("/api/opening/batches", headers=auth_headers,
                      json={"batch_type": "YARN", "effective_date": ED}).json()["id"]
    r = client.patch(f"/api/opening/batches/{bid}", headers=auth_headers,
                     json={"status": "APPROVED"})
    assert r.status_code == 422, r.text


# ------------------------------ permissions ---------------------------------

def test_manage_permission_required_to_create(client, db, company, auth_headers):
    _mk_user(db, company.id, "ob_reader", ["opening.read"])
    db.commit()
    rh = _login(client, "ob_reader")
    # Read-only user can list but not create.
    assert client.get("/api/opening/batches", headers=rh).status_code == 200
    r = client.post("/api/opening/batches", headers=rh,
                    json={"batch_type": "YARN", "effective_date": ED})
    assert r.status_code == 403, r.text


def test_no_permission_cannot_read(client, db, company, auth_headers):
    _mk_user(db, company.id, "ob_none", ["master.read"])
    db.commit()
    nh = _login(client, "ob_none")
    assert client.get("/api/opening/batches", headers=nh).status_code == 403


def test_factory_authorization_enforced(client, db, company, auth_headers):
    factory = Branch(company_id=company.id, code="OBF1", name="Opening Factory 1")
    db.add(factory)
    # A user with opening.manage but no factory.access_all / membership.
    _mk_user(db, company.id, "ob_nofactory", ["opening.manage", "opening.read"])
    db.commit()
    fh = _login(client, "ob_nofactory")
    r = client.post("/api/opening/batches", headers=fh,
                    json={"batch_type": "YARN", "effective_date": ED,
                          "factory_id": str(factory.id)})
    assert r.status_code == 403, r.text


# ------------------------------ route surface -------------------------------

def test_route_surface(client, auth_headers):
    schema = client.get("/openapi.json").json()
    for p in ["/api/opening/batches", "/api/opening/batches/{batch_id}",
              "/api/opening/batches/{batch_id}/submit",
              "/api/opening/batches/{batch_id}/approve",
              "/api/opening/batches/{batch_id}/close",
              "/api/opening/batches/{batch_id}/cancel"]:
        assert p in schema["paths"], p
