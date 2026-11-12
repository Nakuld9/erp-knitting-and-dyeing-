"""Inter-company account mapping — configurable, accountant-approved, fail-closed.

Covers the config/approval service and API: a mapping must be explicitly created
with real accounts from the company's own COA, validated by nature (asset /
liability), and APPROVED by an accountant before get_approved_mapping (the guard
every future posting must use) will return it.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models.accounting import ChartOfAccount
from app.models.core import Company, Role, User
from app.models.tenancy import IntercompanyAccountMapping
from app.services.accounting import intercompany as ic
from app.services.accounting.intercompany import IntercompanyConfigError


def _acct(db, company_id, code):
    return db.execute(select(ChartOfAccount).where(
        ChartOfAccount.company_id == company_id, ChartOfAccount.code == code)).scalar_one()


def _setup(db):
    a = db.execute(select(Company).where(Company.code == "DEMO")).scalar_one()
    b = Company(code="IC-B", name="Intercompany B")
    db.add(b)
    db.commit()
    return a, b


def test_create_requires_valid_control_accounts(db):
    a, b = _setup(db)
    asset = _acct(db, a.id, "1050")     # Advances to Customers (ASSET)
    liab = _acct(db, a.id, "2070")      # Short-term Loans (LIABILITY)

    # self-counterparty rejected
    with pytest.raises(IntercompanyConfigError):
        ic.create_mapping(db, company_id=a.id, counterparty_company_id=a.id,
                          due_from_code="1050", due_to_code="2070")
    # due_from must be ASSET (passing the liability as due_from fails)
    with pytest.raises(IntercompanyConfigError):
        ic.create_mapping(db, company_id=a.id, counterparty_company_id=b.id,
                          due_from_code="2070", due_to_code="2070")
    # unknown account id fails
    with pytest.raises(IntercompanyConfigError):
        ic.create_mapping(db, company_id=a.id, counterparty_company_id=b.id,
                          due_from_code="NOPE", due_to_code="2070")


def test_fail_closed_until_approved(db):
    a, b = _setup(db)
    asset = _acct(db, a.id, "1050")
    liab = _acct(db, a.id, "2070")
    m = ic.create_mapping(db, company_id=a.id, counterparty_company_id=b.id,
                          due_from_code="1050", due_to_code="2070")
    assert m.is_approved is False
    # resolver refuses an unapproved mapping (fail-closed)
    with pytest.raises(IntercompanyConfigError):
        ic.get_approved_mapping(db, a.id, b.id)
    # and refuses when no mapping at all
    with pytest.raises(IntercompanyConfigError):
        ic.get_approved_mapping(db, b.id, a.id)

    ic.approve_mapping(db, m.id, uuid.uuid4())
    got = ic.get_approved_mapping(db, a.id, b.id)
    assert got.id == m.id and got.is_approved is True


def test_duplicate_mapping_rejected(db):
    a, b = _setup(db)
    asset = _acct(db, a.id, "1050")
    liab = _acct(db, a.id, "2070")
    ic.create_mapping(db, company_id=a.id, counterparty_company_id=b.id,
                      due_from_code="1050", due_to_code="2070")
    with pytest.raises(IntercompanyConfigError):
        ic.create_mapping(db, company_id=a.id, counterparty_company_id=b.id,
                          due_from_code="1050", due_to_code="2070")


def test_reconciliation_ready_when_both_legs_approved(db):
    a, b = _setup(db)
    # insert both directions directly (reconciliation checks pairing, not accounts)
    aid = _acct(db, a.id, "1050").id
    lid = _acct(db, a.id, "2070").id
    db.add_all([
        IntercompanyAccountMapping(company_id=a.id, counterparty_company_id=b.id,
                                   due_from_account_id=aid, due_to_account_id=lid, is_approved=True),
        IntercompanyAccountMapping(company_id=b.id, counterparty_company_id=a.id,
                                   due_from_account_id=aid, due_to_account_id=lid, is_approved=True),
    ])
    db.commit()
    recon = ic.reconciliation(db)
    assert all(r["ready_for_elimination"] for r in recon)
    assert len(recon) == 2


# ---- API ----

def _login(client, u, p):
    r = client.post("/api/auth/login", data={"username": u, "password": p})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_api_create_approve_reconcile(client, auth_headers, db):
    a, b = _setup(db)
    r = client.post("/api/admin/intercompany-mappings",
                    json={"company_id": str(a.id), "counterparty_company_id": str(b.id),
                          "due_from_account_code": "1050", "due_to_account_code": "2070"},
                    headers=auth_headers)
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    assert r.json()["is_approved"] is False
    # approve (admin superuser has intercompany.approve via '*')
    ra = client.post(f"/api/admin/intercompany-mappings/{mid}/approve", headers=auth_headers)
    assert ra.status_code == 200, ra.text
    assert ra.json()["is_approved"] is True
    rec = client.get("/api/admin/intercompany-reconciliation", headers=auth_headers)
    assert rec.status_code == 200
    assert any(row["id"] == mid for row in rec.json())


def test_api_bad_account_code_rejected(client, auth_headers, db):
    a, b = _setup(db)
    r = client.post("/api/admin/intercompany-mappings",
                    json={"company_id": str(a.id), "counterparty_company_id": str(b.id),
                          "due_from_account_code": "9999", "due_to_account_code": "2070"},
                    headers=auth_headers)
    # unknown account code is a validation error (422) — validated against the
    # company's own chart of accounts inside create_mapping.
    assert r.status_code == 422


def test_api_approve_requires_permission(client, db):
    a, b = _setup(db)
    asset = _acct(db, a.id, "1050")
    liab = _acct(db, a.id, "2070")
    m = ic.create_mapping(db, company_id=a.id, counterparty_company_id=b.id,
                          due_from_code="1050", due_to_code="2070")
    # a user WITHOUT intercompany.approve (AUDITOR) cannot approve
    role = db.execute(select(Role).where(Role.code == "AUDITOR")).scalar_one()
    u = User(username="noapprove", email="na@demo.local", full_name="NA",
             hashed_password=hash_password("secret12"), is_active=True, is_superuser=False,
             company_id=a.id)
    u.roles.append(role)
    db.add(u)
    db.commit()
    headers = _login(client, "noapprove", "secret12")
    r = client.post(f"/api/admin/intercompany-mappings/{m.id}/approve", headers=headers)
    assert r.status_code == 403, r.text
