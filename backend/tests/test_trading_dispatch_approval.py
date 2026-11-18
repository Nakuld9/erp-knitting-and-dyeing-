"""Stage 4B-3A.1b.2 — trading dispatch approval (FIFO consume + COGS + fail-closed gate).

Covers the CA gate, FIFO COGS, per-layer audit evidence, balanced voucher,
idempotency/inconsistency, over-dispatch, sales-order roll-up, and rollback
atomicity (incl. failure injection). SQLite only; PostgreSQL concurrency is in
test_trading_dispatch_pg_concurrency.py (NOT RUN without a cluster).
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.accounting import AccountType, ChartOfAccount, JournalLine, PostingRule, Voucher
from app.models.inventory import InventoryTransaction, ItemType, StockLayer
from app.models.trading import (
    TradingDispatchFifoAllocation, TradingDispatchStatus, TradingSOStatus,
)
from app.services import inventory, trading
from app.services import trading_sales as ts
from app.services import trading_dispatch as td
from app.services.inventory.service import InsufficientStockError
from app.models.hybrid import WarehouseCategory
from app.models.masters import Warehouse

D = Decimal
OD = date(2026, 11, 14)


def _trade_wh(db, cid, code="WH-APP"):
    w = Warehouse(company_id=cid, code=code, name=code, kind="STORE",
                  category=WarehouseCategory.TRADING.value)
    db.add(w); db.flush()
    return w


def _item(db, cid, code="TF-AP"):
    it = trading.create_trading_item(db, cid, code=code, name=code); db.flush()
    return it


def _configure_delivery_accounts(db, cid):
    """Synthetic TEST fixtures (NOT CA-approved production mappings): a dedicated
    trading COGS account + trading inventory account, mapped to TRADING_DELIVERY."""
    for code, name, typ in [("5155", "Trading COGS", AccountType.EXPENSE.value),
                            ("1105", "Trading Fabric Inventory", AccountType.ASSET.value)]:
        if db.execute(select(ChartOfAccount).where(ChartOfAccount.company_id == cid,
                                                   ChartOfAccount.code == code)).scalar_one_or_none() is None:
            db.add(ChartOfAccount(company_id=cid, code=code, name=name, account_type=typ,
                                  is_group=False, allow_posting=True, is_active=True))
    for slot, code in {"cogs": "5155", "inventory": "1105"}.items():
        if db.execute(select(PostingRule).where(PostingRule.company_id == cid,
                      PostingRule.event_type == "TRADING_DELIVERY", PostingRule.role == slot)).scalar_one_or_none() is None:
            db.add(PostingRule(company_id=cid, event_type="TRADING_DELIVERY", role=slot, account_code=code))
    db.flush()


def _receive(db, cid, item, wh, qty, rate):
    inventory.receive(db, company_id=cid, item_type=ItemType.TRADING_FABRIC, item_id=item.id,
                      qty=qty, unit_cost=rate, warehouse_id=wh.id, event_type="GRN")


def _bal(db, cid, item, wh=None):
    return inventory.balance(db, company_id=cid, item_type=ItemType.TRADING_FABRIC, item_id=item.id,
                             warehouse_id=wh.id if wh else None)["qty_on_hand"]


def _approved_so(db, cid, customer_id, item, wh, qty, number="SO-AP"):
    so = ts.create_trading_so(db, cid, customer_id=customer_id, order_number=number, order_date=OD,
                              lines=[{"trading_item_id": item.id, "warehouse_id": wh.id,
                                      "qty_ordered": qty, "rate": "300"}])
    ts.approve_trading_so(db, cid, so.id); db.flush()
    return so


def _submitted_dispatch(db, cid, customer_id, so, item, wh, qty, number="DSP-AP"):
    d = td.create_trading_dispatch(db, cid, customer_id=customer_id, sales_order_id=so.id,
                                   dispatch_number=number, dispatch_date=OD,
                                   lines=[{"trading_item_id": item.id, "sales_order_line_id": so.lines[0].id,
                                           "warehouse_id": wh.id, "quantity": qty}])
    td.submit_trading_dispatch(db, cid, d.id)
    db.flush()
    return d


def _acct_codes(db, cid, voucher):
    code_by_id = {a.id: a.code for a in db.execute(
        select(ChartOfAccount).where(ChartOfAccount.company_id == cid)).scalars()}
    jls = db.execute(select(JournalLine).where(JournalLine.voucher_id == voucher.id)).scalars().all()
    return jls, code_by_id


# ============================ fail-closed gate ==============================

def test_missing_mappings_block_before_stock_movement(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-NOACCT"); wh = _trade_wh(db, cid)
    _receive(db, cid, it, wh, "100", "120")
    so = _approved_so(db, cid, demo["customer_id"], it, wh, "100", number="SO-NA")
    d = _submitted_dispatch(db, cid, demo["customer_id"], so, it, wh, "80", number="DSP-NA")
    db.commit()
    bal0 = _bal(db, cid, it)
    with pytest.raises(td.TradingDispatchPostingBlocked):
        td.approve_trading_dispatch(db, cid, d.id, approved_by=uuid.uuid4())
    db.rollback()
    assert _bal(db, cid, it) == bal0                       # stock untouched
    assert td.get_trading_dispatch(db, cid, d.id).status == TradingDispatchStatus.SUBMITTED.value
    assert db.execute(select(func.count()).select_from(TradingDispatchFifoAllocation).where(
        TradingDispatchFifoAllocation.dispatch_id == d.id)).scalar() == 0
    assert db.execute(select(func.count()).select_from(Voucher).where(
        Voucher.company_id == cid, Voucher.posting_reference == f"TDISP:{d.dispatch_number}")).scalar() == 0


def test_inactive_account_blocks(db, demo):
    cid = demo["company_id"]
    _configure_delivery_accounts(db, cid)
    # deactivate the COGS account
    acct = db.execute(select(ChartOfAccount).where(ChartOfAccount.company_id == cid,
                                                   ChartOfAccount.code == "5155")).scalar_one()
    acct.is_active = False; db.flush()
    it = _item(db, cid, "TF-INACT"); wh = _trade_wh(db, cid, "WH-IN")
    _receive(db, cid, it, wh, "50", "100")
    so = _approved_so(db, cid, demo["customer_id"], it, wh, "50", number="SO-IA")
    d = _submitted_dispatch(db, cid, demo["customer_id"], so, it, wh, "50", number="DSP-IA")
    db.commit()
    with pytest.raises(td.TradingDispatchPostingBlocked):
        td.approve_trading_dispatch(db, cid, d.id)
    db.rollback()


# ============================ FIFO + COGS ===================================

def test_fifo_multi_layer_cogs_and_evidence(db, demo):
    cid = demo["company_id"]
    _configure_delivery_accounts(db, cid)
    it = _item(db, cid, "TF-FIFO"); wh = _trade_wh(db, cid, "WH-FIFO")
    _receive(db, cid, it, wh, "100", "120")
    _receive(db, cid, it, wh, "100", "130")
    so = _approved_so(db, cid, demo["customer_id"], it, wh, "150", number="SO-FIFO")
    d = _submitted_dispatch(db, cid, demo["customer_id"], so, it, wh, "150", number="DSP-FIFO")
    db.commit()
    res = td.approve_trading_dispatch(db, cid, d.id, approved_by=uuid.uuid4())
    db.commit()
    # COGS = 100*120 + 50*130 = 18500
    assert res["cogs_value"] == D("18500.00")
    d = td.get_trading_dispatch(db, cid, d.id)
    assert d.status == TradingDispatchStatus.APPROVED.value and d.cogs_value == D("18500.00")
    assert d.lines[0].cogs_value == D("18500.00")
    # remaining stock = 50 @ 130
    assert _bal(db, cid, it) == D("50.000000")
    # per-layer evidence: two allocations at 120 and 130
    allocs = db.execute(select(TradingDispatchFifoAllocation).where(
        TradingDispatchFifoAllocation.dispatch_id == d.id)).scalars().all()
    assert len(allocs) == 2
    assert {str(a.unit_cost) for a in allocs} == {"120.00", "130.00"}
    assert sum(D(str(a.total_cost)) for a in allocs) == D("18500.00")
    # voucher balanced, correct accounts, no revenue/GST account
    jls, codes = _acct_codes(db, cid, res["voucher"])
    accts = {codes[j.account_id] for j in jls}
    assert accts == {"5155", "1105"}
    assert sum(D(str(j.debit)) for j in jls) == sum(D(str(j.credit)) for j in jls) == D("18500.00")


def test_insufficient_stock_blocks(db, demo):
    cid = demo["company_id"]
    _configure_delivery_accounts(db, cid)
    it = _item(db, cid, "TF-SHORT"); wh = _trade_wh(db, cid, "WH-SH")
    _receive(db, cid, it, wh, "30", "100")       # only 30 on hand
    so = _approved_so(db, cid, demo["customer_id"], it, wh, "100", number="SO-SH")
    d = _submitted_dispatch(db, cid, demo["customer_id"], so, it, wh, "80", number="DSP-SH")
    db.commit()
    with pytest.raises(InsufficientStockError):
        td.approve_trading_dispatch(db, cid, d.id)
    db.rollback()
    assert _bal(db, cid, it) == D("30.000000")   # untouched
    assert td.get_trading_dispatch(db, cid, d.id).status == TradingDispatchStatus.SUBMITTED.value


def test_consumption_warehouse_and_company_scoped(db, demo):
    cid = demo["company_id"]
    _configure_delivery_accounts(db, cid)
    it = _item(db, cid, "TF-WS"); wh1 = _trade_wh(db, cid, "WH-WS1"); wh2 = _trade_wh(db, cid, "WH-WS2")
    _receive(db, cid, it, wh1, "100", "100")     # stock only in wh1
    so = _approved_so(db, cid, demo["customer_id"], it, wh2, "50", number="SO-WS")  # SO/dispatch on wh2
    d = _submitted_dispatch(db, cid, demo["customer_id"], so, it, wh2, "50", number="DSP-WS")
    db.commit()
    with pytest.raises(InsufficientStockError):   # wh2 has no stock; wh1 not drawn
        td.approve_trading_dispatch(db, cid, d.id)
    db.rollback()
    assert _bal(db, cid, it, wh1) == D("100.000000")


# ============================ idempotency ===================================

def test_approval_idempotent(db, demo):
    cid = demo["company_id"]
    _configure_delivery_accounts(db, cid)
    it = _item(db, cid, "TF-ID"); wh = _trade_wh(db, cid, "WH-ID")
    _receive(db, cid, it, wh, "100", "100")
    so = _approved_so(db, cid, demo["customer_id"], it, wh, "100", number="SO-ID")
    d = _submitted_dispatch(db, cid, demo["customer_id"], so, it, wh, "60", number="DSP-ID")
    db.commit()
    td.approve_trading_dispatch(db, cid, d.id); db.commit()
    r2 = td.approve_trading_dispatch(db, cid, d.id); db.commit()   # idempotent
    assert r2["already_approved"] is True
    # exactly one voucher, one set of allocations, balance reduced once
    assert db.execute(select(func.count()).select_from(Voucher).where(
        Voucher.company_id == cid, Voucher.posting_reference == f"TDISP:{d.dispatch_number}")).scalar() == 1
    assert db.execute(select(func.count()).select_from(TradingDispatchFifoAllocation).where(
        TradingDispatchFifoAllocation.dispatch_id == d.id)).scalar() == 1
    assert _bal(db, cid, it) == D("40.000000")


def test_submitted_with_existing_voucher_fails_safe(db, demo):
    cid = demo["company_id"]
    _configure_delivery_accounts(db, cid)
    it = _item(db, cid, "TF-INC"); wh = _trade_wh(db, cid, "WH-INC")
    _receive(db, cid, it, wh, "100", "100")
    so = _approved_so(db, cid, demo["customer_id"], it, wh, "100", number="SO-INC")
    d = _submitted_dispatch(db, cid, demo["customer_id"], so, it, wh, "50", number="DSP-INC")
    db.commit()
    # fabricate a voucher at the dispatch's reference without approving the dispatch
    from app.services.accounting import posting_engine as pe
    pe.post_voucher(db, pe.PostingRequest(
        company_id=cid, voucher_type_code="SJ", voucher_date=OD,
        posting_reference=f"TDISP:{d.dispatch_number}", narration="stray",
        lines=[pe.PostingLine("5155", debit=D("1")), pe.PostingLine("1105", credit=D("1"))]))
    db.commit()
    with pytest.raises(td.TradingDispatchInconsistent):
        td.approve_trading_dispatch(db, cid, d.id)
    db.rollback()
    assert _bal(db, cid, it) == D("100.000000")   # never reconsumed


# ============================ over-dispatch =================================

def test_over_dispatch_blocked_without_authorisation(db, demo):
    """A first approval shrinks the line outstanding (SO stays PARTIAL); a second
    dispatch created for the old outstanding then exceeds it and needs authorisation."""
    cid = demo["company_id"]
    _configure_delivery_accounts(db, cid)
    it = _item(db, cid, "TF-OVR"); wh = _trade_wh(db, cid, "WH-OVR")
    _receive(db, cid, it, wh, "200", "100")
    so = _approved_so(db, cid, demo["customer_id"], it, wh, "100", number="SO-OVR")
    d1 = _submitted_dispatch(db, cid, demo["customer_id"], so, it, wh, "60", number="DSP-OV1")
    d2 = _submitted_dispatch(db, cid, demo["customer_id"], so, it, wh, "60", number="DSP-OV2")
    db.commit()
    td.approve_trading_dispatch(db, cid, d1.id); db.commit()   # outstanding now 40; SO PARTIAL (open)
    with pytest.raises(td.TradingDispatchOverDispatchError):
        td.approve_trading_dispatch(db, cid, d2.id, over_dispatch_authorized=False)  # 60 > 40
    db.rollback()
    # authorised but no reason -> still blocked
    with pytest.raises(td.TradingDispatchOverDispatchError):
        td.approve_trading_dispatch(db, cid, d2.id, over_dispatch_authorized=True, over_dispatch_reason="  ")
    db.rollback()
    # authorised + reason -> approved and flagged
    res = td.approve_trading_dispatch(db, cid, d2.id, approved_by=uuid.uuid4(),
                                      over_dispatch_authorized=True, over_dispatch_reason="customer agreed extra")
    db.commit()
    d2 = td.get_trading_dispatch(db, cid, d2.id)
    assert d2.is_over_dispatched and d2.over_dispatch_reason == "customer agreed extra"


# ============================ sales-order roll-up ===========================

def test_so_rollup_partial_then_completed(db, demo):
    cid = demo["company_id"]
    _configure_delivery_accounts(db, cid)
    it = _item(db, cid, "TF-RU"); wh = _trade_wh(db, cid, "WH-RU")
    _receive(db, cid, it, wh, "100", "100")
    so = _approved_so(db, cid, demo["customer_id"], it, wh, "100", number="SO-RU")
    d1 = _submitted_dispatch(db, cid, demo["customer_id"], so, it, wh, "60", number="DSP-RU1")
    db.commit()
    td.approve_trading_dispatch(db, cid, d1.id); db.commit()
    so = ts.get_trading_so(db, cid, so.id)
    assert so.status == TradingSOStatus.PARTIAL.value and so.lines[0].qty_dispatched == D("60.000000")
    d2 = _submitted_dispatch(db, cid, demo["customer_id"], so, it, wh, "40", number="DSP-RU2")
    db.commit()
    td.approve_trading_dispatch(db, cid, d2.id); db.commit()
    so = ts.get_trading_so(db, cid, so.id)
    assert so.status == TradingSOStatus.COMPLETED.value and so.lines[0].qty_dispatched == D("100.000000")


def test_draft_reject_and_so_unchanged(db, demo):
    cid = demo["company_id"]
    _configure_delivery_accounts(db, cid)
    it = _item(db, cid, "TF-DR"); wh = _trade_wh(db, cid, "WH-DR")
    _receive(db, cid, it, wh, "100", "100")
    so = _approved_so(db, cid, demo["customer_id"], it, wh, "100", number="SO-DR")
    d = td.create_trading_dispatch(db, cid, customer_id=demo["customer_id"], sales_order_id=so.id,
                                   dispatch_number="DSP-DR", dispatch_date=OD,
                                   lines=[{"trading_item_id": it.id, "sales_order_line_id": so.lines[0].id,
                                           "warehouse_id": wh.id, "quantity": "50"}])
    db.commit()
    with pytest.raises(td.TradingDispatchError):   # DRAFT (not submitted) cannot be approved
        td.approve_trading_dispatch(db, cid, d.id)
    db.rollback()
    assert ts.get_trading_so(db, cid, so.id).lines[0].qty_dispatched == D("0.000000")


# ============================ rollback atomicity ============================

def test_voucher_failure_rolls_back_consumption(db, demo, monkeypatch):
    cid = demo["company_id"]
    _configure_delivery_accounts(db, cid)
    it = _item(db, cid, "TF-RB"); wh = _trade_wh(db, cid, "WH-RB")
    _receive(db, cid, it, wh, "100", "100")
    so = _approved_so(db, cid, demo["customer_id"], it, wh, "100", number="SO-RB")
    d = _submitted_dispatch(db, cid, demo["customer_id"], so, it, wh, "80", number="DSP-RB")
    db.commit()
    bal0 = _bal(db, cid, it)
    # inject a failure at voucher posting (after FIFO consume + allocations are flushed)
    monkeypatch.setattr(td.pe, "post_voucher", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        td.approve_trading_dispatch(db, cid, d.id)
    db.rollback()
    # everything rolled back: stock restored, no allocations, dispatch still SUBMITTED, SO unchanged
    assert _bal(db, cid, it) == bal0
    assert db.execute(select(func.count()).select_from(TradingDispatchFifoAllocation).where(
        TradingDispatchFifoAllocation.dispatch_id == d.id)).scalar() == 0
    assert td.get_trading_dispatch(db, cid, d.id).status == TradingDispatchStatus.SUBMITTED.value
    assert ts.get_trading_so(db, cid, so.id).lines[0].qty_dispatched == D("0.000000")
    assert db.execute(select(func.count()).select_from(InventoryTransaction).where(
        InventoryTransaction.company_id == cid, InventoryTransaction.posting_reference == f"TDISP:{d.dispatch_number}")).scalar() == 0
