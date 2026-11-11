"""API tests for the multi-company tenancy endpoints and scope isolation.

Covers the admin management surface (organizations, memberships, GST
registrations) and the request-scope resolver as exercised through
``/api/me/context`` — including rejection of a forged ``X-Company-Id`` for a
non-superuser (the core cross-company isolation guarantee at the API boundary).
"""
from __future__ import annotations

from sqlalchemy import select

from app.core.security import hash_password
from app.models.core import Branch, Company, User
from app.models.tenancy import UserCompanyMembership


def _login(client, username, password):
    r = client.post("/api/auth/login", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_me_context_superuser_sees_companies(client, auth_headers):
    r = client.get("/api/me/context", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["is_superuser"] is True
    codes = {c["code"] for c in body["companies"]}
    assert "DEMO" in codes  # seeded company visible to the superuser


def test_create_organization_and_assign(client, auth_headers, db):
    company = db.execute(select(Company).where(Company.code == "DEMO")).scalar_one()
    r = client.post("/api/admin/organizations",
                    json={"code": "GRP1", "name": "Group One"}, headers=auth_headers)
    assert r.status_code == 201, r.text
    org_id = r.json()["id"]
    r2 = client.post(f"/api/admin/companies/{company.id}/organization",
                     json={"organization_id": org_id}, headers=auth_headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["organization_id"] == org_id


def test_grant_company_membership(client, auth_headers, db):
    company = db.execute(select(Company).where(Company.code == "DEMO")).scalar_one()
    u = User(username="accountant1", email="acc1@demo.local", full_name="Acc One",
             hashed_password=hash_password("secret12"), is_active=True, is_superuser=False)
    db.add(u)
    db.commit()
    r = client.post("/api/admin/memberships/company",
                    json={"user_id": str(u.id), "company_id": str(company.id), "is_default": True},
                    headers=auth_headers)
    assert r.status_code == 201, r.text
    # listing shows it
    r2 = client.get("/api/admin/memberships", headers=auth_headers)
    assert any(m["user_id"] == str(u.id) for m in r2.json()["company"])


def test_create_gst_registration(client, auth_headers, db):
    company = db.execute(select(Company).where(Company.code == "DEMO")).scalar_one()
    r = client.post("/api/admin/gst-registrations",
                    json={"company_id": str(company.id), "gstin": "27AAAAA0000A1Z5",
                          "state_code": "27", "is_primary": True},
                    headers=auth_headers)
    assert r.status_code == 201, r.text
    r2 = client.get(f"/api/admin/gst-registrations?company_id={company.id}", headers=auth_headers)
    assert r2.json()[0]["gstin"] == "27AAAAA0000A1Z5"
    # duplicate rejected
    r3 = client.post("/api/admin/gst-registrations",
                     json={"company_id": str(company.id), "gstin": "27AAAAA0000A1Z5"},
                     headers=auth_headers)
    assert r3.status_code == 409


def test_forged_company_id_rejected_for_scoped_user(client, auth_headers, db):
    """A non-superuser member of company A is rejected for company B's id."""
    company_a = db.execute(select(Company).where(Company.code == "DEMO")).scalar_one()
    company_b = Company(code="OTHER", name="Other Co")
    db.add(company_b)
    u = User(username="scopeduser", email="scoped@demo.local", full_name="Scoped",
             hashed_password=hash_password("secret12"), is_active=True, is_superuser=False)
    db.add(u)
    db.flush()
    db.add(UserCompanyMembership(user_id=u.id, company_id=company_a.id, is_default=True))
    db.commit()

    headers = _login(client, "scopeduser", "secret12")
    # own company → ok
    ok = client.get("/api/me/context", headers={**headers, "X-Company-Id": str(company_a.id)})
    assert ok.status_code == 200, ok.text
    assert ok.json()["active"]["company_id"] == str(company_a.id)
    # forged company → 403
    bad = client.get("/api/me/context", headers={**headers, "X-Company-Id": str(company_b.id)})
    assert bad.status_code == 403, bad.text


def test_invalid_company_header_is_400(client, auth_headers):
    r = client.get("/api/me/context", headers={**auth_headers, "X-Company-Id": "not-a-uuid"})
    assert r.status_code == 400


def test_gstin_format_validated(client, auth_headers, db):
    company = db.execute(select(Company).where(Company.code == "DEMO")).scalar_one()
    r = client.post("/api/admin/gst-registrations",
                    json={"company_id": str(company.id), "gstin": "NOT-A-GSTIN-123"},
                    headers=auth_headers)
    assert r.status_code == 422  # pydantic validation


def test_one_active_gstin_per_company(client, auth_headers, db):
    company = db.execute(select(Company).where(Company.code == "DEMO")).scalar_one()
    r1 = client.post("/api/admin/gst-registrations",
                     json={"company_id": str(company.id), "gstin": "27AAAAA0000A1Z5", "is_primary": True},
                     headers=auth_headers)
    assert r1.status_code == 201, r1.text
    # a SECOND active registration (different GSTIN) is rejected for the same company
    r2 = client.post("/api/admin/gst-registrations",
                     json={"company_id": str(company.id), "gstin": "29BBBBB1111B2Z4"},
                     headers=auth_headers)
    assert r2.status_code == 409, r2.text


def test_gst_registration_company_isolation(client, auth_headers, db):
    """One company's GST registration never appears in another company's list."""
    company_a = db.execute(select(Company).where(Company.code == "DEMO")).scalar_one()
    company_b = Company(code="GCO-B", name="GST Co B")
    db.add(company_b)
    db.commit()
    client.post("/api/admin/gst-registrations",
                json={"company_id": str(company_a.id), "gstin": "27AAAAA0000A1Z5"}, headers=auth_headers)
    client.post("/api/admin/gst-registrations",
                json={"company_id": str(company_b.id), "gstin": "29BBBBB1111B2Z4"}, headers=auth_headers)

    a_list = client.get(f"/api/admin/gst-registrations?company_id={company_a.id}", headers=auth_headers).json()
    b_list = client.get(f"/api/admin/gst-registrations?company_id={company_b.id}", headers=auth_headers).json()
    a_gstins = {g["gstin"] for g in a_list}
    b_gstins = {g["gstin"] for g in b_list}
    assert "27AAAAA0000A1Z5" in a_gstins and "29BBBBB1111B2Z4" not in a_gstins
    assert "29BBBBB1111B2Z4" in b_gstins and "27AAAAA0000A1Z5" not in b_gstins
