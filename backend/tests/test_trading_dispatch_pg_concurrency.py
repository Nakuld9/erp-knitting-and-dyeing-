"""Stage 4B-3A.1b.2 — PostgreSQL concurrency acceptance for trading dispatch approval.

SQLite implements neither advisory locks nor row-level FOR UPDATE, so these
proofs need a real PostgreSQL server. They run ONLY when PG_TEST_URL points at
one; otherwise they are skipped. A disposable cluster must be approved first:

    PG_TEST_URL='postgresql+psycopg2://erp@/erp_test?host=/tmp/pgtest/sock&port=54329' \
        pytest tests/test_trading_dispatch_pg_concurrency.py

Invariants proven:
* Two dispatches competing for the same SKU/warehouse never over-consume stock
  (total consumed <= available; remainder never negative).
* Two simultaneous approvals of the SAME dispatch consume once and post one
  voucher (dispatch row lock + voucher-ref idempotency).
* Two dispatches competing for the same sales-order line cannot over-dispatch it
  without authorisation.
"""
from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

PG_URL = os.environ.get("PG_TEST_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="PG_TEST_URL not set (needs a real PostgreSQL)")

OD = date(2026, 11, 14)


def _engine_session():
    """Fresh schema per test (each test seeds its own company, so a shared schema
    would collide on seed unique keys)."""
    from app.models import Base
    engine = create_engine(PG_URL, future=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, autoflush=False, future=True)


def _seeded_company(Session):
    """A fully seeded company (chart of accounts, voucher types, period) plus a
    configured TRADING_DELIVERY mapping, a customer, SKU and TRADING warehouse."""
    from app.seed.seed import run_seed
    from app.models.core import Company
    from app.models.accounting import AccountType, ChartOfAccount, PostingRule
    from app.models.masters import Customer, Warehouse
    from app.models.hybrid import WarehouseCategory
    from app.services import trading
    with Session() as s:
        run_seed(s, fy_start_year=2026)
        company = s.execute(select(Company)).scalars().first()
        cid = company.id
        for code, name, typ in [("5155", "Trading COGS", AccountType.EXPENSE.value),
                                ("1105", "Trading Fabric Inventory", AccountType.ASSET.value)]:
            s.add(ChartOfAccount(company_id=cid, code=code, name=name, account_type=typ,
                                 is_group=False, allow_posting=True, is_active=True))
        for slot, code in {"cogs": "5155", "inventory": "1105"}.items():
            s.add(PostingRule(company_id=cid, event_type="TRADING_DELIVERY", role=slot, account_code=code))
        cust = Customer(company_id=cid, code="PGCUST", name="PG Cust", state_code="03")
        wh = Warehouse(company_id=cid, code="PG-TWH", name="PG Trading", kind="STORE",
                       category=WarehouseCategory.TRADING.value)
        s.add_all([cust, wh]); s.flush()
        item = trading.create_trading_item(s, cid, code="PG-TF", name="PG Trading Fabric")
        s.commit()
        return cid, cust.id, item.id, wh.id


def _receive(Session, cid, item_id, wh_id, qty, rate):
    from app.models.inventory import ItemType
    from app.services import inventory
    with Session() as s:
        inventory.receive(s, company_id=cid, item_type=ItemType.TRADING_FABRIC, item_id=item_id,
                          qty=qty, unit_cost=rate, warehouse_id=wh_id, event_type="GRN")
        s.commit()


def _approved_so(Session, cid, cust_id, item_id, wh_id, qty, number):
    from app.services import trading_sales as ts
    with Session() as s:
        so = ts.create_trading_so(s, cid, customer_id=cust_id, order_number=number, order_date=OD,
                                  lines=[{"trading_item_id": item_id, "warehouse_id": wh_id,
                                          "qty_ordered": qty, "rate": "300"}])
        ts.approve_trading_so(s, cid, so.id)
        s.commit()
        return so.id, so.lines[0].id


def _submitted_dispatch(Session, cid, cust_id, so_id, so_line_id, item_id, wh_id, qty, number):
    from app.services import trading_dispatch as td
    with Session() as s:
        d = td.create_trading_dispatch(s, cid, customer_id=cust_id, sales_order_id=so_id,
                                       dispatch_number=number, dispatch_date=OD,
                                       lines=[{"trading_item_id": item_id, "sales_order_line_id": so_line_id,
                                               "warehouse_id": wh_id, "quantity": qty}])
        td.submit_trading_dispatch(s, cid, d.id)
        s.commit()
        return d.id


def test_concurrent_same_dispatch_approval_posts_once():
    """Two threads approve the SAME dispatch at once -> exactly one voucher and one
    stock consumption; the loser returns idempotent success or rolls back."""
    from app.models.inventory import ItemType, StockLayer
    from app.models.accounting import Voucher
    from app.models.trading import TradingDispatchFifoAllocation
    from app.services import trading_dispatch as td
    engine, Session = _engine_session()
    cid, cust_id, item_id, wh_id = _seeded_company(Session)
    _receive(Session, cid, item_id, wh_id, Decimal("100"), Decimal("100"))
    so_id, so_line_id = _approved_so(Session, cid, cust_id, item_id, wh_id, Decimal("100"), "PGSO-1")
    did = _submitted_dispatch(Session, cid, cust_id, so_id, so_line_id, item_id, wh_id, Decimal("60"), "PGDSP-1")

    def worker(_):
        with Session() as s:
            try:
                td.approve_trading_dispatch(s, cid, did, approved_by=None)
                s.commit()
                return "ok"
            except Exception as exc:  # noqa: BLE001
                s.rollback()
                return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as ex:
        list(ex.map(worker, range(2)))

    with Session() as s:
        vcount = s.execute(select(func.count()).select_from(Voucher).where(
            Voucher.company_id == cid, Voucher.posting_reference == "TDISP:PGDSP-1")).scalar()
        acount = s.execute(select(func.count()).select_from(TradingDispatchFifoAllocation).where(
            TradingDispatchFifoAllocation.dispatch_id == did)).scalar()
        remaining = s.execute(select(func.coalesce(func.sum(StockLayer.qty_remaining), 0)).where(
            StockLayer.company_id == cid, StockLayer.item_type == ItemType.TRADING_FABRIC.value,
            StockLayer.item_id == item_id)).scalar()
    assert vcount == 1                        # posted exactly once
    assert acount == 1                        # consumed exactly once
    assert Decimal(str(remaining)) == Decimal("40")   # 100 - 60, never double-drawn


def test_concurrent_two_dispatches_cannot_over_dispatch_line():
    """Two dispatches each equal to the full outstanding; concurrent approval must
    not let combined dispatched exceed the order line without authorisation."""
    from app.models.trading import TradingSalesOrderLine
    from app.services import trading_dispatch as td
    engine, Session = _engine_session()
    cid, cust_id, item_id, wh_id = _seeded_company(Session)
    _receive(Session, cid, item_id, wh_id, Decimal("200"), Decimal("100"))  # ample stock
    so_id, so_line_id = _approved_so(Session, cid, cust_id, item_id, wh_id, Decimal("100"), "PGSO-2")
    d1 = _submitted_dispatch(Session, cid, cust_id, so_id, so_line_id, item_id, wh_id, Decimal("100"), "PGDSP-2A")
    d2 = _submitted_dispatch(Session, cid, cust_id, so_id, so_line_id, item_id, wh_id, Decimal("100"), "PGDSP-2B")

    def worker(did):
        with Session() as s:
            try:
                td.approve_trading_dispatch(s, cid, did, approved_by=None, over_dispatch_authorized=False)
                s.commit()
                return "ok"
            except Exception as exc:  # noqa: BLE001
                s.rollback()
                return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(worker, [d1, d2]))

    assert results.count("ok") == 1           # exactly one succeeds; the other is blocked
    with Session() as s:
        line = s.get(TradingSalesOrderLine, so_line_id)
        assert Decimal(str(line.qty_dispatched)) == Decimal("100")   # never over-dispatched


