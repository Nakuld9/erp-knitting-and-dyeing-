"""End-to-end proof that acting-company switching changes the actual dataset.

A non-superuser who is a member of two companies creates a customer in each via
``X-Company-Id`` and then reads back a company-scoped list. The test asserts:
- data created under company A is visible only under company A, and B under B
  (switching changes what the API returns — not a cosmetic label);
- a company the user is NOT a member of is rejected (403);
- these go through the real routes, which still read ``user.company_id`` — now
  resolved to the acting company by ``get_current_user``.
"""
from __future__ import annotations

from sqlalchemy import select

from app.core.security import hash_password
from app.models.core import Company, Role, User
from app.models.tenancy import UserCompanyMembership


def _login(client, username, password):
    r = client.post("/api/auth/login", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _make_multi_company_user(db):
    company_a = db.execute(select(Company).where(Company.code == "DEMO")).scalar_one()
    company_b = Company(code="CO-B", name="Company B")
    company_c = Company(code="CO-C", name="Company C (not a member)")
    db.add_all([company_b, company_c])
    # role with master.read + master.write
    role = db.execute(select(Role).where(Role.code == "PURCHASE_MANAGER")).scalar_one()
    u = User(username="switcher", email="switcher@demo.local", full_name="Switcher",
             hashed_password=hash_password("secret12"), is_active=True, is_superuser=False,
             company_id=company_a.id)
    u.roles.append(role)
    db.add(u)
    db.flush()
    db.add_all([
        UserCompanyMembership(user_id=u.id, company_id=company_a.id, is_default=True),
        UserCompanyMembership(user_id=u.id, company_id=company_b.id),
    ])
    db.commit()
    return company_a, company_b, company_c


def test_switching_company_changes_dataset(client, db):
    company_a, company_b, company_c = _make_multi_company_user(db)
    headers = _login(client, "switcher", "secret12")

    def h(company_id):
        return {**headers, "X-Company-Id": str(company_id)}

    # create a customer in each company via the acting-company header
    ra = client.post("/api/masters/customers", json={"code": "CUST-A", "name": "Alpha"}, headers=h(company_a.id))
    assert ra.status_code == 201, ra.text
    rb = client.post("/api/masters/customers", json={"code": "CUST-B", "name": "Beta"}, headers=h(company_b.id))
    assert rb.status_code == 201, rb.text

    # listing under company A shows only A's customer
    la = client.get("/api/masters/customers", headers=h(company_a.id))
    codes_a = {c["code"] for c in la.json()}
    assert "CUST-A" in codes_a
    assert "CUST-B" not in codes_a

    # listing under company B shows only B's customer
    lb = client.get("/api/masters/customers", headers=h(company_b.id))
    codes_b = {c["code"] for c in lb.json()}
    assert "CUST-B" in codes_b
    assert "CUST-A" not in codes_b


def test_unauthorized_company_rejected(client, db):
    company_a, company_b, company_c = _make_multi_company_user(db)
    headers = _login(client, "switcher", "secret12")
    # company C: user is not a member -> 403 on any scoped call
    r = client.get("/api/masters/customers", headers={**headers, "X-Company-Id": str(company_c.id)})
    assert r.status_code == 403, r.text


def test_default_company_used_without_header(client, db):
    """No X-Company-Id falls back to the user's default membership (company A)."""
    company_a, company_b, company_c = _make_multi_company_user(db)
    headers = _login(client, "switcher", "secret12")
    # create under A explicitly, then read with NO header -> should see A's data
    client.post("/api/masters/customers", json={"code": "CUST-A", "name": "Alpha"},
                headers={**headers, "X-Company-Id": str(company_a.id)})
    r = client.get("/api/masters/customers", headers=headers)
    assert r.status_code == 200, r.text
    assert "CUST-A" in {c["code"] for c in r.json()}


def test_legacy_single_company_user_unaffected(client, auth_headers):
    """The seeded admin (no memberships beyond its own company) still works with
    no header — backward compatibility for existing single-company users."""
    r = client.get("/api/masters/customers", headers=auth_headers)
    assert r.status_code == 200, r.text
