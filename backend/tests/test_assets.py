"""Fixed assets: capitalisation, SLM/WDV depreciation runs, disposal.

Every depreciation and disposal posts a balanced voucher; a run is idempotent
on its period and an asset never depreciates below its salvage value.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.assets import AssetStatus
from app.services import assets as svc


def _asset(db, company, **kw):
    defaults = dict(code="KM-01", name="Knitting Machine 1", category="Knitting Machine",
                    asset_account_code="1531", acquisition_date=date(2026, 11, 1),
                    cost="120000", salvage=0, method="SLM", useful_life_months=60)
    defaults.update(kw)
    a = svc.capitalize_asset(db, company_id=company.id, **defaults)
    db.commit()
    return a


def test_capitalisation_posts_balanced_voucher(db, company):
    a = _asset(db, company)
    from app.services.accounting import posting_engine as pe
    v = pe.find_existing(db, company.id, "FA:KM-01")
    assert v is not None and v.total_debit == v.total_credit == Decimal("120000.00")
    # Dr the asset account, Cr the funding account.
    assert a.posting_reference == "FA:KM-01"


def test_slm_monthly_charge_and_balanced_run(db, company):
    a = _asset(db, company, cost="120000", useful_life_months=60)   # 2,000 / month
    res = svc.run_depreciation(db, company_id=company.id, period_month=date(2026, 11, 30))
    db.commit()
    assert res["assets_charged"] == 1
    assert res["total"] == Decimal("2000.00")
    v = res["voucher"]
    assert v.total_debit == v.total_credit == Decimal("2000.00")
    db.refresh(a)
    assert a.accumulated_depreciation == Decimal("2000.00")


def test_depreciation_run_is_idempotent(db, company):
    _asset(db, company)
    r1 = svc.run_depreciation(db, company_id=company.id, period_month=date(2026, 11, 15))
    db.commit()
    r2 = svc.run_depreciation(db, company_id=company.id, period_month=date(2026, 11, 2))
    db.commit()
    assert r2["already_posted"] is True
    assert r1["voucher"].id == r2["voucher"].id


def test_slm_never_below_salvage(db, company):
    # cost 1000, salvage 100, life 2 -> 450/month; third month charges nothing.
    a = _asset(db, company, code="FX-1", cost="1000", salvage="100", useful_life_months=2)
    svc.run_depreciation(db, company_id=company.id, period_month=date(2026, 11, 30)); db.commit()
    svc.run_depreciation(db, company_id=company.id, period_month=date(2026, 12, 31)); db.commit()
    db.refresh(a)
    assert a.accumulated_depreciation == Decimal("900.00")          # book == salvage 100
    r3 = svc.run_depreciation(db, company_id=company.id, period_month=date(2027, 1, 31)); db.commit()
    assert r3["total"] == Decimal("0.00") and r3["voucher"] is None
    db.refresh(a)
    assert a.accumulated_depreciation == Decimal("900.00")


def test_wdv_reducing_balance(db, company):
    # cost 100000, 15% annual -> 1250 the first month.
    a = _asset(db, company, code="WDV-1", cost="100000", method="WDV",
               useful_life_months=None, dep_rate_pct=Decimal("15"))
    res = svc.run_depreciation(db, company_id=company.id, period_month=date(2026, 11, 30)); db.commit()
    assert res["total"] == Decimal("1250.00")
    db.refresh(a)
    assert a.accumulated_depreciation == Decimal("1250.00")


def test_disposal_books_gain_and_balances(db, company):
    a = _asset(db, company, code="DSP-1", cost="10000", salvage=0, useful_life_months=10)
    svc.run_depreciation(db, company_id=company.id, period_month=date(2026, 11, 30)); db.commit()
    db.refresh(a)
    assert a.accumulated_depreciation == Decimal("1000.00")          # book 9000
    res = svc.dispose_asset(db, company_id=company.id, asset=a,
                            disposal_date=date(2026, 12, 1), proceeds="9500")
    db.commit()
    assert res["gain_loss"] == Decimal("500.00")                     # 9500 - 9000
    v = res["voucher"]
    assert v.total_debit == v.total_credit                           # balanced
    db.refresh(a)
    assert a.status == AssetStatus.DISPOSED and a.disposal_date == date(2026, 12, 1)


def test_cannot_dispose_twice(db, company):
    a = _asset(db, company, code="DSP-2", cost="5000", useful_life_months=10)
    svc.dispose_asset(db, company_id=company.id, asset=a, disposal_date=date(2026, 12, 1), proceeds="4000")
    db.commit()
    try:
        svc.dispose_asset(db, company_id=company.id, asset=a, disposal_date=date(2026, 12, 2), proceeds="1")
        assert False, "expected AssetError"
    except svc.AssetError:
        db.rollback()


def test_asset_api_roundtrip(client, auth_headers, company):
    body = {"code": "API-1", "name": "Dyeing Jet", "category": "Dyeing Machine",
            "asset_account_code": "1532", "acquisition_date": "2026-11-01",
            "cost": "240000", "method": "SLM", "useful_life_months": 120}
    r = client.post("/api/assets", json=body, headers=auth_headers)
    assert r.status_code == 201, r.text
    assert r.json()["book_value"] == "240000.00"

    run = client.post("/api/assets/depreciation-runs", json={"period_month": "2026-11-30"}, headers=auth_headers)
    assert run.status_code == 201, run.text
    assert run.json()["assets_charged"] >= 1
    assert Decimal(run.json()["total"]) >= Decimal("2000.00")       # 240000/120 = 2000

    lst = client.get("/api/assets", headers=auth_headers).json()
    assert any(x["code"] == "API-1" for x in lst)
