"""Opt-in factory data-filtering on operational list endpoints.

With an authorized X-Factory-Id in scope, a factory-owned list returns only that
factory's rows. With no factory in scope, the list is company-wide (nothing is
hidden) — so legacy/unassigned (NULL factory) rows stay visible by default.
"""
from __future__ import annotations

from sqlalchemy import select

from app.core.security import hash_password
from app.models.core import Branch, Company, Role, User
from app.models.masters import Machine
from app.models.tenancy import UserCompanyMembership, UserFactoryMembership


def _login(client, u, p):
    r = client.post("/api/auth/login", data={"username": u, "password": p})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _setup(db):
    a = db.execute(select(Company).where(Company.code == "DEMO")).scalar_one()
    f1 = Branch(company_id=a.id, code="F1", name="Factory 1")
    f2 = Branch(company_id=a.id, code="F2", name="Factory 2")
    db.add_all([f1, f2])
    db.flush()
    db.add_all([
        Machine(company_id=a.id, factory_id=f1.id, machine_type="KNITTING", code="MC-F1", name="M F1"),
        Machine(company_id=a.id, factory_id=f2.id, machine_type="KNITTING", code="MC-F2", name="M F2"),
        Machine(company_id=a.id, factory_id=None, machine_type="KNITTING", code="MC-NULL", name="M legacy"),
    ])
    # user with master.read, company membership to A, factory membership to F1 only
    role = db.execute(select(Role).where(Role.code == "AUDITOR")).scalar_one()  # master.read
    u = User(username="factoryuser", email="fu@demo.local", full_name="FU",
             hashed_password=hash_password("secret12"), is_active=True, is_superuser=False,
             company_id=a.id)
    u.roles.append(role)
    db.add(u)
    db.flush()
    db.add_all([
        UserCompanyMembership(user_id=u.id, company_id=a.id, is_default=True),
        UserFactoryMembership(user_id=u.id, company_id=a.id, branch_id=f1.id),
    ])
    db.commit()
    return a, f1, f2


def test_factory_scope_filters_list(client, db):
    a, f1, f2 = _setup(db)
    headers = _login(client, "factoryuser", "secret12")

    # with F1 in scope -> only F1's machine
    r1 = client.get("/api/masters/machines", headers={**headers, "X-Factory-Id": str(f1.id)})
    assert r1.status_code == 200, r1.text
    codes = {m["code"] for m in r1.json()}
    assert codes == {"MC-F1"}


def test_no_factory_scope_shows_all(client, db):
    a, f1, f2 = _setup(db)
    headers = _login(client, "factoryuser", "secret12")
    # no X-Factory-Id -> company-wide, includes the NULL (legacy) machine
    r = client.get("/api/masters/machines", headers=headers)
    assert r.status_code == 200, r.text
    codes = {m["code"] for m in r.json()}
    assert {"MC-F1", "MC-F2", "MC-NULL"}.issubset(codes)


def test_unauthorized_factory_rejected(client, db):
    a, f1, f2 = _setup(db)
    headers = _login(client, "factoryuser", "secret12")
    # user has no membership for F2 -> requesting it is rejected (rule 3)
    r = client.get("/api/masters/machines", headers={**headers, "X-Factory-Id": str(f2.id)})
    assert r.status_code == 403, r.text
