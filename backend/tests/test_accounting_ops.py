"""GST adjustment vouchers and year-end closing."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models.accounting import AccountingPeriod, PeriodStatus
from app.services.accounting import financial_statements as fs
from app.services.accounting import gst_adjustment as gadj
from app.services.accounting import year_end
from tests.helpers import L, post


# ------------------------------ GST adjustments -----------------------------

def test_itc_reversal_balances(db, company):
    v = gadj.post_itc_reversal(db, company_id=company.id, vdate=date(2026, 11, 7),
                               reference="RV1", cgst="500", sgst="500")
    db.commit()
    # Dr reversal expense 1000; Cr input CGST 500 + input SGST 500.
    assert v.total_debit == v.total_credit == Decimal("1000.00")
    assert len(v.lines) == 3


def test_itc_reversal_is_idempotent(db, company):
    v1 = gadj.post_itc_reversal(db, company_id=company.id, vdate=date(2026, 11, 7),
                                reference="RV2", igst="900"); db.commit()
    v2 = gadj.post_itc_reversal(db, company_id=company.id, vdate=date(2026, 11, 7),
                                reference="RV2", igst="900"); db.commit()
    assert v1.id == v2.id


def test_rcm_books_output_and_input(db, company):
    v = gadj.post_rcm(db, company_id=company.id, vdate=date(2026, 11, 7),
                      reference="RCM1", cgst="900", sgst="900")
    db.commit()
    # Dr input CGST+SGST 1800; Cr output CGST+SGST 1800 (net-nil to P&L).
    assert v.total_debit == v.total_credit == Decimal("1800.00")
    assert len(v.lines) == 4


def test_gst_adjustment_rejects_zero(db, company):
    try:
        gadj.post_rcm(db, company_id=company.id, vdate=date(2026, 11, 7), reference="Z", cgst="0")
        assert False, "expected GSTAdjustmentError"
    except gadj.GSTAdjustmentError:
        db.rollback()


# ------------------------------- year-end close -----------------------------

def _book_activity(db, company):
    # income: Dr Bank / Cr Fabric Sales 100000
    post(db, company.id, [L("1020", debit="100000"), L("4010", credit="100000")], ref="YE-INC")
    # expense: Dr Salaries / Cr Bank 30000
    post(db, company.id, [L("6010", debit="30000"), L("1020", credit="30000")], ref="YE-EXP")


def test_year_end_close_transfers_profit(db, company):
    _book_activity(db, company)
    res = year_end.close_year(db, company_id=company.id, fiscal_year="2026-2027")
    db.commit()
    assert res["net_profit"] == Decimal("70000.00")       # 100000 - 30000
    v = res["voucher"]
    assert v.total_debit == v.total_credit                # balanced
    assert res["periods_closed"] >= 1

    # Every period in the year is now locked.
    periods = db.execute(select(AccountingPeriod).where(
        AccountingPeriod.company_id == company.id,
        AccountingPeriod.fiscal_year == "2026-2027")).scalars().all()
    assert all(p.status == PeriodStatus.CLOSED for p in periods)

    # P&L for the year now nets to zero (closing entries offset the activity);
    # the profit sits in Retained Earnings.
    pnl = fs.profit_and_loss(db, company.id, date(2026, 4, 1), date(2027, 3, 31))
    assert pnl["net_profit"] == Decimal("0.00")


def test_year_end_close_is_idempotent(db, company):
    _book_activity(db, company)
    r1 = year_end.close_year(db, company_id=company.id, fiscal_year="2026-2027"); db.commit()
    r2 = year_end.close_year(db, company_id=company.id, fiscal_year="2026-2027"); db.commit()
    assert r2["already_closed"] is True
    assert r1["voucher"].id == r2["voucher"].id