def test_concurrent_dispatches_compete_for_final_stock():
    """Two dispatches (separate orders) each want 60 but only 60 is on hand: one
    approves, the other fails InsufficientStock; stock never goes negative and only
    one voucher is posted."""
    from app.models.inventory import ItemType, StockLayer
    from app.models.accounting import Voucher
    from app.services import trading_dispatch as td
    engine, Session = _engine_session()
    cid, cust_id, item_id, wh_id = _seeded_company(Session)
    _receive(Session, cid, item_id, wh_id, Decimal("60"), Decimal("100"))   # only 60 on hand
    so1, sol1 = _approved_so(Session, cid, cust_id, item_id, wh_id, Decimal("60"), "PGSO-3A")
    so2, sol2 = _approved_so(Session, cid, cust_id, item_id, wh_id, Decimal("60"), "PGSO-3B")
    d1 = _submitted_dispatch(Session, cid, cust_id, so1, sol1, item_id, wh_id, Decimal("60"), "PGDSP-3A")
    d2 = _submitted_dispatch(Session, cid, cust_id, so2, sol2, item_id, wh_id, Decimal("60"), "PGDSP-3B")

    def worker(did):
        with Session() as s:
            try:
                td.approve_trading_dispatch(s, cid, did, approved_by=None)
                s.commit()
                return "ok"
            except Exception as exc:  # noqa: BLE001
                s.rollback()
                return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(worker, [d1, d2]))
    assert results.count("ok") == 1 and "InsufficientStockError" in results
    with Session() as s:
        remaining = s.execute(select(func.coalesce(func.sum(StockLayer.qty_remaining), 0)).where(
            StockLayer.company_id == cid, StockLayer.item_type == ItemType.TRADING_FABRIC.value,
            StockLayer.item_id == item_id)).scalar()
        vcount = s.execute(select(func.count()).select_from(Voucher).where(
            Voucher.company_id == cid, Voucher.source_module == "trading_dispatch")).scalar()
    assert Decimal(str(remaining)) == Decimal("0") and vcount == 1   # never negative; one voucher


