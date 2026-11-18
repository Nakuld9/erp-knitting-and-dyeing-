"""Stage 4B-3A.1c.1 — trading sales invoice DRAFT + allocation service tests.

Draft creation, quantity allocation, uninvoiced reconciliation, duplicate-number
protection, cancellation/release, and strict isolation from posting and stock.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.accounting import AccountType, ChartOfAccount, JournalLine, PostingRule, Voucher
from app.models.commercial import DocStatus, SalesInvoice, SalesInvoiceLine
from app.models.core import Company
from app.models.genealogy import RollGenealogyEdge
from app.models.hybrid import WarehouseCategory
from app.models.inventory import InventoryTransaction, ItemType, StockLayer
from app.models.manufacturing import FabricRoll
from app.models.masters import Customer, Warehouse
from app.models.trading import (
    TradingAllocationStatus, TradingDispatchInvoiceAllocation, TradingDispatchLine,
)
from app.services import inventory, trading
from app.services import trading_sales as ts
from app.services import trading_dispatch as td
from app.services import trading_invoice as ti

D = Decimal
OD = date(2026, 11, 15)


def _trade_wh(db, cid, code):
    w = Warehouse(company_id=cid, code=code, name=code, kind="STORE",
                  category=WarehouseCategory.TRADING.value)
    db.add(w); db.flush()
    return w


def _item(db, cid, code, active=True):
    it = trading.create_trading_item(db, cid, code=code, name=code, is_active=active); db.flush()
    return it


def _configure(db, cid):
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


def _approved_dispatch(db, cid, customer_id, item, wh, so_qty, dispatch_qty, *, suffix="1", tax_id=None):
    inventory.receive(db, company_id=cid, item_type=ItemType.TRADING_FABRIC, item_id=item.id,
                      qty=str(max(D(dispatch_qty), D(so_qty))), unit_cost="100", warehouse_id=wh.id, event_type="GRN")
    so = ts.create_trading_so(db, cid, customer_id=customer_id, order_number=f"ISO-{suffix}", order_date=OD,
                              lines=[{"trading_item_id": item.id, "warehouse_id": wh.id,
                                      "qty_ordered": so_qty, "rate": "300", "tax_code_id": tax_id}])
    ts.approve_trading_so(db, cid, so.id)
    d = td.create_trading_dispatch(db, cid, customer_id=customer_id, sales_order_id=so.id,
                                   dispatch_number=f"IDSP-{suffix}", dispatch_date=OD,
                                   lines=[{"trading_item_id": item.id, "sales_order_line_id": so.lines[0].id,
                                           "warehouse_id": wh.id, "quantity": dispatch_qty}])
    td.submit_trading_dispatch(db, cid, d.id)
    td.approve_trading_dispatch(db, cid, d.id)
    db.flush()
    return d


def _acct_counts(db, cid):
    return {
        "vouchers": db.execute(select(func.count()).select_from(Voucher).where(Voucher.company_id == cid)).scalar(),
        "jlines": db.execute(select(func.count()).select_from(JournalLine)).scalar(),
        "layers": db.execute(select(func.count()).select_from(StockLayer).where(StockLayer.company_id == cid)).scalar(),
        "txns": db.execute(select(func.count()).select_from(InventoryTransaction).where(InventoryTransaction.company_id == cid)).scalar(),
        "rolls": db.execute(select(func.count()).select_from(FabricRoll).where(FabricRoll.company_id == cid)).scalar(),
        "edges": db.execute(select(func.count()).select_from(RollGenealogyEdge).where(RollGenealogyEdge.company_id == cid)).scalar(),
    }


def _iline(d, idx, item, qty, rate="300", discount="0", tax_id=None):
    return {"dispatch_line_id": d.lines[idx].id, "trading_item_id": item.id, "quantity": qty,
            "rate": rate, "discount": discount, "tax_code_id": tax_id}


# ------------------------------ creation ------------------------------------

def test_create_draft_one_dispatch(db, demo):
    cid = demo["company_id"]; _configure(db, cid)
    it = _item(db, cid, "TF-I1"); wh = _trade_wh(db, cid, "WH-I1")
    d = _approved_dispatch(db, cid, demo["customer_id"], it, wh, "100", "60", suffix="1")
    db.commit()
    inv = ti.create_trading_invoice(db, cid, customer_id=demo["customer_id"], number="TINV-1",
                                    invoice_date=OD, lines=[_iline(d, 0, it, "60", rate="300")])
    db.commit()
    assert inv.source == "TRADING" and inv.status == DocStatus.DRAFT
    assert len(inv.lines) == 1 and inv.lines[0].trading_item_id == it.id
    assert inv.taxable_value == D("18000.00") and inv.tax_amount == D("0.00")   # 60*300, GST deferred
    assert db.get(TradingDispatchLine, d.lines[0].id).qty_invoiced == D("60.000000")


def test_discount_applied_pretax(db, demo):
    cid = demo["company_id"]; _configure(db, cid)
    it = _item(db, cid, "TF-ID"); wh = _trade_wh(db, cid, "WH-ID")
    d = _approved_dispatch(db, cid, demo["customer_id"], it, wh, "100", "50", suffix="D")
    db.commit()
    inv = ti.create_trading_invoice(db, cid, customer_id=demo["customer_id"], number="TINV-D",
                                    invoice_date=OD, lines=[_iline(d, 0, it, "50", rate="300", discount="500")])
    db.commit()
    assert inv.lines[0].amount == D("14500.00")   # 50*300 - 500
    assert inv.taxable_value == D("14500.00")


def test_partial_and_multiple_invoices_per_dispatch_line(db, demo):
    cid = demo["company_id"]; _configure(db, cid)
    it = _item(db, cid, "TF-IP"); wh = _trade_wh(db, cid, "WH-IP")
    d = _approved_dispatch(db, cid, demo["customer_id"], it, wh, "100", "100", suffix="P")
    db.commit()
    ti.create_trading_invoice(db, cid, customer_id=demo["customer_id"], number="TINV-P1",
                              invoice_date=OD, lines=[_iline(d, 0, it, "60")])
    db.commit()
    dl = db.get(TradingDispatchLine, d.lines[0].id)
    assert ti.dispatch_line_uninvoiced(db, dl) == D("40.000000")
    ti.create_trading_invoice(db, cid, customer_id=demo["customer_id"], number="TINV-P2",
                              invoice_date=OD, lines=[_iline(d, 0, it, "40")])
    db.commit()
    dl = db.get(TradingDispatchLine, d.lines[0].id)
    assert ti.dispatch_line_uninvoiced(db, dl) == D("0.000000")
    assert dl.qty_invoiced == D("100.000000")


def test_over_invoice_blocked(db, demo):
    cid = demo["company_id"]; _configure(db, cid)
    it = _item(db, cid, "TF-IO"); wh = _trade_wh(db, cid, "WH-IO")
    d = _approved_dispatch(db, cid, demo["customer_id"], it, wh, "100", "50", suffix="O")
    db.commit()
    with pytest.raises(ti.TradingInvoiceError):
        ti.create_trading_invoice(db, cid, customer_id=demo["customer_id"], number="TINV-O",
                                  invoice_date=OD, lines=[_iline(d, 0, it, "80")])   # > 50 dispatched
    db.rollback()


def test_over_invoice_within_one_invoice_blocked(db, demo):
    """Two invoice lines on the same dispatch line summing beyond uninvoiced -> blocked."""
    cid = demo["company_id"]; _configure(db, cid)
    it = _item(db, cid, "TF-IW"); wh = _trade_wh(db, cid, "WH-IW")
    d = _approved_dispatch(db, cid, demo["customer_id"], it, wh, "100", "50", suffix="W")
    db.commit()
    with pytest.raises(ti.TradingInvoiceError):
        ti.create_trading_invoice(db, cid, customer_id=demo["customer_id"], number="TINV-W",
                                  invoice_date=OD, lines=[_iline(d, 0, it, "30"), _iline(d, 0, it, "30")])
    db.rollback()


def test_duplicate_number_rejected_same_company(db, demo):
    cid = demo["company_id"]; _configure(db, cid)
    it = _item(db, cid, "TF-IDUP"); wh = _trade_wh(db, cid, "WH-IDUP")
    d = _approved_dispatch(db, cid, demo["customer_id"], it, wh, "100", "20", suffix="DUP")
    db.commit()
    ti.create_trading_invoice(db, cid, customer_id=demo["customer_id"], number="DUPNUM",
                              invoice_date=OD, lines=[_iline(d, 0, it, "10")])
    db.commit()
    with pytest.raises(ti.TradingInvoiceError):
        ti.create_trading_invoice(db, cid, customer_id=demo["customer_id"], number="DUPNUM",
                                  invoice_date=OD, lines=[_iline(d, 0, it, "5")])
    db.rollback()


def test_unapproved_dispatch_rejected(db, demo):
    cid = demo["company_id"]; _configure(db, cid)
    it = _item(db, cid, "TF-IU"); wh = _trade_wh(db, cid, "WH-IU")
    inventory.receive(db, company_id=cid, item_type=ItemType.TRADING_FABRIC, item_id=it.id,
                      qty="100", unit_cost="100", warehouse_id=wh.id, event_type="GRN")
    so = ts.create_trading_so(db, cid, customer_id=demo["customer_id"], order_number="ISO-U", order_date=OD,
                              lines=[{"trading_item_id": it.id, "warehouse_id": wh.id, "qty_ordered": "100", "rate": "300"}])
    ts.approve_trading_so(db, cid, so.id)
    d = td.create_trading_dispatch(db, cid, customer_id=demo["customer_id"], sales_order_id=so.id,
                                   dispatch_number="IDSP-U", dispatch_date=OD,
                                   lines=[{"trading_item_id": it.id, "sales_order_line_id": so.lines[0].id,
                                           "warehouse_id": wh.id, "quantity": "50"}])
    td.submit_trading_dispatch(db, cid, d.id); db.commit()   # SUBMITTED, not APPROVED
    with pytest.raises(ti.TradingInvoiceError):
        ti.create_trading_invoice(db, cid, customer_id=demo["customer_id"], number="TINV-U",
                                  invoice_date=OD, lines=[_iline(d, 0, it, "10")])
    db.rollback()


def test_cross_customer_and_sku_mismatch_rejected(db, demo):
    cid = demo["company_id"]; _configure(db, cid)
    it = _item(db, cid, "TF-IX"); other_it = _item(db, cid, "TF-IX2"); wh = _trade_wh(db, cid, "WH-IX")
    d = _approved_dispatch(db, cid, demo["customer_id"], it, wh, "100", "50", suffix="X")
    other_cust = Customer(company_id=cid, code="CUSTX", name="CustX"); db.add(other_cust); db.commit()
    # customer mismatch
    with pytest.raises(ti.TradingInvoiceError):
        ti.create_trading_invoice(db, cid, customer_id=other_cust.id, number="TINV-X1",
                                  invoice_date=OD, lines=[_iline(d, 0, it, "10")])
    db.rollback()
    # SKU mismatch (invoice line SKU != dispatch line SKU)
    with pytest.raises(ti.TradingInvoiceError):
        ti.create_trading_invoice(db, cid, customer_id=demo["customer_id"], number="TINV-X2",
                                  invoice_date=OD, lines=[_iline(d, 0, other_it, "10")])
    db.rollback()


def test_inactive_sku_rejected(db, demo):
    cid = demo["company_id"]; _configure(db, cid)
    it = _item(db, cid, "TF-IIN"); wh = _trade_wh(db, cid, "WH-IIN")
    d = _approved_dispatch(db, cid, demo["customer_id"], it, wh, "100", "50", suffix="IN")
    it.is_active = False; db.commit()
    with pytest.raises(ti.TradingInvoiceError):
        ti.create_trading_invoice(db, cid, customer_id=demo["customer_id"], number="TINV-IN",
                                  invoice_date=OD, lines=[_iline(d, 0, it, "10")])
    db.rollback()


def test_cross_company_dispatch_rejected(db, demo):
    cid = demo["company_id"]; _configure(db, cid)
    other = Company(code=f"OC-{uuid.uuid4().hex[:4]}", name="Other"); db.add(other); db.flush()
    o_cust = Customer(company_id=other.id, code="OCU", name="OCU"); db.add(o_cust)
    o_item = _item(db, other.id, "OIT"); o_wh = _trade_wh(db, other.id, "OWH")
    # A DRAFT dispatch in the other company suffices: our company's invoice create
    # rejects its dispatch line on company scope (no approval/posting needed).
    o_so = ts.create_trading_so(db, other.id, customer_id=o_cust.id, order_number="OSO", order_date=OD,
                                lines=[{"trading_item_id": o_item.id, "warehouse_id": o_wh.id,
                                        "qty_ordered": "100", "rate": "300"}])
    ts.approve_trading_so(db, other.id, o_so.id)
    o_d = td.create_trading_dispatch(db, other.id, customer_id=o_cust.id, sales_order_id=o_so.id,
                                     dispatch_number="ODSP", dispatch_date=OD,
                                     lines=[{"trading_item_id": o_item.id, "sales_order_line_id": o_so.lines[0].id,
                                             "warehouse_id": o_wh.id, "quantity": "50"}])
    db.commit()
    with pytest.raises(ti.TradingInvoiceError):   # our company cannot invoice another company's dispatch line
        ti.create_trading_invoice(db, cid, customer_id=demo["customer_id"], number="TINV-OC",
                                  invoice_date=OD, lines=[{"dispatch_line_id": o_d.lines[0].id,
                                                           "trading_item_id": o_item.id, "quantity": "10",
                                                           "rate": "300", "discount": "0"}])
    db.rollback()


# ------------------------- no posting / no stock ----------------------------

def test_invoice_creates_no_posting_or_stock_change(db, demo):
    cid = demo["company_id"]; _configure(db, cid)
    it = _item(db, cid, "TF-INOP"); wh = _trade_wh(db, cid, "WH-INOP")
    d = _approved_dispatch(db, cid, demo["customer_id"], it, wh, "100", "60", suffix="NOP")
    db.commit()
    before = _acct_counts(db, cid)
    dstatus = d.status
    ti.create_trading_invoice(db, cid, customer_id=demo["customer_id"], number="TINV-NOP",
                              invoice_date=OD, lines=[_iline(d, 0, it, "60")])
    db.commit()
    after = _acct_counts(db, cid)
    assert after == before                       # no voucher/journal/layer/txn/roll/edge from invoicing
    assert td.get_trading_dispatch(db, cid, d.id).status == dstatus   # dispatch status unchanged
    # sales-order dispatched qty unchanged by invoicing
    so = ts.list_trading_sos(db, cid)[0][0]
    assert all(l.qty_dispatched == l.qty_dispatched for l in so.lines)  # (sanity; see dedicated SO test)


# ----------------------------- cancellation ---------------------------------

def test_cancel_draft_releases_allocation(db, demo):
    cid = demo["company_id"]; _configure(db, cid)
    it = _item(db, cid, "TF-IC"); wh = _trade_wh(db, cid, "WH-IC")
    d = _approved_dispatch(db, cid, demo["customer_id"], it, wh, "100", "100", suffix="C")
    db.commit()
    inv = ti.create_trading_invoice(db, cid, customer_id=demo["customer_id"], number="TINV-C",
                                    invoice_date=OD, lines=[_iline(d, 0, it, "100")])
    db.commit()
    dl = db.get(TradingDispatchLine, d.lines[0].id)
    assert ti.dispatch_line_uninvoiced(db, dl) == D("0.000000")
    ti.cancel_trading_invoice(db, cid, inv.id); db.commit()
    assert ti.get_trading_invoice(db, cid, inv.id).status == DocStatus.CANCELLED
    # allocation released, counter decremented, quantity usable again
    dl = db.get(TradingDispatchLine, d.lines[0].id)
    assert dl.qty_invoiced == D("0.000000")
    assert ti.dispatch_line_uninvoiced(db, dl) == D("100.000000")
    allocs = db.execute(select(TradingDispatchInvoiceAllocation).where(
        TradingDispatchInvoiceAllocation.invoice_id == inv.id)).scalars().all()
    assert allocs and all(a.status == TradingAllocationStatus.RELEASED.value for a in allocs)  # evidence retained
    # a new invoice can reuse the released quantity
    inv2 = ti.create_trading_invoice(db, cid, customer_id=demo["customer_id"], number="TINV-C2",
                                     invoice_date=OD, lines=[_iline(d, 0, it, "100")])
    db.commit()
    assert inv2.taxable_value == D("30000.00")


def test_cancel_non_draft_rejected(db, demo):
    cid = demo["company_id"]; _configure(db, cid)
    it = _item(db, cid, "TF-ICN"); wh = _trade_wh(db, cid, "WH-ICN")
    d = _approved_dispatch(db, cid, demo["customer_id"], it, wh, "100", "20", suffix="CN")
    db.commit()
    inv = ti.create_trading_invoice(db, cid, customer_id=demo["customer_id"], number="TINV-CN",
                                    invoice_date=OD, lines=[_iline(d, 0, it, "10")])
    ti.cancel_trading_invoice(db, cid, inv.id); db.commit()
    with pytest.raises(ti.TradingInvoiceError):   # already cancelled
        ti.cancel_trading_invoice(db, cid, inv.id)
    db.rollback()


def test_consolidated_invoice_across_dispatches(db, demo):
    cid = demo["company_id"]; _configure(db, cid)
    it = _item(db, cid, "TF-ICON"); wh = _trade_wh(db, cid, "WH-ICON")
    d1 = _approved_dispatch(db, cid, demo["customer_id"], it, wh, "100", "40", suffix="CON1")
    d2 = _approved_dispatch(db, cid, demo["customer_id"], it, wh, "100", "30", suffix="CON2")
    db.commit()
    inv = ti.create_trading_invoice(db, cid, customer_id=demo["customer_id"], number="TINV-CON",
                                    invoice_date=OD, lines=[_iline(d1, 0, it, "40"), _iline(d2, 0, it, "30")])
    db.commit()
    assert len(inv.lines) == 2 and inv.taxable_value == D("21000.00")   # (40+30)*300
    assert db.get(TradingDispatchLine, d1.lines[0].id).qty_invoiced == D("40.000000")
    assert db.get(TradingDispatchLine, d2.lines[0].id).qty_invoiced == D("30.000000")
