"""Stage 4B-3A.1b.1 — trading dispatch service tests (draft lifecycle only).

Covers creation/validation against an approved sales order, submit/cancel
lifecycle, company/factory isolation, and strict isolation from any stock
movement or accounting posting.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.accounting import JournalLine, Voucher
from app.models.core import Branch, Company
from app.models.genealogy import RollGenealogyEdge
from app.models.hybrid import WarehouseCategory
from app.models.inventory import InventoryTransaction, ItemType, StockLayer
from app.models.manufacturing import FabricRoll
from app.models.masters import Customer, Warehouse
from app.models.trading import (
    TradingDispatch, TradingDispatchLine, TradingDispatchStatus, TradingSOStatus,
)
from app.services import inventory, trading
from app.services import trading_sales as ts
from app.services import trading_dispatch as td

D = Decimal
OD = date(2026, 11, 13)


def _wh(db, cid, code, category):
    w = Warehouse(company_id=cid, code=code, name=code, kind="STORE", category=category)
    db.add(w); db.flush()
    return w


def _trade_wh(db, cid, code="WH-DSP"):
    return _wh(db, cid, code, WarehouseCategory.TRADING.value)


def _item(db, cid, code="TF-D", active=True):
    it = trading.create_trading_item(db, cid, code=code, name=code, is_active=active); db.flush()
    return it


def _approved_so(db, cid, customer_id, items_whs_qtys, number="SO-D", factory_id=None):
    lines = [{"trading_item_id": it.id, "warehouse_id": wh.id, "qty_ordered": q, "rate": "200"}
             for (it, wh, q) in items_whs_qtys]
    so = ts.create_trading_so(db, cid, customer_id=customer_id, order_number=number,
                              order_date=OD, factory_id=factory_id, lines=lines)
    ts.approve_trading_so(db, cid, so.id)
    db.flush()
    return so


def _counts(db, cid):
    return {
        "layers": db.execute(select(func.count()).select_from(StockLayer).where(StockLayer.company_id == cid)).scalar(),
        "txns": db.execute(select(func.count()).select_from(InventoryTransaction).where(InventoryTransaction.company_id == cid)).scalar(),
        "vouchers": db.execute(select(func.count()).select_from(Voucher).where(Voucher.company_id == cid)).scalar(),
        "jlines": db.execute(select(func.count()).select_from(JournalLine)).scalar(),
        "rolls": db.execute(select(func.count()).select_from(FabricRoll).where(FabricRoll.company_id == cid)).scalar(),
        "edges": db.execute(select(func.count()).select_from(RollGenealogyEdge).where(RollGenealogyEdge.company_id == cid)).scalar(),
    }


def _dline(so, idx, qty):
    l = so.lines[idx]
    return {"trading_item_id": l.trading_item_id, "sales_order_line_id": l.id,
            "warehouse_id": l.warehouse_id, "quantity": qty}


# ------------------------------- creation -----------------------------------

def test_create_draft_against_approved_so_multi_sku(db, demo):
    cid = demo["company_id"]
    a, b = _item(db, cid, "TF-A"), _item(db, cid, "TF-B"); wh = _trade_wh(db, cid)
    so = _approved_so(db, cid, demo["customer_id"], [(a, wh, "100"), (b, wh, "50")]); db.commit()
    d = td.create_trading_dispatch(db, cid, customer_id=demo["customer_id"], sales_order_id=so.id,
                                   dispatch_number="DSP-1", dispatch_date=OD,
                                   lines=[_dline(so, 0, "60"), _dline(so, 1, "20")])
    db.commit()
    assert d.status == TradingDispatchStatus.DRAFT.value and len(d.lines) == 2


def test_multiple_dispatches_within_outstanding(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-M"); wh = _trade_wh(db, cid)
    so = _approved_so(db, cid, demo["customer_id"], [(it, wh, "100")]); db.commit()
    td.create_trading_dispatch(db, cid, customer_id=demo["customer_id"], sales_order_id=so.id,
                               dispatch_number="DSP-M1", dispatch_date=OD, lines=[_dline(so, 0, "60")])
    td.create_trading_dispatch(db, cid, customer_id=demo["customer_id"], sales_order_id=so.id,
                               dispatch_number="DSP-M2", dispatch_date=OD, lines=[_dline(so, 0, "40")])
    db.commit()
    # SO dispatched qty is NOT incremented in this stage (no approval)
    assert ts.get_trading_so(db, cid, so.id).lines[0].qty_dispatched == D("0.000000")


def test_qty_exceeds_outstanding_rejected(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-EX"); wh = _trade_wh(db, cid)
    so = _approved_so(db, cid, demo["customer_id"], [(it, wh, "100")]); db.commit()
    with pytest.raises(td.TradingDispatchError):
        td.create_trading_dispatch(db, cid, customer_id=demo["customer_id"], sales_order_id=so.id,
                                   dispatch_number="DSP-EX", dispatch_date=OD, lines=[_dline(so, 0, "150")])
    db.rollback()


def test_zero_quantity_rejected(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-ZQ"); wh = _trade_wh(db, cid)
    so = _approved_so(db, cid, demo["customer_id"], [(it, wh, "100")]); db.commit()
    with pytest.raises(td.TradingDispatchError):
        td.create_trading_dispatch(db, cid, customer_id=demo["customer_id"], sales_order_id=so.id,
                                   dispatch_number="DSP-ZQ", dispatch_date=OD, lines=[_dline(so, 0, "0")])
    db.rollback()


def test_draft_or_cancelled_so_rejected(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-DS"); wh = _trade_wh(db, cid)
    so = ts.create_trading_so(db, cid, customer_id=demo["customer_id"], order_number="SO-DRAFT",
                              order_date=OD, lines=[{"trading_item_id": it.id, "warehouse_id": wh.id,
                                                     "qty_ordered": "100", "rate": "1"}])
    db.commit()
    with pytest.raises(td.TradingDispatchError):   # SO still DRAFT
        td.create_trading_dispatch(db, cid, customer_id=demo["customer_id"], sales_order_id=so.id,
                                   dispatch_number="DSP-DR", dispatch_date=OD, lines=[_dline(so, 0, "10")])
    db.rollback()


def test_customer_mismatch_rejected(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-CM"); wh = _trade_wh(db, cid)
    so = _approved_so(db, cid, demo["customer_id"], [(it, wh, "100")]); db.flush()
    other_cust = Customer(company_id=cid, code="CUST2", name="Cust2"); db.add(other_cust); db.commit()
    with pytest.raises(td.TradingDispatchError):
        td.create_trading_dispatch(db, cid, customer_id=other_cust.id, sales_order_id=so.id,
                                   dispatch_number="DSP-CM", dispatch_date=OD, lines=[_dline(so, 0, "10")])
    db.rollback()


def test_so_line_from_another_order_rejected(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-OL"); wh = _trade_wh(db, cid)
    so1 = _approved_so(db, cid, demo["customer_id"], [(it, wh, "100")], number="SO-OL1")
    so2 = _approved_so(db, cid, demo["customer_id"], [(it, wh, "100")], number="SO-OL2"); db.commit()
    bad = {"trading_item_id": it.id, "sales_order_line_id": so2.lines[0].id,
           "warehouse_id": wh.id, "quantity": "10"}
    with pytest.raises(td.TradingDispatchError):
        td.create_trading_dispatch(db, cid, customer_id=demo["customer_id"], sales_order_id=so1.id,
                                   dispatch_number="DSP-OL", dispatch_date=OD, lines=[bad])
    db.rollback()


def test_sku_mismatch_rejected(db, demo):
    cid = demo["company_id"]
    a, b = _item(db, cid, "TF-SA"), _item(db, cid, "TF-SB"); wh = _trade_wh(db, cid)
    so = _approved_so(db, cid, demo["customer_id"], [(a, wh, "100")]); db.commit()
    bad = {"trading_item_id": b.id, "sales_order_line_id": so.lines[0].id,
           "warehouse_id": wh.id, "quantity": "10"}
    with pytest.raises(td.TradingDispatchError):
        td.create_trading_dispatch(db, cid, customer_id=demo["customer_id"], sales_order_id=so.id,
                                   dispatch_number="DSP-SK", dispatch_date=OD, lines=[bad])
    db.rollback()


def test_warehouse_mismatch_rejected(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-WM"); wh1 = _trade_wh(db, cid, "WH-WM1"); wh2 = _trade_wh(db, cid, "WH-WM2")
    so = _approved_so(db, cid, demo["customer_id"], [(it, wh1, "100")]); db.commit()
    bad = {"trading_item_id": it.id, "sales_order_line_id": so.lines[0].id,
           "warehouse_id": wh2.id, "quantity": "10"}
    with pytest.raises(td.TradingDispatchError):
        td.create_trading_dispatch(db, cid, customer_id=demo["customer_id"], sales_order_id=so.id,
                                   dispatch_number="DSP-WM", dispatch_date=OD, lines=[bad])
    db.rollback()


def test_cross_company_so_rejected(db, demo):
    cid = demo["company_id"]
    other = Company(code=f"OC-{uuid.uuid4().hex[:4]}", name="Other"); db.add(other); db.flush()
    o_cust = Customer(company_id=other.id, code="OCU", name="OCU"); db.add(o_cust)
    o_item = trading.create_trading_item(db, other.id, code="OIT", name="OIT")
    o_wh = _wh(db, other.id, "OWH", WarehouseCategory.TRADING.value)
    o_so = _approved_so(db, other.id, o_cust.id, [(o_item, o_wh, "100")], number="OSO"); db.commit()
    with pytest.raises(td.TradingDispatchError):   # another company's SO invisible
        td.create_trading_dispatch(db, cid, customer_id=demo["customer_id"], sales_order_id=o_so.id,
                                   dispatch_number="DSP-XC", dispatch_date=OD,
                                   lines=[{"trading_item_id": o_item.id, "sales_order_line_id": o_so.lines[0].id,
                                           "warehouse_id": o_wh.id, "quantity": "10"}])
    db.rollback()


def test_wrong_factory_rejected(db, demo):
    cid = demo["company_id"]
    b1 = Branch(company_id=cid, code="DF1", name="F1"); b2 = Branch(company_id=cid, code="DF2", name="F2")
    db.add_all([b1, b2]); db.flush()
    it = _item(db, cid, "TF-FC")
    wh = Warehouse(company_id=cid, code="WH-DF1", name="WH-DF1", kind="STORE",
                   category=WarehouseCategory.TRADING.value, branch_id=b1.id)
    db.add(wh); db.flush()
    so = _approved_so(db, cid, demo["customer_id"], [(it, wh, "100")], factory_id=b1.id); db.commit()
    # dispatch claims factory 2 but SO/warehouse are factory 1
    with pytest.raises(td.TradingDispatchError):
        td.create_trading_dispatch(db, cid, customer_id=demo["customer_id"], sales_order_id=so.id,
                                   dispatch_number="DSP-FC", dispatch_date=OD, factory_id=b2.id,
                                   lines=[_dline(so, 0, "10")])
    db.rollback()


# ------------------------------- lifecycle ----------------------------------

def test_submit_idempotent_and_cancel(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-LC"); wh = _trade_wh(db, cid)
    so = _approved_so(db, cid, demo["customer_id"], [(it, wh, "100")]); db.commit()
    d = td.create_trading_dispatch(db, cid, customer_id=demo["customer_id"], sales_order_id=so.id,
                                   dispatch_number="DSP-LC", dispatch_date=OD, lines=[_dline(so, 0, "50")])
    db.commit()
    td.submit_trading_dispatch(db, cid, d.id, submitted_by=uuid.uuid4()); db.commit()
    at = td.get_trading_dispatch(db, cid, d.id).submitted_at
    td.submit_trading_dispatch(db, cid, d.id); db.commit()      # idempotent
    assert td.get_trading_dispatch(db, cid, d.id).submitted_at == at
    td.cancel_trading_dispatch(db, cid, d.id); db.commit()
    assert td.get_trading_dispatch(db, cid, d.id).status == TradingDispatchStatus.CANCELLED.value
    # cannot submit a cancelled dispatch
    with pytest.raises(td.TradingDispatchError):
        td.submit_trading_dispatch(db, cid, d.id)
    db.rollback()


def test_cancel_invalid_status_rejected(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-CI"); wh = _trade_wh(db, cid)
    so = _approved_so(db, cid, demo["customer_id"], [(it, wh, "100")]); db.commit()
    d = td.create_trading_dispatch(db, cid, customer_id=demo["customer_id"], sales_order_id=so.id,
                                   dispatch_number="DSP-CI", dispatch_date=OD, lines=[_dline(so, 0, "10")])
    td.cancel_trading_dispatch(db, cid, d.id); db.commit()
    with pytest.raises(td.TradingDispatchError):   # already cancelled
        td.cancel_trading_dispatch(db, cid, d.id)
    db.rollback()


# ------------------- no stock / no accounting isolation ---------------------

def test_lifecycle_moves_no_stock_and_posts_nothing(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-ISO"); wh = _trade_wh(db, cid)
    inventory.receive(db, company_id=cid, item_type=ItemType.TRADING_FABRIC, item_id=it.id,
                      qty="100", unit_cost="100", warehouse_id=wh.id, event_type="GRN")
    so = _approved_so(db, cid, demo["customer_id"], [(it, wh, "100")]); db.commit()
    before = _counts(db, cid)
    d = td.create_trading_dispatch(db, cid, customer_id=demo["customer_id"], sales_order_id=so.id,
                                   dispatch_number="DSP-ISO", dispatch_date=OD, lines=[_dline(so, 0, "80")])
    db.commit()
    td.submit_trading_dispatch(db, cid, d.id); db.commit()
    td.cancel_trading_dispatch(db, cid, d.id); db.commit()
    after = _counts(db, cid)
    assert after == before      # no layer/txn/voucher/jline/roll/edge change through the whole lifecycle
    # SO dispatched qty untouched
    assert ts.get_trading_so(db, cid, so.id).lines[0].qty_dispatched == D("0.000000")


def test_no_reserved_or_invoice_or_fifo_ref_columns():
    # 4B-3A.1b.2 adds cogs_value to the line (set only on approval); it must NOT
    # introduce reservations, invoice allocation (that is 1c), or a per-line FIFO
    # layer/posting reference (FIFO evidence lives in a separate allocations table).
    cols = {c.name for c in TradingDispatchLine.__table__.columns}
    assert not any("reserv" in c.lower() for c in cols)
    assert "qty_invoiced" not in cols
    assert "layer_id" not in cols and "posting_reference" not in cols
