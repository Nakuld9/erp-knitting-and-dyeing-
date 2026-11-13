"""Factory-level authorization on the Fabric BOM production-order API.

A production order can be owned by a factory. A user may only create/read/issue
against a factory they have explicit access to (or ``factory.access_all`` /
superuser). ``factory_id`` and ``warehouse_id`` from the client are never
trusted — they are validated against the acting company and the user's access.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.core.security import hash_password
from app.models.core import Branch, Company, Role, User
from app.models.masters import Warehouse
from app.models.tenancy import UserCompanyMembership, UserFactoryMembership
from app.services.bom import service as bom


def _login(client, u, p):
    r = client.post("/api/auth/login", data={"username": u, "password": p})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _restricted_user(db, company_id, factory_id, username):
    """A PRODUCTION_MANAGER (bom.* + production.manage, NOT factory.access_all)
    with company membership and access to exactly one factory."""
    role = db.execute(select(Role).where(Role.code == "PRODUCTION_MANAGER")).scalar_one()
    u = User(username=username, email=f"{username}@demo.local", full_name=username,
             hashed_password=hash_password("secret12"), is_active=True, is_superuser=False,
             company_id=company_id)
    u.roles.append(role)
    db.add(u)
    db.flush()
    db.add_all([
        UserCompanyMembership(user_id=u.id, company_id=company_id, is_default=True),
        UserFactoryMembership(user_id=u.id, company_id=company_id, branch_id=factory_id),
    ])
    db.flush()
    return u


def _approved_bom(db, demo):
    b = bom.create_bom(db, company_id=demo["company_id"], code="FB-AUTHZ", name="authz")
    v = b.versions[0]
    bom.set_yarns(db, v.id, [{"yarn_id": demo["yarn_id"], "blend_percent": Decimal("100"),
                              "std_consumption_per_kg": Decimal("1.0"), "std_unit_cost": Decimal("240")}])
    bom.approve_version(db, version_id=v.id, approved_by=None, effective_from=date(2026, 4, 1))
    return b


def _setup(db, demo):
    cid = demo["company_id"]
    f1 = Branch(company_id=cid, code="AF1", name="Authz Factory 1")
    f2 = Branch(company_id=cid, code="AF2", name="Authz Factory 2")
    db.add_all([f1, f2]); db.flush()
    wh1 = Warehouse(company_id=cid, branch_id=f1.id, code="WH-AF1", name="wh f1")
    wh2 = Warehouse(company_id=cid, branch_id=f2.id, code="WH-AF2", name="wh f2")
    db.add_all([wh1, wh2]); db.flush()
    b = _approved_bom(db, demo)
    _restricted_user(db, cid, f1.id, "pm_f1")
    _restricted_user(db, cid, f2.id, "pm_f2")
    db.commit()
    return b, f1, f2, wh1, wh2


def _order_body(b, **extra):
    body = {"number": "PO-AZ-" + uuid.uuid4().hex[:6], "planned_qty_kg": "10", "bom_id": str(b.id),
            "order_date": "2026-06-01"}
    body.update(extra)
    return body


def test_create_order_in_own_factory_allowed(client, demo, db):
    b, f1, f2, wh1, wh2 = _setup(db, demo)
    h = _login(client, "pm_f1", "secret12")
    r = client.post("/api/bom/orders", headers=h, json=_order_body(b, factory_id=str(f1.id)))
    assert r.status_code == 201, r.text
    assert r.json()["factory_id"] == str(f1.id)


def test_create_order_in_other_factory_forbidden(client, demo, db):
    b, f1, f2, wh1, wh2 = _setup(db, demo)
    h = _login(client, "pm_f1", "secret12")   # only F1
    r = client.post("/api/bom/orders", headers=h, json=_order_body(b, factory_id=str(f2.id)))
    assert r.status_code == 403, r.text


def test_create_order_forged_factory_not_found(client, demo, db):
    b, f1, f2, wh1, wh2 = _setup(db, demo)
    h = _login(client, "pm_f1", "secret12")
    r = client.post("/api/bom/orders", headers=h, json=_order_body(b, factory_id=str(uuid.uuid4())))
    assert r.status_code == 404, r.text


def test_create_order_warehouse_factory_mismatch_forbidden(client, demo, db):
    b, f1, f2, wh1, wh2 = _setup(db, demo)
    h = _login(client, "pm_f1", "secret12")
    # F1 is accessible, but warehouse wh2 belongs to F2 → incompatible
    r = client.post("/api/bom/orders", headers=h,
                    json=_order_body(b, factory_id=str(f1.id), warehouse_id=str(wh2.id)))
    assert r.status_code == 403, r.text


def test_cross_factory_order_read_forbidden(client, demo, db):
    b, f1, f2, wh1, wh2 = _setup(db, demo)
    # pm_f1 creates an F1 order
    h1 = _login(client, "pm_f1", "secret12")
    oid = client.post("/api/bom/orders", headers=h1,
                      json=_order_body(b, factory_id=str(f1.id))).json()["id"]
    # pm_f2 (only F2) cannot read it, see requirements, issue, or view variance
    h2 = _login(client, "pm_f2", "secret12")
    assert client.get(f"/api/bom/orders/{oid}", headers=h2).status_code == 403
    assert client.get(f"/api/bom/orders/{oid}/requirements", headers=h2).status_code == 403
    assert client.get(f"/api/bom/orders/{oid}/variance", headers=h2).status_code == 403
    assert client.post(f"/api/bom/orders/{oid}/complete", headers=h2,
                       json={"produced_qty_kg": "1"}).status_code == 403
    # but pm_f1 can read its own
    assert client.get(f"/api/bom/orders/{oid}", headers=h1).status_code == 200


def test_list_orders_hides_inaccessible_factories(client, demo, db):
    b, f1, f2, wh1, wh2 = _setup(db, demo)
    h1 = _login(client, "pm_f1", "secret12")
    client.post("/api/bom/orders", headers=h1, json=_order_body(b, factory_id=str(f1.id)))
    h2 = _login(client, "pm_f2", "secret12")
    client.post("/api/bom/orders", headers=h2, json=_order_body(b, factory_id=str(f2.id)))
    # pm_f1 sees only F1 orders in the list
    rows = client.get("/api/bom/orders", headers=h1).json()
    assert rows and all(o["factory_id"] == str(f1.id) for o in rows)


def test_forged_order_id_not_found(client, demo, db):
    b, f1, f2, wh1, wh2 = _setup(db, demo)
    h = _login(client, "pm_f1", "secret12")
    assert client.get(f"/api/bom/orders/{uuid.uuid4()}", headers=h).status_code == 404
