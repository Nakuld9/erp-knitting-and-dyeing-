"""Group consolidation — read-only aggregation + approved report-only eliminations.

The aggregation/elimination logic is tested with controlled per-company trial
balances (monkeypatched), so the maths is exact and independent of the posting
engine. A second group of tests exercises the API surface (eliminations CRUD +
approval + authorization) against seeded data.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.models.core import Company, User
from app.models.tenancy import ConsolidationElimination, Organization
from app.services.accounting import consolidation as consol
from app.core.security import hash_password


def _row(code, name, atype, cd, cc):
    return {"account_code": code, "account_name": name, "account_type": atype,
            "opening_debit": Decimal("0"), "opening_credit": Decimal("0"),
            "period_debit": Decimal("0"), "period_credit": Decimal("0"),
            "closing_debit": Decimal(cd), "closing_credit": Decimal(cc)}


def _org_with_two_companies(db):
    org = Organization(code="GRP", name="Group")
    db.add(org)
    db.flush()
    a = db.execute(select(Company).where(Company.code == "DEMO")).scalar_one()
    a.organization_id = org.id
    b = Company(code="CON-B", name="Consol B", organization_id=org.id)
    db.add(b)
    db.commit()
    return org, a, b


def test_consolidation_aggregates_and_eliminates(db, monkeypatch):
    org, a, b = _org_with_two_companies(db)

    def fake_tb(_db, company_id, *, date_from=None, date_to=None):
        if company_id == a.id:
            return {"rows": [_row("1000", "Cash", "ASSET", "100", "0"),
                             _row("4000", "Sales", "INCOME", "0", "100")]}
        if company_id == b.id:
            return {"rows": [_row("1000", "Cash", "ASSET", "50", "0"),
                             _row("4000", "Sales", "INCOME", "0", "50")]}
        return {"rows": []}

    monkeypatch.setattr(consol.tb, "trial_balance", fake_tb)

    # balanced elimination: credit Cash 30, debit Sales 30 (approved)
    db.add_all([
        ConsolidationElimination(organization_id=org.id, account_code="1000",
                                 debit=Decimal("0"), credit=Decimal("30"), is_approved=True),
        ConsolidationElimination(organization_id=org.id, account_code="4000",
                                 debit=Decimal("30"), credit=Decimal("0"), is_approved=True),
        # a pending (unapproved) elimination that must NOT be applied
        ConsolidationElimination(organization_id=org.id, account_code="1000",
                                 debit=Decimal("0"), credit=Decimal("999"), is_approved=False),
    ])
    db.commit()

    out = consol.consolidated_trial_balance(db, org.id)
    by_code = {r["account_code"]: r for r in out["rows"]}

    # combined before elimination
    assert by_code["1000"]["combined_debit"] == Decimal("150")
    assert by_code["4000"]["combined_credit"] == Decimal("150")
    # consolidated after the approved elimination only (999 pending ignored)
    assert by_code["1000"]["consolidated_debit"] == Decimal("120")
    assert by_code["4000"]["consolidated_credit"] == Decimal("120")
    assert out["is_balanced"] is True
    assert len(out["eliminations_applied"]) == 2
    assert len(out["eliminations_pending"]) == 1
    assert out["approved"] is False  # a pending elimination remains
    assert {c["code"] for c in out["companies"]} == {"DEMO", "CON-B"}


def test_company_outside_group_excluded(db, monkeypatch):
    org, a, b = _org_with_two_companies(db)
    outsider = Company(code="OUT", name="Outsider")  # no organization_id
    db.add(outsider)
    db.commit()
    members = set(consol.member_company_ids(db, org.id))
    assert a.id in members and b.id in members
    assert outsider.id not in members


# ---- API ----

def _login(client, u, p):
    r = client.post("/api/auth/login", data={"username": u, "password": p})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_elimination_crud_and_approval_api(client, auth_headers, db):
    org, a, b = _org_with_two_companies(db)
    # create (admin.manage)
    r = client.post("/api/admin/consolidation-eliminations",
                    json={"organization_id": str(org.id), "account_code": "1000", "credit": "30"},
                    headers=auth_headers)
    assert r.status_code == 201, r.text
    elim_id = r.json()["id"]
    assert r.json()["is_approved"] is False
    # approve (admin is superuser -> has consolidation.approve via '*')
    ra = client.post(f"/api/admin/consolidation-eliminations/{elim_id}/approve", headers=auth_headers)
    assert ra.status_code == 200, ra.text
    assert ra.json()["is_approved"] is True
    # consolidated report returns structure (read-only)
    rep = client.get(f"/api/reports/consolidated?organization_id={org.id}", headers=auth_headers)
    assert rep.status_code == 200, rep.text
    body = rep.json()
    assert "note" in body and "No entries are posted" in body["note"]
    assert {c["code"] for c in body["companies"]} == {"DEMO", "CON-B"}


def test_consolidation_requires_access_to_all_companies(client, auth_headers, db):
    org, a, b = _org_with_two_companies(db)
    # a non-super user with report.financial but membership only in A
    from app.models.core import Role
    role = db.execute(select(Role).where(Role.code == "AUDITOR")).scalar_one()  # has report.financial
    u = User(username="grpauditor", email="ga@demo.local", full_name="GA",
             hashed_password=hash_password("secret12"), is_active=True, is_superuser=False,
             company_id=a.id)
    u.roles.append(role)
    db.add(u)
    db.flush()
    from app.models.tenancy import UserCompanyMembership
    db.add(UserCompanyMembership(user_id=u.id, company_id=a.id, is_default=True))
    db.commit()
    headers = _login(client, "grpauditor", "secret12")
    # not a member of company B -> group access denied
    r = client.get(f"/api/reports/consolidated?organization_id={org.id}",
                   headers={**headers, "X-Company-Id": str(a.id)})
    assert r.status_code == 403, r.text


def test_consolidated_pnl(db, monkeypatch):
    org, a, b = _org_with_two_companies(db)

    def fake_tb(_db, company_id, *, date_from=None, date_to=None):
        if company_id == a.id:
            return {"rows": [_row("4000", "Sales", "INCOME", "0", "1000"),
                             _row("5000", "COGS", "EXPENSE", "400", "0")]}
        if company_id == b.id:
            return {"rows": [_row("4000", "Sales", "INCOME", "0", "500"),
                             _row("5000", "COGS", "EXPENSE", "100", "0")]}
        return {"rows": []}

    monkeypatch.setattr(consol.tb, "trial_balance", fake_tb)
    out = consol.consolidated_profit_and_loss(db, org.id)
    assert out["total_income"] == Decimal("1500")
    assert out["total_expense"] == Decimal("500")
    assert out["net_profit"] == Decimal("1000")
    assert {c["code"] for c in out["companies"]} == {"DEMO", "CON-B"}


def test_consolidated_pnl_api(client, auth_headers, db):
    org, a, b = _org_with_two_companies(db)
    r = client.get(f"/api/reports/consolidated/pnl?organization_id={org.id}", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "net_profit" in body and "No entries are posted" in body["note"]
