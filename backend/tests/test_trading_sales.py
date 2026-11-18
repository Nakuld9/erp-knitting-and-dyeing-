"""Stage 4B-3A.1a — trading sales order service tests.

Order lifecycle, validation, company/factory isolation, informational
availability, and strict isolation from stock movement and accounting.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.accounting import JournalLine, Voucher
from app.models.core import Branch, Company
from app.models.hybrid import WarehouseCategory
from app.models.inventory import InventoryTransaction, ItemType, StockLayer
from app.models.masters import Customer, Warehouse
from app.models.trading import TradingSalesOrder, TradingSalesOrderLine, TradingSOStatus
from app.services import inventory, trading
from app.services import trading_sales as ts

D = Decimal
OD = date(2026, 11, 12)


def _wh(db, cid, code, category):
    w = Warehouse(company_id=cid, code=code, name=code, kind="STORE", category=category)
    db.add(w); db.flush()
    return w


def _trade_wh(db, cid, code="WH-SELL"):
    return _wh(db, cid, code, WarehouseCategory.TRADING.value)


def _item(db, cid, code="TF-S", active=True):
    it = trading.create_trading_item(db, cid, code=code, name=code, is_active=active); db.flush()
    return it


def _counts(db, cid):
    return {
        "layers": db.execute(select(func.count()).select_from(StockLayer).where(StockLayer.company_id == cid)).scalar(),
        "txns": db.execute(select(func.count()).select_from(InventoryTransaction).where(InventoryTransaction.company_id == cid)).scalar(),
        "vouchers": db.execute(select(func.count()).select_from(Voucher).where(Voucher.company_id == cid)).scalar(),
        "jlines": db.execute(select(func.count()).select_from(JournalLine)).scalar(),
    }


def _line(it, wh, qty="100", rate="200", discount="0"):
    return {"trading_item_id": it.id, "warehouse_id": wh.id, "qty_ordered": qty, "rate": rate, "discount": discount}


# ------------------------------- lifecycle ----------------------------------

def test_create_multi_sku_order(db, demo):
    cid = demo["company_id"]
    a, b = _item(db, cid, "TF-A"), _item(db, cid, "TF-B"); wh = _trade_wh(db, cid); db.commit()
    so = ts.create_trading_so(db, cid, customer_id=demo["customer_id"], order_number="TSO-1",
                              order_date=OD, lines=[_line(a, wh, "100"), _line(b, wh, "50")])
    db.commit()
    assert so.status == TradingSOStatus.DRAFT.value and len(so.lines) == 2


def test_company_scoped_number_uniqueness(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-NUM"); wh = _trade_wh(db, cid); db.commit()
    ts.create_trading_so(db, cid, customer_id=demo["customer_id"], order_number="DUP",
                         order_date=OD, lines=[_line(it, wh)])
    db.commit()
    with pytest.raises(Exception):
        ts.create_trading_so(db, cid, customer_id=demo["customer_id"], order_number="DUP",
                             order_date=OD, lines=[_line(it, wh)])
        db.commit()
    db.rollback()


def test_approve_idempotent(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-AP"); wh = _trade_wh(db, cid); db.commit()
    so = ts.create_trading_so(db, cid, customer_id=demo["customer_id"], order_number="TSO-AP",
                              order_date=OD, lines=[_line(it, wh)])
    db.commit()
    ts.approve_trading_so(db, cid, so.id, approved_by=uuid.uuid4()); db.commit()
    assert ts.get_trading_so(db, cid, so.id).status == TradingSOStatus.APPROVED.value
    before = ts.get_trading_so(db, cid, so.id).approved_at
    ts.approve_trading_so(db, cid, so.id); db.commit()        # idempotent, no error
    assert ts.get_trading_so(db, cid, so.id).approved_at == before


def test_cancel_and_invalid_transitions(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-CX"); wh = _trade_wh(db, cid); db.commit()
    so = ts.create_trading_so(db, cid, customer_id=demo["customer_id"], order_number="TSO-CX",
                              order_date=OD, lines=[_line(it, wh)])
    db.commit()
    ts.cancel_trading_so(db, cid, so.id); db.commit()
    assert ts.get_trading_so(db, cid, so.id).status == TradingSOStatus.CANCELLED.value
    # cannot approve a cancelled order
    with pytest.raises(ts.TradingSalesError):
        ts.approve_trading_so(db, cid, so.id)
    db.rollback()
    # cannot cancel again
    with pytest.raises(ts.TradingSalesError):
        ts.cancel_trading_so(db, cid, so.id)
    db.rollback()


def test_cancel_rejected_when_dispatched(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-DSP"); wh = _trade_wh(db, cid); db.commit()
    so = ts.create_trading_so(db, cid, customer_id=demo["customer_id"], order_number="TSO-DSP",
                              order_date=OD, lines=[_line(it, wh)])
    # simulate a (future-stage) dispatched quantity directly on the line
    so.lines[0].qty_dispatched = D("10"); db.commit()
    with pytest.raises(ts.TradingSalesError):
        ts.cancel_trading_so(db, cid, so.id)
    db.rollback()


def test_outstanding_calculation(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-OUT"); wh = _trade_wh(db, cid); db.commit()
    so = ts.create_trading_so(db, cid, customer_id=demo["customer_id"], order_number="TSO-OUT",
                              order_date=OD, lines=[_line(it, wh, "100")])
    so.lines[0].qty_dispatched = D("30"); so.lines[0].qty_cancelled = D("20"); db.commit()
    out = ts.so_outstanding(ts.get_trading_so(db, cid, so.id))
    assert out[0]["outstanding"] == D("50.000000")


# ------------------------------ validation ----------------------------------

def test_zero_quantity_rejected(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-Z"); wh = _trade_wh(db, cid); db.commit()
    with pytest.raises(ts.TradingSalesError):
        ts.create_trading_so(db, cid, customer_id=demo["customer_id"], order_number="TSO-Z",
                             order_date=OD, lines=[_line(it, wh, "0")])
    db.rollback()


def test_inactive_sku_rejected(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-INACT", active=False); wh = _trade_wh(db, cid); db.commit()
    with pytest.raises(ts.TradingSalesError):
        ts.create_trading_so(db, cid, customer_id=demo["customer_id"], order_number="TSO-IN",
                             order_date=OD, lines=[_line(it, wh)])
    db.rollback()


@pytest.mark.parametrize("cat", ["GREIGE", "SEMI_FINISHED", "FINISHED_GOODS", "YARN_STORE",
                                 "VENDOR_CUSTODY", None])
def test_non_trading_warehouse_rejected(db, demo, cat):
    cid = demo["company_id"]
    it = _item(db, cid, f"TF-W{cat or 'N'}"); wh = _wh(db, cid, f"WS-{cat or 'NONE'}", cat); db.commit()
    with pytest.raises(ts.TradingSalesError):
        ts.create_trading_so(db, cid, customer_id=demo["customer_id"], order_number=f"TSO-W{cat or 'N'}",
                             order_date=OD, lines=[_line(it, wh)])
    db.rollback()


def test_cross_company_references_rejected(db, demo):
    cid = demo["company_id"]
    other = Company(code=f"OC-{uuid.uuid4().hex[:4]}", name="Other"); db.add(other); db.flush()
    foreign_item = trading.create_trading_item(db, other.id, code="FX", name="FX")
    foreign_cust = Customer(company_id=other.id, code="FC", name="FC"); db.add(foreign_cust)
    foreign_wh = _wh(db, other.id, "FW", WarehouseCategory.TRADING.value)
    local_item = _item(db, cid, "TF-LOC"); local_wh = _trade_wh(db, cid); db.commit()
    # cross-company customer
    with pytest.raises(ts.TradingSalesError):
        ts.create_trading_so(db, cid, customer_id=foreign_cust.id, order_number="X1",
                             order_date=OD, lines=[_line(local_item, local_wh)])
    db.rollback()
    # cross-company SKU
    with pytest.raises(ts.TradingSalesError):
        ts.create_trading_so(db, cid, customer_id=demo["customer_id"], order_number="X2",
                             order_date=OD, lines=[_line(foreign_item, local_wh)])
    db.rollback()
    # cross-company warehouse
    with pytest.raises(ts.TradingSalesError):
        ts.create_trading_so(db, cid, customer_id=demo["customer_id"], order_number="X3",
                             order_date=OD, lines=[_line(local_item, foreign_wh)])
    db.rollback()


def test_invalid_tax_code_and_uom_rejected(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-TX"); wh = _trade_wh(db, cid); db.commit()
    ln = _line(it, wh); ln["tax_code_id"] = uuid.uuid4()
    with pytest.raises(ts.TradingSalesError):
        ts.create_trading_so(db, cid, customer_id=demo["customer_id"], order_number="TSO-TX",
                             order_date=OD, lines=[ln])
    db.rollback()


def test_wrong_factory_warehouse_rejected(db, demo):
    cid = demo["company_id"]
    b1 = Branch(company_id=cid, code="SF1", name="F1"); b2 = Branch(company_id=cid, code="SF2", name="F2")
    db.add_all([b1, b2]); db.flush()
    it = _item(db, cid, "TF-FAC")
    wh = Warehouse(company_id=cid, code="WH-SF1", name="WH-SF1", kind="STORE",
                   category=WarehouseCategory.TRADING.value, branch_id=b1.id)
    db.add(wh); db.commit()
    with pytest.raises(ts.TradingSalesError):
        ts.create_trading_so(db, cid, customer_id=demo["customer_id"], order_number="TSO-FAC",
                             order_date=OD, factory_id=b2.id, lines=[_line(it, wh)])
    db.rollback()


# ---------------------- availability + strict isolation ---------------------

def test_availability_is_informational_and_moves_no_stock(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-AV"); wh = _trade_wh(db, cid); db.commit()
    # seed on-hand directly through the inventory engine (not via the SO service)
    inventory.receive(db, company_id=cid, item_type=ItemType.TRADING_FABRIC, item_id=it.id,
                      qty="250", unit_cost="100", warehouse_id=wh.id, event_type="GRN")
    db.commit()
    av = ts.availability(db, cid, it.id, wh.id)
    assert av["on_hand_qty"] == D("250.000000")
    assert av["informational_only"] is True and av["is_guaranteed_allocation"] is False
    assert av["is_reservation"] is False


def test_order_create_and_approve_move_no_stock_and_post_nothing(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-ISO"); wh = _trade_wh(db, cid)
    inventory.receive(db, company_id=cid, item_type=ItemType.TRADING_FABRIC, item_id=it.id,
                      qty="100", unit_cost="100", warehouse_id=wh.id, event_type="GRN")
    db.commit()
    before = _counts(db, cid)
    so = ts.create_trading_so(db, cid, customer_id=demo["customer_id"], order_number="TSO-ISO",
                              order_date=OD, lines=[_line(it, wh, "100")])
    db.commit()
    ts.approve_trading_so(db, cid, so.id); db.commit()
    after = _counts(db, cid)
    # no new stock layer, no inventory transaction, no voucher, no journal line
    assert after == before
    # no reservation columns/tables exist on the line model
    assert not any(c.name.startswith("qty_reserved") or "reserv" in c.name.lower()
                   for c in TradingSalesOrderLine.__table__.columns)


def test_no_reservation_model_exists():
    import app.models.trading as m
    assert not hasattr(m, "TradingStockReservation")
    assert "trading_stock_reservations" not in {t for t in __import__("app.models", fromlist=["Base"]).Base.metadata.tables}
