"""Cross-database inter-company saga: idempotency, completion, compensation, flag.

The router falls back to the single test database, so both legs post there; this
exercises the saga LOGIC (status transitions, idempotency, compensation,
reconciliation). The true two-PostgreSQL-database run is in the PG validation.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.accounting import ChartOfAccount, Voucher
from app.models.core import Company
from app.models.tenancy import IntercompanyAccountMapping, IntercompanyTxnStatus
from app.seed.seed import seed_chart_of_accounts, seed_periods
from app.services.accounting.posting_rules import seed_default_posting_rules
from app.services.accounting import intercompany_saga as saga
from app.services.accounting.intercompany_saga import IntercompanySagaError


def _seed_company(db, code, state="03"):
    c = Company(code=code, name=code, state_code=state)
    db.add(c); db.flush()
    seed_chart_of_accounts(db, c); seed_default_posting_rules(db, c.id); seed_periods(db, c, 2026)
    db.commit()
    return c


def _setup(db):
    a = db.execute(select(Company).where(Company.code == "DEMO")).scalar_one()
    b = _seed_company(db, "SAGAB", "03")
    def acc(cid, code): return db.execute(select(ChartOfAccount).where(
        ChartOfAccount.company_id == cid, ChartOfAccount.code == code)).scalar_one().id
    for (x, y) in [(a, b), (b, a)]:
        db.add(IntercompanyAccountMapping(
            company_id=x.id, counterparty_company_id=y.id,
            due_from_account_id=acc(x.id, "1050"), due_to_account_id=acc(x.id, "2070"),
            due_from_account_code="1050", due_to_account_code="2070", is_approved=True))
    db.commit()
    return a, b


def test_saga_completes_both_legs(db):
    a, b = _setup(db)
    txn = saga.post_intercompany_saga(db, seller_company_id=a.id, buyer_company_id=b.id,
                                      taxable=Decimal("100000"), idempotency_key="K1",
                                      voucher_date=date(2026, 4, 15), require_live_flag=False)
    assert IntercompanyTxnStatus(txn.status) == IntercompanyTxnStatus.COMPLETED
    assert txn.seller_voucher_number and txn.buyer_voucher_number
    # seller voucher in A, buyer voucher in B
    sv = db.execute(select(Voucher).where(Voucher.company_id == a.id,
                    Voucher.posting_reference == "ICSAGA:K1:SELL")).scalar_one()
    bv = db.execute(select(Voucher).where(Voucher.company_id == b.id,
                    Voucher.posting_reference == "ICSAGA:K1:BUY")).scalar_one()
    assert sv.total_debit == sv.total_credit == Decimal("100000.00")
    assert bv.total_debit == bv.total_credit == Decimal("100000.00")


def test_saga_is_idempotent(db):
    a, b = _setup(db)
    t1 = saga.post_intercompany_saga(db, seller_company_id=a.id, buyer_company_id=b.id,
                                     taxable=Decimal("100000"), idempotency_key="K2",
                                     voucher_date=date(2026, 4, 15), require_live_flag=False)
    t2 = saga.post_intercompany_saga(db, seller_company_id=a.id, buyer_company_id=b.id,
                                     taxable=Decimal("100000"), idempotency_key="K2",
                                     voucher_date=date(2026, 4, 15), require_live_flag=False)
    assert t1.id == t2.id  # same saga, not a duplicate
    # exactly one seller + one buyer voucher
    sv = db.execute(select(Voucher).where(Voucher.posting_reference == "ICSAGA:K2:SELL")).scalars().all()
    bv = db.execute(select(Voucher).where(Voucher.posting_reference == "ICSAGA:K2:BUY")).scalars().all()
    assert len(sv) == 1 and len(bv) == 1


def test_live_flag_disabled_by_default(db):
    a, b = _setup(db)
    with pytest.raises(IntercompanySagaError):
        saga.post_intercompany_saga(db, seller_company_id=a.id, buyer_company_id=b.id,
                                    taxable=Decimal("1000"), idempotency_key="K3",
                                    voucher_date=date(2026, 4, 15))  # require_live_flag defaults True


def test_buyer_failure_compensates_seller(db, monkeypatch):
    a, b = _setup(db)
    # force the buyer leg to fail
    def boom(*args, **kwargs):
        raise RuntimeError("buyer DB unavailable")
    monkeypatch.setattr(saga, "_buyer_lines", boom)
    with pytest.raises(IntercompanySagaError):
        saga.post_intercompany_saga(db, seller_company_id=a.id, buyer_company_id=b.id,
                                    taxable=Decimal("100000"), idempotency_key="K4",
                                    voucher_date=date(2026, 4, 15), require_live_flag=False)
    from app.models.tenancy import IntercompanyTxn
    txn = db.execute(select(IntercompanyTxn).where(IntercompanyTxn.idempotency_key == "K4")).scalar_one()
    assert IntercompanyTxnStatus(txn.status) == IntercompanyTxnStatus.COMPENSATED
    # seller leg posted then reversed -> net zero across the two seller vouchers
    sell = db.execute(select(Voucher).where(Voucher.company_id == a.id,
           Voucher.posting_reference == "ICSAGA:K4:SELL")).scalar_one()
    rev = db.execute(select(Voucher).where(Voucher.company_id == a.id,
          Voucher.posting_reference == "ICSAGA:K4:SELL-REV")).scalar_one()
    assert sell.total_debit == rev.total_credit  # reversal mirrors the original
