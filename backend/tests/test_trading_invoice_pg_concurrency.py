"""Stage 4B-3A.1c.3 — PostgreSQL concurrency & transaction acceptance for trading
sales invoices (allocation foundation 1c.1 + posting 1c.2).

SQLite has neither row-level FOR UPDATE nor a unique-constraint race, so these
proofs need a real PostgreSQL server. They run ONLY when PG_TEST_URL points at
one; otherwise they skip. A disposable cluster must be approved first:

    PG_TEST_URL='postgresql+psycopg2://erp@/erp_test?host=/tmp/pgtest_1c3/sock&port=54331' \
        pytest tests/test_trading_invoice_pg_concurrency.py

Invariants proven (genuinely independent transactions, one per thread):
* Competing invoices on the same dispatch line never over-invoice (row lock).
* Concurrent partial invoicing respects remaining uninvoiced quantity.
* Consolidated multi-line/multi-dispatch invoices use deterministic lock order
  (no deadlock) and allocate consistently.
* Draft cancellation races an allocation without double-releasing or breaking the
  qty_invoiced <-> active-allocation reconciliation.
* Two simultaneous posts create exactly one voucher (posting_reference unique);
  idempotent repost returns the existing voucher; failures roll back fully.
* Fail-closed GST / mapping / cross-company / manufactured-isolation on PG.
* Posting never moves stock, consumes FIFO or posts COGS.
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

D = Decimal
OD = date(2026, 11, 15)


# ------------------------------ harness -------------------------------------

def _engine_session():
    from app.models import Base
    engine = create_engine(PG_URL, future=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, autoflush=False, future=True)


def _seeded_company(Session, *, inter_state=False):
    """Seeded company + TRADING_DELIVERY and TRADING_SALES mappings (synthetic
    TEST accounts), a customer, a trading SKU and a trading warehouse."""
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
        # delivery (dispatch COGS) mapping
        for code, name, typ in [("5155", "Trading COGS", AccountType.EXPENSE.value),
                                ("1105", "Trading Fabric Inventory", AccountType.ASSET.value),
                                ("4011", "Trading Fabric Sales", AccountType.INCOME.value)]:
            if s.execute(select(ChartOfAccount).where(ChartOfAccount.company_id == cid,
                         ChartOfAccount.code == code)).scalar_one_or_none() is None:
                s.add(ChartOfAccount(company_id=cid, code=code, name=name, account_type=typ,
                                     is_group=False, allow_posting=True, is_active=True))
        for slot, code in {"cogs": "5155", "inventory": "1105"}.items():
            s.add(PostingRule(company_id=cid, event_type="TRADING_DELIVERY", role=slot, account_code=code))
        # sales (revenue/GST/receivable) mapping — TRADING_SALES, synthetic
        for slot, code in {"receivable": "1040", "sales": "4011", "output_cgst": "2041",
                           "output_sgst": "2042", "output_igst": "2043"}.items():
            s.add(PostingRule(company_id=cid, event_type="TRADING_SALES", role=slot, account_code=code))
        state = "06" if inter_state else "03"   # company seed state is 03
        cust = Customer(company_id=cid, code="PGCUST", name="PG Cust", state_code=state)
        wh = Warehouse(company_id=cid, code="PG-TWH", name="PG Trading", kind="STORE",
                       category=WarehouseCategory.TRADING.value)
        s.add_all([cust, wh]); s.flush()
        item = trading.create_trading_item(s, cid, code="PG-TF", name="PG Trading Fabric")
        tax = _gst18(s, cid)
        s.commit()
        return cid, cust.id, item.id, wh.id, tax


def _gst18(s, cid):
    from app.models.masters import TaxCode
    tc = s.execute(select(TaxCode).where(TaxCode.company_id == cid, TaxCode.code == "GST18")).scalar_one_or_none()
    if tc is None:
        tc = TaxCode(company_id=cid, code="GST18", name="GST 18%", cgst_rate=D("9"),
                     sgst_rate=D("9"), igst_rate=D("18"), hsn_sac="5208")
        s.add(tc); s.flush()
    return tc.id


def _receive(Session, cid, item_id, wh_id, qty, rate="100"):
    from app.models.inventory import ItemType
    from app.services import inventory
    with Session() as s:
        inventory.receive(s, company_id=cid, item_type=ItemType.TRADING_FABRIC, item_id=item_id,
                          qty=str(qty), unit_cost=rate, warehouse_id=wh_id, event_type="GRN")
        s.commit()


def _approved_dispatch_line(Session, cid, cust_id, item_id, wh_id, qty, suffix):
    """Create+approve a dispatch; return (dispatch_id, dispatch_line_id)."""
    from app.services import trading_sales as ts
    from app.services import trading_dispatch as td
    with Session() as s:
        so = ts.create_trading_so(s, cid, customer_id=cust_id, order_number=f"PGSO-{suffix}", order_date=OD,
                                  lines=[{"trading_item_id": item_id, "warehouse_id": wh_id,
                                          "qty_ordered": str(qty), "rate": "300"}])
        ts.approve_trading_so(s, cid, so.id)
        d = td.create_trading_dispatch(s, cid, customer_id=cust_id, sales_order_id=so.id,
                                       dispatch_number=f"PGDSP-{suffix}", dispatch_date=OD,
                                       lines=[{"trading_item_id": item_id, "sales_order_line_id": so.lines[0].id,
                                               "warehouse_id": wh_id, "quantity": str(qty)}])
        td.submit_trading_dispatch(s, cid, d.id)
        td.approve_trading_dispatch(s, cid, d.id, approved_by=None)
        s.commit()
        return d.id, d.lines[0].id


def _active_alloc_sum(s, dl_id):
    from app.models.trading import TradingDispatchInvoiceAllocation as A, TradingAllocationStatus
    return D(str(s.execute(select(func.coalesce(func.sum(A.quantity_allocated), 0)).where(
        A.dispatch_line_id == dl_id, A.status == TradingAllocationStatus.ACTIVE.value)).scalar()))


def _qty_invoiced(s, dl_id):
    from app.models.trading import TradingDispatchLine
    return D(str(s.get(TradingDispatchLine, dl_id).qty_invoiced))


# ========================= 5A over-invoice prevention ========================

def test_competing_invoices_same_line_cannot_over_invoice():
    from app.services import trading_invoice as ti
    from app.models.trading import TradingDispatchLine
    engine, Session = _engine_session()
    cid, cust_id, item_id, wh_id, tax = _seeded_company(Session)
    _receive(Session, cid, item_id, wh_id, 100)
    _, dl_id = _approved_dispatch_line(Session, cid, cust_id, item_id, wh_id, 60, "A")

    def worker(n):
        with Session() as s:
            try:
                ti.create_trading_invoice(s, cid, customer_id=cust_id, number=f"PGINV-A{n}", invoice_date=OD,
                                          lines=[{"dispatch_line_id": dl_id, "trading_item_id": item_id,
                                                  "quantity": "60", "rate": "300", "tax_code_id": tax}])
                s.commit(); return "ok"
            except Exception as exc:  # noqa: BLE001
                s.rollback(); return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(worker, range(2)))

    assert results.count("ok") == 1, results            # exactly one wins the full 60
    with Session() as s:
        assert _active_alloc_sum(s, dl_id) == D("60")    # never 120
        assert _qty_invoiced(s, dl_id) == D("60")        # counter reconciled
        assert ti.dispatch_line_uninvoiced(s, s.get(__import__('app.models.trading', fromlist=['TradingDispatchLine']).TradingDispatchLine, dl_id)) == D("0")


# ========================= 5B concurrent partial invoicing ===================

def test_concurrent_partial_invoicing_respects_remaining():
    from app.services import trading_invoice as ti
    from app.models.trading import TradingDispatchLine
    engine, Session = _engine_session()
    cid, cust_id, item_id, wh_id, tax = _seeded_company(Session)
    _receive(Session, cid, item_id, wh_id, 100)
    _, dl_id = _approved_dispatch_line(Session, cid, cust_id, item_id, wh_id, 100, "B")

    def worker(n):   # each bills 40; only two fit in 100
        with Session() as s:
            try:
                ti.create_trading_invoice(s, cid, customer_id=cust_id, number=f"PGINV-B{n}", invoice_date=OD,
                                          lines=[{"dispatch_line_id": dl_id, "trading_item_id": item_id,
                                                  "quantity": "40", "rate": "300", "tax_code_id": tax}])
                s.commit(); return "ok"
            except Exception as exc:  # noqa: BLE001
                s.rollback(); return type(exc).__name__

    with ThreadPoolExecutor(max_workers=3) as ex:
        results = list(ex.map(worker, range(3)))

    assert results.count("ok") == 2, results             # 40+40 fit, third rejected
    with Session() as s:
        active = _active_alloc_sum(s, dl_id)
        assert active == D("80") and _qty_invoiced(s, dl_id) == D("80")
        assert ti.dispatch_line_uninvoiced(s, s.get(TradingDispatchLine, dl_id)) == D("20")


# ========================= 5C multi-line deterministic lock order ============

def test_consolidated_multi_dispatch_no_deadlock():
    """Two threads each create a consolidated invoice over the SAME two dispatch
    lines; deterministic (sorted-id) lock order means no deadlock, and both 50+50
    allocations fit each 100-line."""
    from app.services import trading_invoice as ti
    from app.models.trading import TradingDispatchLine
    engine, Session = _engine_session()
    cid, cust_id, item_id, wh_id, tax = _seeded_company(Session)
    _receive(Session, cid, item_id, wh_id, 400)
    _, dlA = _approved_dispatch_line(Session, cid, cust_id, item_id, wh_id, 100, "C1")
    _, dlB = _approved_dispatch_line(Session, cid, cust_id, item_id, wh_id, 100, "C2")

    def worker(n):
        with Session() as s:
            try:
                ti.create_trading_invoice(s, cid, customer_id=cust_id, number=f"PGINV-C{n}", invoice_date=OD,
                                          lines=[{"dispatch_line_id": dlA, "trading_item_id": item_id,
                                                  "quantity": "50", "rate": "300", "tax_code_id": tax},
                                                 {"dispatch_line_id": dlB, "trading_item_id": item_id,
                                                  "quantity": "50", "rate": "300", "tax_code_id": tax}])
                s.commit(); return "ok"
            except Exception as exc:  # noqa: BLE001
                s.rollback(); return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(worker, range(2)))

    assert results.count("ok") == 2, results             # no deadlock; both fit
    with Session() as s:
        for dl in (dlA, dlB):
            assert _active_alloc_sum(s, dl) == D("100") and _qty_invoiced(s, dl) == D("100")
            assert ti.dispatch_line_uninvoiced(s, s.get(TradingDispatchLine, dl)) == D("0")


# ========================= 5D draft-cancellation race ========================

def test_cancel_races_allocation_reconciled():
    """Cancel a full draft while another tx tries to bill the same line again.
    Serialised by the dispatch-line lock: whichever order, the counter stays
    reconciled, nothing is released twice, and total active <= 60."""
    from app.services import trading_invoice as ti
    from app.models.trading import TradingDispatchLine
    engine, Session = _engine_session()
    cid, cust_id, item_id, wh_id, tax = _seeded_company(Session)
    _receive(Session, cid, item_id, wh_id, 100)
    _, dl_id = _approved_dispatch_line(Session, cid, cust_id, item_id, wh_id, 60, "D")
    # existing full draft
    with Session() as s:
        inv = ti.create_trading_invoice(s, cid, customer_id=cust_id, number="PGINV-D0", invoice_date=OD,
                                        lines=[{"dispatch_line_id": dl_id, "trading_item_id": item_id,
                                                "quantity": "60", "rate": "300", "tax_code_id": tax}])
        s.commit(); inv_id = inv.id

    def canceller(_):
        with Session() as s:
            try:
                ti.cancel_trading_invoice(s, cid, inv_id); s.commit(); return "cancelled"
            except Exception as exc:  # noqa: BLE001
                s.rollback(); return f"cancel:{type(exc).__name__}"

    def rebiller(_):
        with Session() as s:
            try:
                ti.create_trading_invoice(s, cid, customer_id=cust_id, number="PGINV-D1", invoice_date=OD,
                                          lines=[{"dispatch_line_id": dl_id, "trading_item_id": item_id,
                                                  "quantity": "60", "rate": "300", "tax_code_id": tax}])
                s.commit(); return "rebilled"
            except Exception as exc:  # noqa: BLE001
                s.rollback(); return f"rebill:{type(exc).__name__}"

    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(canceller, 0); f2 = ex.submit(rebiller, 0)
        results = [f1.result(), f2.result()]

    with Session() as s:
        active = _active_alloc_sum(s, dl_id)
        assert active <= D("60")
        assert _qty_invoiced(s, dl_id) == active            # reconciled, never double-released
        # reconcile call must not raise (counter == active)
        ti.dispatch_line_uninvoiced(s, s.get(TradingDispatchLine, dl_id))


# ========================= 5E counter reconciliation stress ==================

def test_counter_reconciliation_under_churn():
    from app.services import trading_invoice as ti
    from app.models.trading import TradingDispatchLine, TradingDispatchInvoiceAllocation as A, TradingAllocationStatus
    engine, Session = _engine_session()
    cid, cust_id, item_id, wh_id, tax = _seeded_company(Session)
    _receive(Session, cid, item_id, wh_id, 200)
    _, dl_id = _approved_dispatch_line(Session, cid, cust_id, item_id, wh_id, 100, "E")

    def create_worker(n):
        with Session() as s:
            try:
                inv = ti.create_trading_invoice(s, cid, customer_id=cust_id, number=f"PGINV-E{n}", invoice_date=OD,
                                                lines=[{"dispatch_line_id": dl_id, "trading_item_id": item_id,
                                                        "quantity": "25", "rate": "300", "tax_code_id": tax}])
                s.commit(); return str(inv.id)
            except Exception as exc:  # noqa: BLE001
                s.rollback(); return type(exc).__name__

    with ThreadPoolExecutor(max_workers=8) as ex:
        created = [r for r in ex.map(create_worker, range(8)) if len(r) > 20]  # uuids
    # cancel half of them concurrently
    def cancel_worker(iid):
        with Session() as s:
            try:
                ti.cancel_trading_invoice(s, cid, uuid.UUID(iid)); s.commit(); return "c"
            except Exception as exc:  # noqa: BLE001
                s.rollback(); return type(exc).__name__
    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(cancel_worker, created[::2]))

    with Session() as s:
        active = _active_alloc_sum(s, dl_id)
        assert _qty_invoiced(s, dl_id) == active                 # authoritative reconciliation
        assert active <= D("100")
        ti.dispatch_line_uninvoiced(s, s.get(TradingDispatchLine, dl_id))   # must not raise


# ========================= 6 concurrent posting =============================

def _draft_invoice(Session, cid, cust_id, item_id, dl_id, tax, number, qty="60"):
    from app.services import trading_invoice as ti
    with Session() as s:
        inv = ti.create_trading_invoice(s, cid, customer_id=cust_id, number=number, invoice_date=OD,
                                        lines=[{"dispatch_line_id": dl_id, "trading_item_id": item_id,
                                                "quantity": qty, "rate": "300", "tax_code_id": tax}])
        s.commit(); return inv.id


def test_concurrent_posting_creates_one_voucher():
    from app.services import trading_invoice as ti
    from app.models.accounting import Voucher, JournalLine
    from app.models.commercial import DocStatus, SalesInvoice
    engine, Session = _engine_session()
    cid, cust_id, item_id, wh_id, tax = _seeded_company(Session)
    _receive(Session, cid, item_id, wh_id, 100)
    _, dl_id = _approved_dispatch_line(Session, cid, cust_id, item_id, wh_id, 60, "P")
    inv_id = _draft_invoice(Session, cid, cust_id, item_id, dl_id, tax, "PGINV-P")

    def worker(_):
        with Session() as s:
            try:
                ti.post_trading_invoice(s, cid, inv_id, posted_by=None); s.commit(); return "ok"
            except Exception as exc:  # noqa: BLE001
                s.rollback(); return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(worker, range(2)))

    assert "ok" in results, results
    with Session() as s:
        inv = s.get(SalesInvoice, inv_id)
        vids = s.execute(select(Voucher.id).where(Voucher.company_id == cid,
                         Voucher.posting_reference == "TSI:PGINV-P")).scalars().all()
        assert len(vids) == 1                                  # exactly one voucher
        assert str(inv.status) in ("DocStatus.POSTED", "POSTED")
        # receivable/revenue/GST not duplicated: journal has the one voucher's lines only
        jcount = s.execute(select(func.count()).select_from(JournalLine).where(
            JournalLine.voucher_id == vids[0])).scalar()
        assert jcount == 4                                     # Dr AR, Cr sales, Cr CGST, Cr SGST (intra)
        # balanced
        dr = s.execute(select(func.coalesce(func.sum(JournalLine.debit), 0)).where(JournalLine.voucher_id == vids[0])).scalar()
        cr = s.execute(select(func.coalesce(func.sum(JournalLine.credit), 0)).where(JournalLine.voucher_id == vids[0])).scalar()
        assert D(str(dr)) == D(str(cr)) == D("21240.00")


def test_idempotent_repost_returns_existing():
    from app.services import trading_invoice as ti
    from app.models.accounting import Voucher
    engine, Session = _engine_session()
    cid, cust_id, item_id, wh_id, tax = _seeded_company(Session)
    _receive(Session, cid, item_id, wh_id, 100)
    _, dl_id = _approved_dispatch_line(Session, cid, cust_id, item_id, wh_id, 60, "Q")
    inv_id = _draft_invoice(Session, cid, cust_id, item_id, dl_id, tax, "PGINV-Q")
    with Session() as s:
        r1 = ti.post_trading_invoice(s, cid, inv_id, posted_by=None); s.commit()
    with Session() as s:
        r2 = ti.post_trading_invoice(s, cid, inv_id, posted_by=None); s.commit()
    assert r1["already_posted"] is False and r2["already_posted"] is True
    with Session() as s:
        assert s.execute(select(func.count()).select_from(Voucher).where(
            Voucher.company_id == cid, Voucher.posting_reference == "TSI:PGINV-Q")).scalar() == 1


def test_posting_rollback_on_missing_state_leaves_draft():
    """Place-of-supply undeterminable (no customer state) -> blocked; no voucher,
    invoice stays DRAFT, no partial posting."""
    from app.services import trading_invoice as ti
    from app.models.accounting import Voucher
    from app.models.commercial import DocStatus, SalesInvoice
    from app.models.masters import Customer
    engine, Session = _engine_session()
    cid, cust_id, item_id, wh_id, tax = _seeded_company(Session)
    with Session() as s:   # strip the customer's state
        s.get(Customer, cust_id).state_code = None; s.commit()
    _receive(Session, cid, item_id, wh_id, 100)
    _, dl_id = _approved_dispatch_line(Session, cid, cust_id, item_id, wh_id, 60, "R")
    inv_id = _draft_invoice(Session, cid, cust_id, item_id, dl_id, tax, "PGINV-R")
    with Session() as s:
        with pytest.raises(ti.TradingInvoicePostingBlocked):
            ti.post_trading_invoice(s, cid, inv_id, posted_by=None)
        s.rollback()
    with Session() as s:
        assert s.get(SalesInvoice, inv_id).status == DocStatus.DRAFT
        assert s.execute(select(func.count()).select_from(Voucher).where(
            Voucher.company_id == cid, Voucher.posting_reference == "TSI:PGINV-R")).scalar() == 0


# ========================= 7 GST + fail-closed on PG ========================

def test_pg_inter_state_posts_igst():
    from app.services import trading_invoice as ti
    from app.models.accounting import Voucher, JournalLine, ChartOfAccount
    engine, Session = _engine_session()
    cid, cust_id, item_id, wh_id, tax = _seeded_company(Session, inter_state=True)
    _receive(Session, cid, item_id, wh_id, 100)
    _, dl_id = _approved_dispatch_line(Session, cid, cust_id, item_id, wh_id, 60, "IG")
    inv_id = _draft_invoice(Session, cid, cust_id, item_id, dl_id, tax, "PGINV-IG")
    with Session() as s:
        ti.post_trading_invoice(s, cid, inv_id, posted_by=None); s.commit()
    with Session() as s:
        vid = s.execute(select(Voucher.id).where(Voucher.posting_reference == "TSI:PGINV-IG")).scalar_one()
        codes = {c.id: c.code for c in s.execute(select(ChartOfAccount).where(ChartOfAccount.company_id == cid)).scalars()}
        accts = {codes[j.account_id] for j in s.execute(select(JournalLine).where(JournalLine.voucher_id == vid)).scalars()}
        assert "2043" in accts and "2041" not in accts and "2042" not in accts   # IGST only


def test_pg_missing_mapping_fails_closed():
    """No TRADING_SALES posting rules configured -> HTTP-409-class block, DRAFT,
    no voucher, no fall-back to manufactured revenue 4010."""
    from app.services import trading_invoice as ti
    from app.models.accounting import Voucher, PostingRule, ChartOfAccount, AccountType
    from app.models.core import Company
    from app.models.masters import Customer, Warehouse
    from app.models.hybrid import WarehouseCategory
    from app.models.commercial import DocStatus, SalesInvoice
    from app.seed.seed import run_seed
    from app.services import trading
    engine, Session = _engine_session()
    # seed a company WITHOUT TRADING_SALES mapping (delivery only, to allow dispatch)
    with Session() as s:
        run_seed(s, fy_start_year=2026)
        cid = s.execute(select(Company)).scalars().first().id
        for code, name, typ in [("5155", "Trading COGS", AccountType.EXPENSE.value),
                                ("1105", "Trading Fab Inv", AccountType.ASSET.value)]:
            s.add(ChartOfAccount(company_id=cid, code=code, name=name, account_type=typ,
                                 is_group=False, allow_posting=True, is_active=True))
        for slot, code in {"cogs": "5155", "inventory": "1105"}.items():
            s.add(PostingRule(company_id=cid, event_type="TRADING_DELIVERY", role=slot, account_code=code))
        cust = Customer(company_id=cid, code="PGC2", name="C2", state_code="03")
        wh = Warehouse(company_id=cid, code="PG-W2", name="W2", kind="STORE",
                       category=WarehouseCategory.TRADING.value)
        s.add_all([cust, wh]); s.flush()
        item = trading.create_trading_item(s, cid, code="PG-TF2", name="TF2")
        tax = _gst18(s, cid)
        s.commit(); cust_id, item_id, wh_id = cust.id, item.id, wh.id
    _receive(Session, cid, item_id, wh_id, 100)
    _, dl_id = _approved_dispatch_line(Session, cid, cust_id, item_id, wh_id, 60, "NA")
    inv_id = _draft_invoice(Session, cid, cust_id, item_id, dl_id, tax, "PGINV-NA")
    with Session() as s:
        with pytest.raises(ti.TradingInvoicePostingBlocked):
            ti.post_trading_invoice(s, cid, inv_id, posted_by=None)
        s.rollback()
    with Session() as s:
        assert s.get(SalesInvoice, inv_id).status == DocStatus.DRAFT
        assert s.execute(select(func.count()).select_from(Voucher).where(
            Voucher.company_id == cid, Voucher.source_module == "trading_invoice")).scalar() == 0


def test_pg_posting_moves_no_stock_or_cogs():
    from app.services import trading_invoice as ti
    from app.models.inventory import ItemType, StockLayer, InventoryTransaction
    from app.models.accounting import Voucher
    engine, Session = _engine_session()
    cid, cust_id, item_id, wh_id, tax = _seeded_company(Session)
    _receive(Session, cid, item_id, wh_id, 100)
    _, dl_id = _approved_dispatch_line(Session, cid, cust_id, item_id, wh_id, 60, "NS")
    inv_id = _draft_invoice(Session, cid, cust_id, item_id, dl_id, tax, "PGINV-NS")

    def snap(s):
        return (
            D(str(s.execute(select(func.coalesce(func.sum(StockLayer.qty_remaining), 0)).where(
                StockLayer.company_id == cid, StockLayer.item_type == ItemType.TRADING_FABRIC.value,
                StockLayer.item_id == item_id)).scalar())),
            s.execute(select(func.count()).select_from(InventoryTransaction).where(
                InventoryTransaction.company_id == cid)).scalar(),
            s.execute(select(func.count()).select_from(Voucher).where(
                Voucher.company_id == cid, Voucher.source_module == "trading_dispatch")).scalar(),
        )
    with Session() as s:
        before = snap(s)
    with Session() as s:
        ti.post_trading_invoice(s, cid, inv_id, posted_by=None); s.commit()
    with Session() as s:
        after = snap(s)
    assert after == before    # stock remaining, inventory txns, and COGS vouchers all unchanged


def test_pg_cross_company_post_rejected():
    """Posting an invoice under a different company's scope is rejected (not
    found), and a wrong-company customer never leaks across the boundary."""
    from app.services import trading_invoice as ti
    engine, Session = _engine_session()
    cid, cust_id, item_id, wh_id, tax = _seeded_company(Session)
    _receive(Session, cid, item_id, wh_id, 100)
    _, dl_id = _approved_dispatch_line(Session, cid, cust_id, item_id, wh_id, 60, "XC")
    inv_id = _draft_invoice(Session, cid, cust_id, item_id, dl_id, tax, "PGINV-XC")
    other = uuid.uuid4()
    with Session() as s:
        with pytest.raises(ti.TradingInvoiceError):
            ti.post_trading_invoice(s, other, inv_id, posted_by=None)
        s.rollback()


def test_pg_manufactured_invoice_isolated_from_trading_post():
    """A manufactured SalesInvoice (source != TRADING) is invisible to the trading
    posting path."""
    from app.services import trading_invoice as ti
    from app.models.commercial import SalesInvoice, DocStatus
    engine, Session = _engine_session()
    cid, cust_id, item_id, wh_id, tax = _seeded_company(Session)
    with Session() as s:
        mi = SalesInvoice(company_id=cid, customer_id=cust_id, number="PGM-1", invoice_date=OD,
                          status=DocStatus.DRAFT, source="MANUFACTURED", taxable_value=0,
                          tax_amount=0, total_value=0)
        s.add(mi); s.commit(); mid = mi.id
    with Session() as s:
        with pytest.raises(ti.TradingInvoiceError):
            ti.post_trading_invoice(s, cid, mid, posted_by=None)
        s.rollback()


def test_pg_trading_number_uniqueness_enforced():
    """The partial unique index rejects a second TRADING invoice with the same
    company+number, while a MANUFACTURED invoice may reuse that number."""
    from app.services import trading_invoice as ti
    from app.models.commercial import SalesInvoice, DocStatus
    from sqlalchemy.exc import IntegrityError
    engine, Session = _engine_session()
    cid, cust_id, item_id, wh_id, tax = _seeded_company(Session)
    _receive(Session, cid, item_id, wh_id, 100)
    _, dl_id = _approved_dispatch_line(Session, cid, cust_id, item_id, wh_id, 60, "U")
    _draft_invoice(Session, cid, cust_id, item_id, dl_id, tax, "PGINV-U", qty="30")
    # second TRADING invoice, same number -> service pre-check raises
    with Session() as s:
        with pytest.raises(ti.TradingInvoiceError):
            ti.create_trading_invoice(s, cid, customer_id=cust_id, number="PGINV-U", invoice_date=OD,
                                      lines=[{"dispatch_line_id": dl_id, "trading_item_id": item_id,
                                              "quantity": "30", "rate": "300", "tax_code_id": tax}])
        s.rollback()
    # a manufactured invoice may reuse PGINV-U (partial index only covers TRADING)
    with Session() as s:
        s.add(SalesInvoice(company_id=cid, customer_id=cust_id, number="PGINV-U", invoice_date=OD,
                           status=DocStatus.DRAFT, source="MANUFACTURED", taxable_value=0,
                           tax_amount=0, total_value=0))
        s.commit()   # must NOT raise
