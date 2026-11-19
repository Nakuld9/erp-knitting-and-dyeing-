"""Stage 5D — PostgreSQL concurrency & transaction acceptance for direct yarn sales.

SQLite has neither row-level FOR UPDATE / advisory locks nor a unique-constraint
race, so these proofs need a real PostgreSQL server. They run ONLY when
PG_TEST_URL points at one; otherwise they skip. A disposable cluster must be
approved first:

    PG_TEST_URL='postgresql+psycopg2://erp@/erp_test?host=/tmp/pgtest_5d/sock&port=54332' \
        pytest tests/test_yarn_sales_pg_concurrency.py

Every concurrent operation runs in its OWN thread with its OWN SQLAlchemy session
and its OWN transaction; assertions check final DATABASE state, not service return
values. Yarn stock is the shared ItemType.YARN FIFO pool (also used by production
issue), so correctness is enforced by inventory.consume (advisory lock + FOR
UPDATE) and the voucher posting_reference unique constraint.
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


def _seeded_company(Session, *, inter_state=False, with_sales=True):
    """Seeded company + YARN_DELIVERY (and optionally YARN_SALES) synthetic TEST
    mappings, a customer, a yarn SKU and a YARN_STORE warehouse."""
    from app.seed.seed import run_seed
    from app.models.core import Company
    from app.models.accounting import AccountType, ChartOfAccount, PostingRule
    from app.models.masters import Customer, Warehouse, YarnMaster
    from app.models.hybrid import WarehouseCategory
    with Session() as s:
        run_seed(s, fy_start_year=2026)
        cid = s.execute(select(Company)).scalars().first().id
        for code, name, typ in [("5160", "Yarn COGS", AccountType.EXPENSE.value),
                                ("4012", "Yarn Sales", AccountType.INCOME.value)]:
            if s.execute(select(ChartOfAccount).where(ChartOfAccount.company_id == cid,
                         ChartOfAccount.code == code)).scalar_one_or_none() is None:
                s.add(ChartOfAccount(company_id=cid, code=code, name=name, account_type=typ,
                                     is_group=False, allow_posting=True, is_active=True))
        for slot, code in {"cogs": "5160", "inventory": "1060"}.items():
            s.add(PostingRule(company_id=cid, event_type="YARN_DELIVERY", role=slot, account_code=code))
        if with_sales:
            for slot, code in {"receivable": "1040", "sales": "4012", "output_cgst": "2041",
                               "output_sgst": "2042", "output_igst": "2043"}.items():
                s.add(PostingRule(company_id=cid, event_type="YARN_SALES", role=slot, account_code=code))
        state = "06" if inter_state else "03"
        cust = Customer(company_id=cid, code="YPGC", name="PG Cust", state_code=state)
        wh = Warehouse(company_id=cid, code="YPG-WH", name="PG Yarn Store", kind="STORE",
                       category=WarehouseCategory.YARN_STORE.value)
        yarn = YarnMaster(company_id=cid, code="YPG", name="PG Yarn", is_active=True)
        s.add_all([cust, wh, yarn]); s.flush()
        tax = _gst18(s, cid)
        s.commit()
        return cid, cust.id, yarn.id, wh.id, tax


def _gst18(s, cid):
    from app.models.masters import TaxCode
    tc = s.execute(select(TaxCode).where(TaxCode.company_id == cid, TaxCode.code == "GST18")).scalar_one_or_none()
    if tc is None:
        tc = TaxCode(company_id=cid, code="GST18", name="GST 18%", cgst_rate=D("9"),
                     sgst_rate=D("9"), igst_rate=D("18"), hsn_sac="5208")
        s.add(tc); s.flush()
    return tc.id


def _lot(Session, cid, yarn_id, wh_id, *, internal, qty, cost):
    from app.models.inventory import ItemType
    from app.models.masters import YarnLot
    from app.services import inventory
    with Session() as s:
        lot = YarnLot(company_id=cid, yarn_id=yarn_id, internal_lot_no=internal,
                      supplier_lot_no=internal + "-SUP", warehouse_id=wh_id, received_date=OD.isoformat(),
                      unit_cost=D(cost))
        s.add(lot); s.flush()
        inventory.receive(s, company_id=cid, item_type=ItemType.YARN, item_id=yarn_id, qty=str(qty),
                          unit_cost=str(cost), lot_id=lot.id, warehouse_id=wh_id, event_type="YARN_GRN")
        s.commit()
        return lot.id


def _approved_so(Session, cid, cust_id, yarn_id, wh_id, qty, number):
    from app.services import yarn_sales_order as so_svc
    with Session() as s:
        so = so_svc.create_yarn_so(s, cid, customer_id=cust_id, order_number=number, order_date=OD,
                                   lines=[{"yarn_id": yarn_id, "warehouse_id": wh_id,
                                           "qty_ordered": str(qty), "rate": "300"}])
        so_svc.approve_yarn_so(s, cid, so.id)
        s.commit()
        return so.id, so.lines[0].id


def _submitted_dispatch(Session, cid, cust_id, so_id, so_line_id, yarn_id, wh_id, qty, number, lot_id=None):
    from app.services import yarn_sales_dispatch as dsp_svc
    with Session() as s:
        d = dsp_svc.create_yarn_dispatch(s, cid, customer_id=cust_id, sales_order_id=so_id,
                                         dispatch_number=number, dispatch_date=OD,
                                         lines=[{"yarn_id": yarn_id, "sales_order_line_id": so_line_id,
                                                 "warehouse_id": wh_id, "lot_id": lot_id, "quantity": str(qty)}])
        dsp_svc.submit_yarn_dispatch(s, cid, d.id)
        s.commit()
        return d.id


def _draft_invoice(Session, cid, cust_id, d_id, number, tax):
    from app.services import yarn_sales_invoice as inv_svc
    from app.models.yarn_sales import YarnSalesDispatch
    with Session() as s:
        d = s.get(YarnSalesDispatch, d_id)
        dl = d.lines[0]
        inv = inv_svc.create_yarn_invoice(s, cid, customer_id=cust_id, number=number, invoice_date=OD,
                                          lines=[{"dispatch_line_id": dl.id, "quantity": str(dl.quantity),
                                                  "rate": "300", "tax_code_id": tax}])
        s.commit()
        return inv.id


def _yarn_on_hand(s, cid, yarn_id):
    from app.models.inventory import ItemType
    from app.services import inventory
    return D(str(inventory.balance(s, company_id=cid, item_type=ItemType.YARN, item_id=yarn_id)["qty_on_hand"]))


def _min_layer_qty(s, cid):
    from app.models.inventory import StockLayer
    vals = s.execute(select(StockLayer.qty_remaining).where(StockLayer.company_id == cid)).scalars().all()
    return min((D(str(v)) for v in vals), default=D("0"))


# ============================ Scenario 1 ====================================

def test_s1_competing_dispatches_same_lot():
    from app.services import yarn_sales_dispatch as dsp_svc
    from app.services.inventory.service import InsufficientStockError
    from app.models.yarn_sales import YarnSalesDispatchFifoAllocation
    from app.models.accounting import Voucher
    engine, Session = _engine_session()
    cid, cust, yarn, wh, tax = _seeded_company(Session)
    _lot(Session, cid, yarn, wh, internal="L1", qty="60", cost="200")
    so1, sol1 = _approved_so(Session, cid, cust, yarn, wh, "60", "YSO-1A")
    so2, sol2 = _approved_so(Session, cid, cust, yarn, wh, "60", "YSO-1B")
    d1 = _submitted_dispatch(Session, cid, cust, so1, sol1, yarn, wh, "60", "YD-1A")
    d2 = _submitted_dispatch(Session, cid, cust, so2, sol2, yarn, wh, "60", "YD-1B")

    def worker(did):
        with Session() as s:
            try:
                dsp_svc.approve_yarn_dispatch(s, cid, did, approved_by=None); s.commit(); return "ok"
            except Exception as exc:  # noqa: BLE001
                s.rollback(); return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(worker, [d1, d2]))
    assert results.count("ok") == 1 and "InsufficientStockError" in results
    with Session() as s:
        assert _yarn_on_hand(s, cid, yarn) == D("0.000000")       # 60 drawn once, never 120
        assert _min_layer_qty(s, cid) >= D("0")
        assert s.execute(select(func.count()).select_from(Voucher).where(
            Voucher.company_id == cid, Voucher.source_module == "yarn_sales_dispatch")).scalar() == 1


# ============================ Scenario 2 ====================================

def test_s2_production_issue_vs_yarn_sale_shared_pool():
    from app.services import yarn_sales_dispatch as dsp_svc
    from app.services import inventory
    from app.services.inventory.service import InsufficientStockError
    from app.models.inventory import ItemType, StockLayer
    engine, Session = _engine_session()
    cid, cust, yarn, wh, tax = _seeded_company(Session)
    _lot(Session, cid, yarn, wh, internal="L2", qty="100", cost="200")
    so, sol = _approved_so(Session, cid, cust, yarn, wh, "70", "YSO-2")
    d = _submitted_dispatch(Session, cid, cust, so, sol, yarn, wh, "70", "YD-2")

    def sale(_):
        with Session() as s:
            try:
                dsp_svc.approve_yarn_dispatch(s, cid, d, approved_by=None); s.commit(); return "sale_ok"
            except Exception as exc:  # noqa: BLE001
                s.rollback(); return f"sale:{type(exc).__name__}"

    def production(_):   # a production-style issue drawing the SAME YARN pool
        with Session() as s:
            try:
                r = inventory.consume(s, company_id=cid, item_type=ItemType.YARN, item_id=yarn,
                                      qty=D("70"), warehouse_id=wh, event_type="YARN_ISSUE")
                s.commit(); return "prod_ok"
            except InsufficientStockError:
                s.rollback(); return "prod:InsufficientStockError"

    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(sale, 0); f2 = ex.submit(production, 0)
        results = [f1.result(), f2.result()]
    # 70 + 70 = 140 > 100: exactly one of the two 70-draws succeeds
    oks = [r for r in results if r.endswith("_ok")]
    assert len(oks) == 1, results
    with Session() as s:
        remaining = _yarn_on_hand(s, cid, yarn)
        assert remaining == D("30.000000")        # one 70-draw only; combined never exceeds 100
        assert _min_layer_qty(s, cid) >= D("0")


# ============================ Scenario 3 ====================================

def test_s3_multiple_dispatches_limited_stock():
    from app.services import yarn_sales_dispatch as dsp_svc
    from app.models.yarn_sales import YarnSalesDispatchFifoAllocation, YarnSalesDispatch, YarnDispatchStatus
    engine, Session = _engine_session()
    cid, cust, yarn, wh, tax = _seeded_company(Session)
    _lot(Session, cid, yarn, wh, internal="L3", qty="100", cost="200")   # only 100
    dids = []
    for i in range(4):   # 4 x 40 = 160 demand
        so, sol = _approved_so(Session, cid, cust, yarn, wh, "40", f"YSO-3{i}")
        dids.append(_submitted_dispatch(Session, cid, cust, so, sol, yarn, wh, "40", f"YD-3{i}"))

    def worker(did):
        with Session() as s:
            try:
                dsp_svc.approve_yarn_dispatch(s, cid, did, approved_by=None); s.commit(); return "ok"
            except Exception as exc:  # noqa: BLE001
                s.rollback(); return type(exc).__name__

    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(worker, dids))
    assert results.count("ok") == 2            # 40+40 fit in 100; 3rd/4th fail
    with Session() as s:
        assert _yarn_on_hand(s, cid, yarn) == D("20.000000")   # 100 - 80
        assert _min_layer_qty(s, cid) >= D("0")
        # every APPROVED dispatch reconciles: consumed == sum(allocation qty)
        for did in dids:
            d = s.get(YarnSalesDispatch, did)
            allocs = s.execute(select(YarnSalesDispatchFifoAllocation).where(
                YarnSalesDispatchFifoAllocation.dispatch_id == did)).scalars().all()
            if d.status == YarnDispatchStatus.APPROVED.value:
                assert sum(D(str(a.quantity_consumed)) for a in allocs) == D(str(d.lines[0].quantity))
            else:
                assert allocs == []           # failed approvals leave no partial evidence


# ============================ Scenario 4 ====================================

def test_s4_concurrent_retry_same_dispatch():
    from app.services import yarn_sales_dispatch as dsp_svc
    from app.models.yarn_sales import YarnSalesDispatchFifoAllocation
    from app.models.accounting import Voucher
    engine, Session = _engine_session()
    cid, cust, yarn, wh, tax = _seeded_company(Session)
    _lot(Session, cid, yarn, wh, internal="L4", qty="100", cost="200")
    so, sol = _approved_so(Session, cid, cust, yarn, wh, "60", "YSO-4")
    d = _submitted_dispatch(Session, cid, cust, so, sol, yarn, wh, "60", "YD-4")

    def worker(_):
        with Session() as s:
            try:
                dsp_svc.approve_yarn_dispatch(s, cid, d, approved_by=None); s.commit(); return "ok"
            except Exception as exc:  # noqa: BLE001
                s.rollback(); return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as ex:
        list(ex.map(worker, range(2)))
    with Session() as s:
        assert _yarn_on_hand(s, cid, yarn) == D("40.000000")        # drawn once
        assert s.execute(select(func.count()).select_from(Voucher).where(
            Voucher.company_id == cid, Voucher.posting_reference == "YDISP:YD-4")).scalar() == 1
        allocs = s.execute(select(YarnSalesDispatchFifoAllocation).where(
            YarnSalesDispatchFifoAllocation.dispatch_id == d)).scalars().all()
        assert sum(D(str(a.quantity_consumed)) for a in allocs) == D("60")   # one allocation set


# ============================ Scenario 5 ====================================

def test_s5_concurrent_invoice_posting():
    from app.services import yarn_sales_dispatch as dsp_svc
    from app.services import yarn_sales_invoice as inv_svc
    from app.models.accounting import Voucher, JournalLine
    engine, Session = _engine_session()
    cid, cust, yarn, wh, tax = _seeded_company(Session)
    _lot(Session, cid, yarn, wh, internal="L5", qty="100", cost="200")
    so, sol = _approved_so(Session, cid, cust, yarn, wh, "60", "YSO-5")
    d = _submitted_dispatch(Session, cid, cust, so, sol, yarn, wh, "60", "YD-5")
    with Session() as s:
        dsp_svc.approve_yarn_dispatch(s, cid, d, approved_by=None); s.commit()
    iid = _draft_invoice(Session, cid, cust, d, "YI-5", tax)

    def worker(_):
        with Session() as s:
            try:
                inv_svc.post_yarn_invoice(s, cid, iid, posted_by=None); s.commit(); return "ok"
            except Exception as exc:  # noqa: BLE001
                s.rollback(); return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as ex:
        list(ex.map(worker, range(2)))
    with Session() as s:
        vids = s.execute(select(Voucher.id).where(Voucher.company_id == cid,
                         Voucher.posting_reference == "YSI:YI-5")).scalars().all()
        assert len(vids) == 1                      # exactly one revenue voucher
        jcount = s.execute(select(func.count()).select_from(JournalLine).where(
            JournalLine.voucher_id == vids[0])).scalar()
        assert jcount == 4                          # Dr AR / Cr sales / Cr CGST / Cr SGST (no duplication)


# ============================ Scenario 6 ====================================

def test_s6_cancel_races_approval():
    from app.services import yarn_sales_dispatch as dsp_svc
    from app.models.yarn_sales import YarnSalesDispatch, YarnSalesDispatchFifoAllocation, YarnDispatchStatus
    from app.models.accounting import Voucher
    engine, Session = _engine_session()
    cid, cust, yarn, wh, tax = _seeded_company(Session)
    _lot(Session, cid, yarn, wh, internal="L6", qty="100", cost="200")
    so, sol = _approved_so(Session, cid, cust, yarn, wh, "60", "YSO-6")
    d = _submitted_dispatch(Session, cid, cust, so, sol, yarn, wh, "60", "YD-6")

    def approver(_):
        with Session() as s:
            try:
                dsp_svc.approve_yarn_dispatch(s, cid, d, approved_by=None); s.commit(); return "approved"
            except Exception as exc:  # noqa: BLE001
                s.rollback(); return f"approve:{type(exc).__name__}"

    def canceller(_):
        with Session() as s:
            try:
                dsp_svc.cancel_yarn_dispatch(s, cid, d); s.commit(); return "cancelled"
            except Exception as exc:  # noqa: BLE001
                s.rollback(); return f"cancel:{type(exc).__name__}"

    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(approver, 0); f2 = ex.submit(canceller, 0)
        _ = [f1.result(), f2.result()]
    with Session() as s:
        d_row = s.get(YarnSalesDispatch, d)
        vcount = s.execute(select(func.count()).select_from(Voucher).where(
            Voucher.company_id == cid, Voucher.posting_reference == "YDISP:YD-6")).scalar()
        allocs = s.execute(select(func.count()).select_from(YarnSalesDispatchFifoAllocation).where(
            YarnSalesDispatchFifoAllocation.dispatch_id == d)).scalar()
        remaining = _yarn_on_hand(s, cid, yarn)
        if d_row.status == YarnDispatchStatus.APPROVED.value:
            assert vcount == 1 and allocs >= 1 and remaining == D("40.000000")
        elif d_row.status == YarnDispatchStatus.CANCELLED.value:
            assert vcount == 0 and allocs == 0 and remaining == D("100.000000")
        else:
            raise AssertionError(f"unexpected terminal status {d_row.status}")


# ============================ Scenario 7 ====================================

def test_s7_concurrent_specific_lot_dispatches():
    from app.services import yarn_sales_dispatch as dsp_svc
    from app.models.yarn_sales import YarnSalesDispatchFifoAllocation
    engine, Session = _engine_session()
    cid, cust, yarn, wh, tax = _seeded_company(Session)
    lotA = _lot(Session, cid, yarn, wh, internal="L7A", qty="50", cost="100")
    lotB = _lot(Session, cid, yarn, wh, internal="L7B", qty="50", cost="300")
    soA, solA = _approved_so(Session, cid, cust, yarn, wh, "40", "YSO-7A")
    soB, solB = _approved_so(Session, cid, cust, yarn, wh, "40", "YSO-7B")
    dA = _submitted_dispatch(Session, cid, cust, soA, solA, yarn, wh, "40", "YD-7A", lot_id=lotA)
    dB = _submitted_dispatch(Session, cid, cust, soB, solB, yarn, wh, "40", "YD-7B", lot_id=lotB)

    def worker(did):
        with Session() as s:
            try:
                dsp_svc.approve_yarn_dispatch(s, cid, did, approved_by=None); s.commit(); return "ok"
            except Exception as exc:  # noqa: BLE001
                s.rollback(); return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(worker, [dA, dB]))
    assert results == ["ok", "ok"]        # different lots -> both succeed, no contention
    with Session() as s:
        for did, lot in [(dA, lotA), (dB, lotB)]:
            allocs = s.execute(select(YarnSalesDispatchFifoAllocation).where(
                YarnSalesDispatchFifoAllocation.dispatch_id == did)).scalars().all()
            assert allocs and all(a.source_lot_id == lot for a in allocs)   # only its own lot, no leakage


# ============================ Scenario 8 ====================================

def test_s8_cross_company_isolation():
    from app.services import yarn_sales_order as so_svc
    from app.services import yarn_sales_dispatch as dsp_svc
    from app.services import inventory
    from app.services.inventory.service import InsufficientStockError
    from app.models.core import Company
    from app.models.inventory import ItemType
    engine, Session = _engine_session()
    cid, cust, yarn, wh, tax = _seeded_company(Session)
    _lot(Session, cid, yarn, wh, internal="L8", qty="100", cost="200")
    so, sol = _approved_so(Session, cid, cust, yarn, wh, "60", "YSO-8")
    d = _submitted_dispatch(Session, cid, cust, so, sol, yarn, wh, "60", "YD-8")
    with Session() as s:
        other = Company(code=f"OC{uuid.uuid4().hex[:4]}", name="Other"); s.add(other); s.commit(); ocid = other.id
    with Session() as s:
        with pytest.raises(so_svc.YarnSalesError):
            so_svc.get_yarn_so(s, ocid, so)                 # B cannot read A's order
        with pytest.raises(dsp_svc.YarnDispatchError):
            dsp_svc.get_yarn_dispatch(s, ocid, d)           # B cannot read A's dispatch
        with pytest.raises(dsp_svc.YarnDispatchError):
            dsp_svc.approve_yarn_dispatch(s, ocid, d)       # B cannot approve A's dispatch
        s.rollback()
        # B cannot consume A's yarn stock (no stock under B's company scope)
        with pytest.raises(InsufficientStockError):
            inventory.consume(s, company_id=ocid, item_type=ItemType.YARN, item_id=yarn, qty=D("10"))
        s.rollback()
    with Session() as s:
        assert _yarn_on_hand(s, cid, yarn) == D("100.000000")   # A's stock untouched


# ============================ Scenario 12 (fail-closed) =====================

def test_s12a_fail_closed_missing_delivery_mapping():
    from app.services import yarn_sales_dispatch as dsp_svc
    from app.models.yarn_sales import YarnSalesDispatch, YarnSalesDispatchFifoAllocation, YarnDispatchStatus
    from app.models.accounting import Voucher
    engine, Session = _engine_session()
    cid, cust, yarn, wh, tax = _seeded_company(Session, with_sales=True)
    # remove YARN_DELIVERY mapping to force fail-closed
    with Session() as s:
        from app.models.accounting import PostingRule
        for r in s.execute(select(PostingRule).where(PostingRule.company_id == cid,
                           PostingRule.event_type == "YARN_DELIVERY")).scalars().all():
            s.delete(r)
        s.commit()
    _lot(Session, cid, yarn, wh, internal="L12", qty="100", cost="200")
    so, sol = _approved_so(Session, cid, cust, yarn, wh, "60", "YSO-12")
    d = _submitted_dispatch(Session, cid, cust, so, sol, yarn, wh, "60", "YD-12")
    with Session() as s:
        with pytest.raises(dsp_svc.YarnDispatchPostingBlocked):
            dsp_svc.approve_yarn_dispatch(s, cid, d, approved_by=None)
        s.rollback()
    with Session() as s:
        assert s.get(YarnSalesDispatch, d).status == YarnDispatchStatus.SUBMITTED.value
        assert _yarn_on_hand(s, cid, yarn) == D("100.000000")       # no stock moved
        assert s.execute(select(func.count()).select_from(YarnSalesDispatchFifoAllocation).where(
            YarnSalesDispatchFifoAllocation.dispatch_id == d)).scalar() == 0
        assert s.execute(select(func.count()).select_from(Voucher).where(
            Voucher.company_id == cid, Voucher.posting_reference == "YDISP:YD-12")).scalar() == 0


def test_s12b_insufficient_specific_lot_blocks_no_partial():
    from app.services import yarn_sales_dispatch as dsp_svc
    from app.services.inventory.service import InsufficientStockError
    from app.models.yarn_sales import YarnSalesDispatch, YarnSalesDispatchFifoAllocation, YarnDispatchStatus
    from app.models.accounting import Voucher
    engine, Session = _engine_session()
    cid, cust, yarn, wh, tax = _seeded_company(Session)
    lotA = _lot(Session, cid, yarn, wh, internal="L12A", qty="100", cost="100")  # ample in A
    lotB = _lot(Session, cid, yarn, wh, internal="L12B", qty="10", cost="300")   # only 10 in B
    so, sol = _approved_so(Session, cid, cust, yarn, wh, "40", "YSO-12B")
    d = _submitted_dispatch(Session, cid, cust, so, sol, yarn, wh, "40", "YD-12B", lot_id=lotB)
    with Session() as s:
        with pytest.raises(InsufficientStockError):   # lot B has 10, needs 40; never falls back to A
            dsp_svc.approve_yarn_dispatch(s, cid, d, approved_by=None)
        s.rollback()
    with Session() as s:
        assert s.get(YarnSalesDispatch, d).status == YarnDispatchStatus.SUBMITTED.value
        assert _yarn_on_hand(s, cid, yarn) == D("110.000000")       # nothing consumed from A or B
        assert s.execute(select(func.count()).select_from(YarnSalesDispatchFifoAllocation).where(
            YarnSalesDispatchFifoAllocation.dispatch_id == d)).scalar() == 0
        assert s.execute(select(func.count()).select_from(Voucher).where(
            Voucher.company_id == cid, Voucher.posting_reference == "YDISP:YD-12B")).scalar() == 0


def test_s12c_invoice_post_missing_sales_mapping_blocks():
    from app.services import yarn_sales_dispatch as dsp_svc
    from app.services import yarn_sales_invoice as inv_svc
    from app.models.commercial import DocStatus
    from app.models.accounting import Voucher, PostingRule
    engine, Session = _engine_session()
    cid, cust, yarn, wh, tax = _seeded_company(Session, with_sales=False)   # no YARN_SALES
    _lot(Session, cid, yarn, wh, internal="L12C", qty="100", cost="200")
    so, sol = _approved_so(Session, cid, cust, yarn, wh, "60", "YSO-12C")
    d = _submitted_dispatch(Session, cid, cust, so, sol, yarn, wh, "60", "YD-12C")
    with Session() as s:
        dsp_svc.approve_yarn_dispatch(s, cid, d, approved_by=None); s.commit()
    iid = _draft_invoice(Session, cid, cust, d, "YI-12C", tax)
    with Session() as s:
        with pytest.raises(inv_svc.YarnInvoicePostingBlocked):
            inv_svc.post_yarn_invoice(s, cid, iid, posted_by=None)
        s.rollback()
    with Session() as s:
        assert inv_svc.get_yarn_invoice(s, cid, iid).status == DocStatus.DRAFT
        assert s.execute(select(func.count()).select_from(Voucher).where(
            Voucher.company_id == cid, Voucher.posting_reference == "YSI:YI-12C")).scalar() == 0


# ======================= Scenarios 9/10/11 (global invariants) ==============

def test_s9_10_11_invariants_after_contention():
    """After a mixed contention run, assert: no negative stock (9); consumed ==
    sum(FIFO allocation) per approved dispatch (10); exactly one balanced voucher
    per approved dispatch, unique refs, none for failed ops (11)."""
    from app.services import yarn_sales_dispatch as dsp_svc
    from app.models.inventory import StockLayer, ItemType
    from app.models.yarn_sales import (YarnSalesDispatch, YarnSalesDispatchFifoAllocation,
                                       YarnDispatchStatus)
    from app.models.accounting import Voucher, JournalLine
    from app.services import inventory
    engine, Session = _engine_session()
    cid, cust, yarn, wh, tax = _seeded_company(Session)
    _lot(Session, cid, yarn, wh, internal="L9", qty="120", cost="200")
    dids = []
    for i in range(5):   # 5 x 40 = 200 demand vs 120
        so, sol = _approved_so(Session, cid, cust, yarn, wh, "40", f"YSO-9{i}")
        dids.append(_submitted_dispatch(Session, cid, cust, so, sol, yarn, wh, "40", f"YD-9{i}"))

    def worker(did):
        with Session() as s:
            try:
                dsp_svc.approve_yarn_dispatch(s, cid, did, approved_by=None); s.commit(); return "ok"
            except Exception as exc:  # noqa: BLE001
                s.rollback(); return type(exc).__name__

    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(worker, dids))
    assert results.count("ok") == 3                 # 3 x 40 = 120 fits exactly

    with Session() as s:
        # (9) no negative stock layer, no negative aggregate
        for v in s.execute(select(StockLayer.qty_remaining).where(StockLayer.company_id == cid)).scalars():
            assert D(str(v)) >= D("0")
        assert _yarn_on_hand(s, cid, yarn) == D("0.000000")   # 120 fully drawn, never below 0
        approved = 0
        for did in dids:
            d = s.get(YarnSalesDispatch, did)
            allocs = s.execute(select(YarnSalesDispatchFifoAllocation).where(
                YarnSalesDispatchFifoAllocation.dispatch_id == did)).scalars().all()
            vids = s.execute(select(Voucher.id).where(Voucher.company_id == cid,
                             Voucher.posting_reference == f"YDISP:{d.dispatch_number}")).scalars().all()
            if d.status == YarnDispatchStatus.APPROVED.value:
                approved += 1
                # (10) consumed == sum allocations
                assert sum(D(str(a.quantity_consumed)) for a in allocs) == D(str(d.lines[0].quantity))
                # (11) exactly one balanced voucher
                assert len(vids) == 1
                jls = s.execute(select(JournalLine).where(JournalLine.voucher_id == vids[0])).scalars().all()
                assert sum(D(str(j.debit)) for j in jls) == sum(D(str(j.credit)) for j in jls)
            else:
                # (11) no voucher / no evidence for a failed op
                assert vids == [] and allocs == []
        assert approved == 3
