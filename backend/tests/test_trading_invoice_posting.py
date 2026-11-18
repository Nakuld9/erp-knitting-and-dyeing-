"""Stage 4B-3A.1c.2 — trading sales invoice posting (revenue + GST + receivable).

Covers the fail-closed CA gate, balanced revenue voucher, intra/inter GST,
idempotency, lifecycle, and strict isolation from inventory/COGS.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.accounting import AccountType, ChartOfAccount, JournalLine, PostingRule, Voucher
from app.models.commercial import DocStatus, SalesInvoice
from app.models.inventory import InventoryTransaction, ItemType, StockLayer
from app.models.masters import Customer
from app.services import inventory, trading
from app.services import trading_sales as ts
from app.services import trading_dispatch as td
from app.services import trading_invoice as ti
from app.models.hybrid import WarehouseCategory
from app.models.masters import Warehouse

D = Decimal
OD = date(2026, 11, 15)


def _trade_wh(db, cid, code):
    w = Warehouse(company_id=cid, code=code, name=code, kind="STORE",
                  category=WarehouseCategory.TRADING.value)
    db.add(w); db.flush()
    return w


def _item(db, cid, code):
    it = trading.create_trading_item(db, cid, code=code, name=code); db.flush()
    return it


def _configure_delivery(db, cid):
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


def _configure_sales(db, cid):
    """Synthetic TEST TRADING_SALES mapping: a dedicated trading revenue account
    (4011) + the existing AR (1040) and output-GST (2041/2042/2043) accounts."""
    if db.execute(select(ChartOfAccount).where(ChartOfAccount.company_id == cid,
                                               ChartOfAccount.code == "4011")).scalar_one_or_none() is None:
        db.add(ChartOfAccount(company_id=cid, code="4011", name="Trading Fabric Sales",
                              account_type=AccountType.INCOME.value, is_group=False,
                              allow_posting=True, is_active=True))
    for slot, code in {"receivable": "1040", "sales": "4011", "output_cgst": "2041",
                       "output_sgst": "2042", "output_igst": "2043"}.items():
        if db.execute(select(PostingRule).where(PostingRule.company_id == cid,
                      PostingRule.event_type == "TRADING_SALES", PostingRule.role == slot)).scalar_one_or_none() is None:
            db.add(PostingRule(company_id=cid, event_type="TRADING_SALES", role=slot, account_code=code))
    db.flush()


def _draft_invoice(db, cid, customer_id, item, wh, *, number, qty="60", rate="300", tax_id=None, suffix="A"):
    inventory.receive(db, company_id=cid, item_type=ItemType.TRADING_FABRIC, item_id=item.id,
                      qty="100", unit_cost="100", warehouse_id=wh.id, event_type="GRN")
    so = ts.create_trading_so(db, cid, customer_id=customer_id, order_number=f"PISO-{suffix}", order_date=OD,
                              lines=[{"trading_item_id": item.id, "warehouse_id": wh.id, "qty_ordered": "100", "rate": rate}])
    ts.approve_trading_so(db, cid, so.id)
    d = td.create_trading_dispatch(db, cid, customer_id=customer_id, sales_order_id=so.id,
                                   dispatch_number=f"PIDSP-{suffix}", dispatch_date=OD,
                                   lines=[{"trading_item_id": item.id, "sales_order_line_id": so.lines[0].id,
                                           "warehouse_id": wh.id, "quantity": qty}])
    td.submit_trading_dispatch(db, cid, d.id); td.approve_trading_dispatch(db, cid, d.id); db.flush()
    inv = ti.create_trading_invoice(db, cid, customer_id=customer_id, number=number, invoice_date=OD,
                                    lines=[{"dispatch_line_id": d.lines[0].id, "trading_item_id": item.id,
                                            "quantity": qty, "rate": rate, "discount": "0", "tax_code_id": tax_id}])
    db.flush()
    return inv


def _snap(db, cid):
    return {
        "vouchers": db.execute(select(func.count()).select_from(Voucher).where(Voucher.company_id == cid)).scalar(),
        "jlines": db.execute(select(func.count()).select_from(JournalLine)).scalar(),
        "layers": db.execute(select(func.count()).select_from(StockLayer).where(StockLayer.company_id == cid)).scalar(),
        "txns": db.execute(select(func.count()).select_from(InventoryTransaction).where(InventoryTransaction.company_id == cid)).scalar(),
    }


def _codes(db, cid, voucher):
    code_by_id = {a.id: a.code for a in db.execute(
        select(ChartOfAccount).where(ChartOfAccount.company_id == cid)).scalars()}
    jls = db.execute(select(JournalLine).where(JournalLine.voucher_id == voucher.id)).scalars().all()
    return jls, code_by_id


# ------------------------------ happy path ----------------------------------

def test_post_intra_state_balanced_voucher(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid); _configure_sales(db, cid)
    it = _item(db, cid, "TF-PI"); wh = _trade_wh(db, cid, "WH-PI")
    inv = _draft_invoice(db, cid, demo["customer_id"], it, wh, number="PINV-1", qty="60", rate="300",
                         tax_id=demo["tax_id"], suffix="PI")
    db.commit()
    res = ti.post_trading_invoice(db, cid, inv.id, posted_by=uuid.uuid4())
    db.commit()
    inv = ti.get_trading_invoice(db, cid, inv.id)
    assert inv.status == DocStatus.POSTED and inv.posting_reference == f"TSI:{inv.number}"
    # taxable 18000, GST 9%+9% = 3240, total 21240 (demo customer+company state 03 -> intra)
    assert inv.taxable_value == D("18000.00") and inv.tax_amount == D("3240.00") and inv.total_value == D("21240.00")
    jls, codes = _codes(db, cid, res["voucher"])
    accts = {codes[j.account_id] for j in jls}
    assert "1040" in accts and "4011" in accts and "2041" in accts and "2042" in accts   # AR, trading rev, CGST, SGST
    assert "2043" not in accts                                                            # no IGST intra
    assert "4010" not in accts                                                            # NOT manufactured revenue
    assert sum(D(str(j.debit)) for j in jls) == sum(D(str(j.credit)) for j in jls) == D("21240.00")


def test_post_inter_state_igst(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid); _configure_sales(db, cid)
    inter_cust = Customer(company_id=cid, code="CUST-INT", name="Inter", state_code="06")
    db.add(inter_cust); db.flush()
    it = _item(db, cid, "TF-PIG"); wh = _trade_wh(db, cid, "WH-PIG")
    inv = _draft_invoice(db, cid, inter_cust.id, it, wh, number="PINV-IG", qty="60", rate="300",
                         tax_id=demo["tax_id"], suffix="PIG")
    db.commit()
    res = ti.post_trading_invoice(db, cid, inv.id)
    db.commit()
    jls, codes = _codes(db, cid, res["voucher"])
    accts = {codes[j.account_id] for j in jls}
    assert "2043" in accts and "2041" not in accts and "2042" not in accts   # IGST only, inter-state
    assert ti.get_trading_invoice(db, cid, inv.id).tax_amount == D("3240.00")   # 18% IGST


# ------------------------------ fail-closed ---------------------------------

def test_missing_sales_mapping_blocks_posting(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid)   # TRADING_SALES NOT configured
    it = _item(db, cid, "TF-PNA"); wh = _trade_wh(db, cid, "WH-PNA")
    inv = _draft_invoice(db, cid, demo["customer_id"], it, wh, number="PINV-NA", tax_id=demo["tax_id"], suffix="PNA")
    db.commit()
    before = _snap(db, cid)
    with pytest.raises(ti.TradingInvoicePostingBlocked):
        ti.post_trading_invoice(db, cid, inv.id)
    db.rollback()
    assert ti.get_trading_invoice(db, cid, inv.id).status == DocStatus.DRAFT   # still DRAFT
    assert _snap(db, cid) == before                       # no voucher/journal/stock change
    assert db.execute(select(Voucher).where(Voucher.company_id == cid,
                      Voucher.posting_reference == f"TSI:{inv.number}")).scalar_one_or_none() is None


def test_inactive_account_blocks_posting(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid); _configure_sales(db, cid)
    db.execute(select(ChartOfAccount).where(ChartOfAccount.company_id == cid, ChartOfAccount.code == "4011")).scalar_one().is_active = False
    db.flush()
    it = _item(db, cid, "TF-PIA"); wh = _trade_wh(db, cid, "WH-PIA")
    inv = _draft_invoice(db, cid, demo["customer_id"], it, wh, number="PINV-IA", tax_id=demo["tax_id"], suffix="PIA")
    db.commit()
    with pytest.raises(ti.TradingInvoicePostingBlocked):
        ti.post_trading_invoice(db, cid, inv.id)
    db.rollback()
    assert ti.get_trading_invoice(db, cid, inv.id).status == DocStatus.DRAFT


def test_missing_place_of_supply_blocks_posting(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid); _configure_sales(db, cid)
    no_state = Customer(company_id=cid, code="CUST-NS", name="NoState", state_code=None)
    db.add(no_state); db.flush()
    it = _item(db, cid, "TF-PNS"); wh = _trade_wh(db, cid, "WH-PNS")
    inv = _draft_invoice(db, cid, no_state.id, it, wh, number="PINV-NS", tax_id=demo["tax_id"], suffix="PNS")
    db.commit()
    with pytest.raises(ti.TradingInvoicePostingBlocked):   # place of supply undeterminable
        ti.post_trading_invoice(db, cid, inv.id)
    db.rollback()
    assert ti.get_trading_invoice(db, cid, inv.id).status == DocStatus.DRAFT


# ------------------------------ idempotency / lifecycle ---------------------

def test_posting_idempotent(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid); _configure_sales(db, cid)
    it = _item(db, cid, "TF-PID"); wh = _trade_wh(db, cid, "WH-PID")
    inv = _draft_invoice(db, cid, demo["customer_id"], it, wh, number="PINV-ID", tax_id=demo["tax_id"], suffix="PID")
    db.commit()
    ti.post_trading_invoice(db, cid, inv.id); db.commit()
    r2 = ti.post_trading_invoice(db, cid, inv.id); db.commit()   # idempotent
    assert r2["already_posted"] is True
    assert db.execute(select(func.count()).select_from(Voucher).where(
        Voucher.company_id == cid, Voucher.posting_reference == f"TSI:{inv.number}")).scalar() == 1


def test_posted_invoice_cannot_be_cancelled(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid); _configure_sales(db, cid)
    it = _item(db, cid, "TF-PC"); wh = _trade_wh(db, cid, "WH-PC")
    inv = _draft_invoice(db, cid, demo["customer_id"], it, wh, number="PINV-PC", tax_id=demo["tax_id"], suffix="PC")
    db.commit()
    ti.post_trading_invoice(db, cid, inv.id); db.commit()
    with pytest.raises(ti.TradingInvoiceError):   # posted cannot use draft-cancel
        ti.cancel_trading_invoice(db, cid, inv.id)
    db.rollback()


def test_posting_no_inventory_or_cogs_change(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid); _configure_sales(db, cid)
    it = _item(db, cid, "TF-PNO"); wh = _trade_wh(db, cid, "WH-PNO")
    inv = _draft_invoice(db, cid, demo["customer_id"], it, wh, number="PINV-NO", tax_id=demo["tax_id"], suffix="PNO")
    db.commit()
    before = _snap(db, cid)        # after dispatch approval (which already consumed stock + posted COGS)
    ti.post_trading_invoice(db, cid, inv.id); db.commit()
    after = _snap(db, cid)
    # posting adds exactly one revenue voucher; stock layers and inventory txns unchanged
    assert after["layers"] == before["layers"] and after["txns"] == before["txns"]
    assert after["vouchers"] == before["vouchers"] + 1