def test_concurrent_approval_and_cancellation_are_consistent():
    """Approving and cancelling the same SUBMITTED dispatch at once must not leave
    an inconsistent state (never both a voucher AND a cancelled dispatch)."""
    from app.models.accounting import Voucher
    from app.models.trading import TradingDispatch, TradingDispatchStatus
    from app.services import trading_dispatch as td
    engine, Session = _engine_session()
    cid, cust_id, item_id, wh_id = _seeded_company(Session)
    _receive(Session, cid, item_id, wh_id, Decimal("100"), Decimal("100"))
    so_id, so_line_id = _approved_so(Session, cid, cust_id, item_id, wh_id, Decimal("100"), "PGSO-4")
    did = _submitted_dispatch(Session, cid, cust_id, so_id, so_line_id, item_id, wh_id, Decimal("60"), "PGDSP-4")

    def approver(_):
        with Session() as s:
            try:
                td.approve_trading_dispatch(s, cid, did, approved_by=None); s.commit(); return "approved"
            except Exception as exc:  # noqa: BLE001
                s.rollback(); return f"approve:{type(exc).__name__}"

    def canceller(_):
        with Session() as s:
            try:
                td.cancel_trading_dispatch(s, cid, did); s.commit(); return "cancelled"
            except Exception as exc:  # noqa: BLE001
                s.rollback(); return f"cancel:{type(exc).__name__}"

    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(approver, 0); f2 = ex.submit(canceller, 0)
        _ = [f1.result(), f2.result()]

    with Session() as s:
        d = s.get(TradingDispatch, did)
        vcount = s.execute(select(func.count()).select_from(Voucher).where(
            Voucher.company_id == cid, Voucher.posting_reference == "TDISP:PGDSP-4")).scalar()
    # consistent: APPROVED <=> exactly one voucher; CANCELLED <=> no voucher
    if d.status == TradingDispatchStatus.APPROVED.value:
        assert vcount == 1
    elif d.status == TradingDispatchStatus.CANCELLED.value:
        assert vcount == 0
    else:
        raise AssertionError(f"unexpected terminal status {d.status}")


def test_concurrent_consume_same_sku_warehouse_never_double_draws():
    """Direct proof that FIFO consumption of one trading SKU/warehouse never
    over-draws under many concurrent threads (advisory lock + FOR UPDATE)."""
    from app.models.inventory import ItemType, StockLayer
    from app.services import inventory
    from app.services.inventory.service import InsufficientStockError
    engine, Session = _engine_session()
    cid, cust_id, item_id, wh_id = _seeded_company(Session)
    _receive(Session, cid, item_id, wh_id, Decimal("100"), Decimal("10"))
    THREADS, EACH = 12, Decimal("12")   # demand 144 > 100

    def worker(_):
        with Session() as s:
            try:
                r = inventory.consume(s, company_id=cid, item_type=ItemType.TRADING_FABRIC,
                                      item_id=item_id, qty=EACH, warehouse_id=wh_id)
                s.commit(); return Decimal(str(r["qty"]))
            except InsufficientStockError:
                s.rollback(); return Decimal("0")

    with ThreadPoolExecutor(max_workers=THREADS) as ex:
        drawn = sum(ex.map(worker, range(THREADS)), Decimal("0"))
    with Session() as s:
        remaining = s.execute(select(func.coalesce(func.sum(StockLayer.qty_remaining), 0)).where(
            StockLayer.company_id == cid, StockLayer.item_type == ItemType.TRADING_FABRIC.value,
            StockLayer.item_id == item_id)).scalar()
    assert drawn <= Decimal("100")                       # never over-consumed
    assert Decimal(str(remaining)) >= Decimal("0")       # never negative
    assert drawn + Decimal(str(remaining)) == Decimal("100")   # exact conservation
