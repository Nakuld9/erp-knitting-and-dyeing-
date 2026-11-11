"""Inter-company dual-leg posting — atomic, fail-closed, GST by place of supply."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models.accounting import ChartOfAccount, Voucher, VoucherStatus
from app.models.core import Company, Role, User
from app.models.masters import TaxCode
from app.models.tenancy import IntercompanyAccountMapping, UserCompanyMembership
from app.seed.seed import seed_chart_of_accounts, seed_periods
from app.services.accounting.posting_rules import seed_default_posting_rules
from app.services.accounting import intercompany as ic
from app.services.accounting.intercompany import IntercompanyConfigError
from app.services.tax import GstStateError


def _acct(db, company_id, code):
    return db.execute(select(ChartOfAccount).where(
        ChartOfAccount.company_id == company_id, ChartOfAccount.code == code)).scalar_one()


def _seed_company(db, code, state_code="03"):
    c = Company(code=code, name=code, state_code=state_code)
    db.add(c)
    db.flush()
    seed_chart_of_accounts(db, c)
    seed_default_posting_rules(db, c.id)
    seed_periods(db, c, 2026)
    db.commit()
    return c


def _approve_pair(db, a, b, approved=True):
    for (x, y) in [(a, b), (b, a)]:
        db.add(IntercompanyAccountMapping(
            company_id=x.id, counterparty_company_id=y.id,
            due_from_account_id=_acct(db, x.id, "1050").id,
            due_to_account_id=_acct(db, x.id, "2070").id,
            is_approved=approved))
    db.commit()


def _demo(db):
    return db.execute(select(Company).where(Company.code == "DEMO")).scalar_one()


def test_posting_creates_both_balanced_legs(db):
    a = _demo(db)  # state 03
    b = _seed_company(db, "ICP-B", "03")
    _approve_pair(db, a, b)

    res = ic.post_intercompany_charge(
        db, seller_company_id=a.id, buyer_company_id=b.id, taxable=Decimal("1000"),
        voucher_date=date(2026, 4, 15), reference="T1")
    assert res["gross"] == "1000.00"  # no tax code -> no GST

    sv = db.execute(select(Voucher).where(Voucher.company_id == a.id,
                    Voucher.posting_reference == "INTERCO:T1:SELL")).scalar_one()
    bv = db.execute(select(Voucher).where(Voucher.company_id == b.id,
                    Voucher.posting_reference == "INTERCO:T1:BUY")).scalar_one()
    assert sv.status == VoucherStatus.POSTED.value and sv.total_debit == sv.total_credit
    assert bv.status == VoucherStatus.POSTED.value and bv.total_debit == bv.total_credit
    # seller debits due-from (1050); buyer credits due-to (2070)
    due_from = _acct(db, a.id, "1050")
    assert any(l.account_id == due_from.id and l.debit == Decimal("1000.00") for l in sv.lines)
    due_to = _acct(db, b.id, "2070")
    assert any(l.account_id == due_to.id and l.credit == Decimal("1000.00") for l in bv.lines)


def test_posting_fail_closed_without_approval(db):
    a = _demo(db)
    b = _seed_company(db, "ICP-C", "03")
    _approve_pair(db, a, b, approved=False)  # mappings exist but NOT approved
    with pytest.raises(IntercompanyConfigError):
        ic.post_intercompany_charge(db, seller_company_id=a.id, buyer_company_id=b.id,
                                    taxable=Decimal("500"), voucher_date=date(2026, 4, 15),
                                    reference="T2")


def test_posting_gst_intra_vs_inter(db):
    a = _demo(db)  # state 03
    tax = TaxCode(company_id=a.id, code="GST18", name="GST 18%",
                  cgst_rate=Decimal("9"), sgst_rate=Decimal("9"), igst_rate=Decimal("18"))
    db.add(tax)
    # intra-state buyer (same state 03)
    b_intra = _seed_company(db, "ICP-INTRA", "03")
    _approve_pair(db, a, b_intra)
    db.commit()
    r_intra = ic.post_intercompany_charge(
        db, seller_company_id=a.id, buyer_company_id=b_intra.id, taxable=Decimal("1000"),
        voucher_date=date(2026, 4, 15), reference="GI", tax_code_id=tax.id)
    assert r_intra["intra_state"] is True
    assert r_intra["total_tax"] == "180.00"  # 90 CGST + 90 SGST
    assert r_intra["gross"] == "1180.00"

    # inter-state buyer (different state 07)
    b_inter = _seed_company(db, "ICP-INTER", "07")
    _approve_pair(db, a, b_inter)
    db.commit()
    r_inter = ic.post_intercompany_charge(
        db, seller_company_id=a.id, buyer_company_id=b_inter.id, taxable=Decimal("1000"),
        voucher_date=date(2026, 4, 15), reference="GE", tax_code_id=tax.id)
    assert r_inter["intra_state"] is False
    assert r_inter["total_tax"] == "180.00"  # 180 IGST


def test_posting_fail_closed_on_missing_state(db):
    a = _demo(db)
    b = _seed_company(db, "ICP-NOSTATE", state_code=None)  # buyer has no state code
    _approve_pair(db, a, b)
    tax = TaxCode(company_id=a.id, code="GST5", name="GST 5%",
                  cgst_rate=Decimal("2.5"), sgst_rate=Decimal("2.5"), igst_rate=Decimal("5"))
    db.add(tax)
    db.commit()
    with pytest.raises(GstStateError):
        ic.post_intercompany_charge(db, seller_company_id=a.id, buyer_company_id=b.id,
                                    taxable=Decimal("100"), voucher_date=date(2026, 4, 15),
                                    reference="NS", tax_code_id=tax.id)


# ---- API ----

def _login(client, u, p):
    r = client.post("/api/auth/login", data={"username": u, "password": p})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_api_posting_and_authorization(client, auth_headers, db):
    a = _demo(db)
    b = _seed_company(db, "ICP-API", "03")
    _approve_pair(db, a, b)

    # admin (superuser) can post
    r = client.post("/api/accounting/intercompany-posting",
                    json={"seller_company_id": str(a.id), "buyer_company_id": str(b.id),
                          "taxable": "2000", "voucher_date": "2026-04-15", "reference": "API1"},
                    headers=auth_headers)
    assert r.status_code == 201, r.text
    assert r.json()["seller_voucher"]["company_id"] == str(a.id)

    # a non-super user with voucher.post but authorized only for company A -> 403
    role = db.execute(select(Role).where(Role.code == "ACCOUNTANT")).scalar_one()  # has voucher.post
    u = User(username="icposter", email="icp@demo.local", full_name="ICP",
             hashed_password=hash_password("secret12"), is_active=True, is_superuser=False,
             company_id=a.id)
    u.roles.append(role)
    db.add(u)
    db.flush()
    db.add(UserCompanyMembership(user_id=u.id, company_id=a.id, is_default=True))
    db.commit()
    headers = _login(client, "icposter", "secret12")
    r2 = client.post("/api/accounting/intercompany-posting",
                     json={"seller_company_id": str(a.id), "buyer_company_id": str(b.id),
                           "taxable": "100", "voucher_date": "2026-04-15", "reference": "API2"},
                     headers={**headers, "X-Company-Id": str(a.id)})
    assert r2.status_code == 403, r2.text


def test_elimination_suggestion_maths():
    """Pure elimination maths: A's due-from receivable and B's due-to payable are
    removed by a balanced elimination; each unordered pair emits once."""
    from decimal import Decimal as D
    from app.services.accounting.consolidation import compute_elimination_suggestions

    import uuid as _uuid
    a, b = _uuid.uuid4(), _uuid.uuid4()
    pairs = [
        (a, b, "1050", D("1000.00"), "2070", D("-1000.00")),
        (b, a, "1050", D("-1000.00"), "2070", D("1000.00")),  # reciprocal -> deduped
    ]
    out = compute_elimination_suggestions(pairs)
    triples = {(s["account_code"], s["debit"], s["credit"]) for s in out}
    assert ("1050", "0", "1000.00") in triples    # remove A's due-from receivable
    assert ("2070", "1000.00", "0") in triples     # remove B's due-to payable
    assert len(out) == 2                            # pair emitted once, not twice


def test_elimination_suggestions_api(client, auth_headers, db):
    from app.models.tenancy import Organization
    a = _demo(db)
    b = _seed_company(db, "ICP-ELA", "03")
    org = Organization(code="ELA", name="Elim API Group")
    db.add(org)
    db.flush()
    a.organization_id = org.id
    b.organization_id = org.id
    _approve_pair(db, a, b)
    db.commit()
    r = client.get(f"/api/reports/consolidated/elimination-suggestions?organization_id={org.id}",
                   headers=auth_headers)
    assert r.status_code == 200, r.text
    assert "suggestions" in r.json()
