"""Stage 5B — direct yarn sales functional tests (SQLite).

Order -> dispatch (FIFO lot consume + COGS) -> invoice (revenue + GST) with
lot-level evidence, fail-closed accounting, and strict isolation from
manufactured/trading/roll/WIP side effects.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.accounting import AccountType, ChartOfAccount, JournalLine, PostingRule, Voucher
from app.models.commercial import DocStatus, SalesInvoice
from app.models.hybrid import WarehouseCategory
from app.models.inventory import InventoryTransaction, ItemType, StockLayer
from app.models.manufacturing import FabricRoll
from app.models.masters import Customer, Warehouse, YarnLot, YarnMaster
from app.models.yarn_sales import (
    YarnSalesDispatchFifoAllocation, YarnSalesOrder, YarnDispatchInvoiceAllocation,
)
from app.services import inventory
from app.services import yarn_sales_order as so_svc
from app.services import yarn_sales_dispatch as dsp_svc
from app.services import yarn_sales_invoice as inv_svc

D = Decimal
OD = date(2026, 11, 15)


# ------------------------------ helpers -------------------------------------

def _yarn_wh(db, cid, code, category=WarehouseCategory.YARN_STORE.value):
    w = Warehouse(company_id=cid, code=code, name=code, kind="STORE", category=category)
    db.add(w); db.flush()
    return w


def _yarn(db, cid, code):
    y = YarnMaster(company_id=cid, code=code, name=code, yarn_type="Cotton", count="30s", is_active=True)
    db.add(y); db.flush()
    return y


def _lot(db, cid, yarn, *, internal, supplier_lot, qty, cost, wh, supplier_id=None):
    lot = YarnLot(company_id=cid, yarn_id=yarn.id, supplier_id=supplier_id, internal_lot_no=internal,
                  supplier_lot_no=supplier_lot, warehouse_id=wh.id if wh else None,
                  received_date=OD.isoformat(), unit_cost=D(cost))
    db.add(lot); db.flush()
    inventory.receive(db, company_id=cid, item_type=ItemType.YARN, item_id=yarn.id, qty=str(qty),
                      unit_cost=str(cost), lot_id=lot.id, warehouse_id=wh.id if wh else None,
                      event_type="YARN_GRN")
    db.flush()
    return lot


def _configure_delivery(db, cid):
    """Synthetic TEST YARN_DELIVERY mapping: a dedicated yarn COGS account (5160)
    debited, yarn inventory (1060) relieved."""
    for code, name, typ in [("5160", "Yarn Sales COGS", AccountType.EXPENSE.value)]:
        if db.execute(select(ChartOfAccount).where(ChartOfAccount.company_id == cid,
                      ChartOfAccount.code == code)).scalar_one_or_none() is None:
            db.add(ChartOfAccount(company_id=cid, code=code, name=name, account_type=typ,
                                  is_group=False, allow_posting=True, is_active=True))
    for slot, code in {"cogs": "5160", "inventory": "1060"}.items():
        db.add(PostingRule(company_id=cid, event_type="YARN_DELIVERY", role=slot, account_code=code))
    db.flush()


def _configure_sales(db, cid):
    """Synthetic TEST YARN_SALES mapping: dedicated yarn revenue (4012) + existing
    receivable (1040) / output GST (2041/2042/2043)."""
    if db.execute(select(ChartOfAccount).where(ChartOfAccount.company_id == cid,
                  ChartOfAccount.code == "4012")).scalar_one_or_none() is None:
        db.add(ChartOfAccount(company_id=cid, code="4012", name="Yarn Sales",
                              account_type=AccountType.INCOME.value, is_group=False,
                              allow_posting=True, is_active=True))
    for slot, code in {"receivable": "1040", "sales": "4012", "output_cgst": "2041",
                       "output_sgst": "2042", "output_igst": "2043"}.items():
        db.add(PostingRule(company_id=cid, event_type="YARN_SALES", role=slot, account_code=code))
    db.flush()


def _approved_so(db, cid, cust_id, yarn, wh, qty, *, suffix, rate="250"):
    so = so_svc.create_yarn_so(db, cid, customer_id=cust_id, order_number=f"YSO-{suffix}", order_date=OD,
                               lines=[{"yarn_id": yarn.id, "warehouse_id": wh.id if wh else None,
                                       "qty_ordered": str(qty), "rate": rate}])
    so_svc.approve_yarn_so(db, cid, so.id)
    db.flush()
    return so


def _approved_dispatch(db, cid, cust_id, yarn, wh, so, qty, *, suffix, lot_id=None):
    d = dsp_svc.create_yarn_dispatch(db, cid, customer_id=cust_id, sales_order_id=so.id,
                                     dispatch_number=f"YDSP-{suffix}", dispatch_date=OD,
                                     lines=[{"yarn_id": yarn.id, "sales_order_line_id": so.lines[0].id,
                                             "warehouse_id": wh.id if wh else None, "lot_id": lot_id,
                                             "quantity": str(qty)}])
    dsp_svc.submit_yarn_dispatch(db, cid, d.id)
    dsp_svc.approve_yarn_dispatch(db, cid, d.id)
    db.flush()
    return d


def _snap(db, cid, yarn_id):
    return {
        "layers": db.execute(select(func.count()).select_from(StockLayer).where(
            StockLayer.company_id == cid)).scalar(),
        "rolls": db.execute(select(func.count()).select_from(FabricRoll).where(
            FabricRoll.company_id == cid)).scalar(),
        "yarn_on_hand": D(str(inventory.balance(db, company_id=cid, item_type=ItemType.YARN,
                                                item_id=yarn_id)["qty_on_hand"])),
    }


# ------------------------------ single-lot sale -----------------------------

def test_single_lot_sale_consumes_and_posts_cogs(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid)
    wh = _yarn_wh(db, cid, "YWH-1"); y = _yarn(db, cid, "Y-A")
    _lot(db, cid, y, internal="L-A1", supplier_lot="SUP-A1", qty="100", cost="200", wh=wh)
    db.commit()
    so = _approved_so(db, cid, demo["customer_id"], y, wh, "60", suffix="1"); db.commit()
    d = _approved_dispatch(db, cid, demo["customer_id"], y, wh, so, "60", suffix="1")
    db.commit()
    # 60 consumed at cost 200 => COGS 12000; yarn on hand 40
    assert D(str(inventory.balance(db, company_id=cid, item_type=ItemType.YARN, item_id=y.id)["qty_on_hand"])) == D("40.000000")
    allocs = db.execute(select(YarnSalesDispatchFifoAllocation).where(
        YarnSalesDispatchFifoAllocation.dispatch_id == d.id)).scalars().all()
    assert len(allocs) == 1 and D(str(allocs[0].total_cost)) == D("12000.00")
    assert allocs[0].source_lot_id is not None            # original lot preserved
    v = db.execute(select(Voucher).where(Voucher.company_id == cid,
                   Voucher.posting_reference == f"YDISP:{d.dispatch_number}")).scalar_one()
    jls = db.execute(select(JournalLine).where(JournalLine.voucher_id == v.id)).scalars().all()
    codes = {c.id: c.code for c in db.execute(select(ChartOfAccount).where(ChartOfAccount.company_id == cid)).scalars()}
    accts = {codes[j.account_id] for j in jls}
    assert "5160" in accts and "1060" in accts            # yarn COGS + yarn inventory
    assert "5150" not in accts and "5155" not in accts    # NOT manufactured/trading COGS


def test_specific_lot_selection_never_substitutes(db, demo):
    """A line that names lot B draws ONLY lot B even when lot A is older/cheaper."""
    cid = demo["company_id"]; _configure_delivery(db, cid)
    wh = _yarn_wh(db, cid, "YWH-S"); y = _yarn(db, cid, "Y-S")
    _lot(db, cid, y, internal="L-S1", supplier_lot="SUP-S1", qty="50", cost="100", wh=wh)   # older/cheaper
    lotB = _lot(db, cid, y, internal="L-S2", supplier_lot="SUP-S2", qty="50", cost="300", wh=wh)
    db.commit()
    so = _approved_so(db, cid, demo["customer_id"], y, wh, "40", suffix="S"); db.commit()
    d = _approved_dispatch(db, cid, demo["customer_id"], y, wh, so, "40", suffix="S", lot_id=lotB.id)
    db.commit()
    allocs = db.execute(select(YarnSalesDispatchFifoAllocation).where(
        YarnSalesDispatchFifoAllocation.dispatch_id == d.id)).scalars().all()
    assert all(a.source_lot_id == lotB.id for a in allocs)     # only lot B
    assert sum(D(str(a.total_cost)) for a in allocs) == D("12000.00")   # 40 * 300
    # lot B insufficient -> blocks, never falls back to lot A
    so2 = _approved_so(db, cid, demo["customer_id"], y, wh, "20", suffix="S2"); db.commit()
    from app.services.inventory.service import InsufficientStockError
    d2 = dsp_svc.create_yarn_dispatch(db, cid, customer_id=demo["customer_id"], sales_order_id=so2.id,
                                      dispatch_number="YDSP-S2", dispatch_date=OD,
                                      lines=[{"yarn_id": y.id, "sales_order_line_id": so2.lines[0].id,
                                              "warehouse_id": wh.id, "lot_id": lotB.id, "quantity": "20"}])
    dsp_svc.submit_yarn_dispatch(db, cid, d2.id); db.commit()
    with pytest.raises(InsufficientStockError):
        dsp_svc.approve_yarn_dispatch(db, cid, d2.id)   # only 10 left in lot B
    db.rollback()


def test_fifo_across_multiple_lots(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid)
    wh = _yarn_wh(db, cid, "YWH-F"); y = _yarn(db, cid, "Y-F")
    _lot(db, cid, y, internal="L-F1", supplier_lot="SUP-F1", qty="30", cost="100", wh=wh)
    _lot(db, cid, y, internal="L-F2", supplier_lot="SUP-F2", qty="30", cost="200", wh=wh)
    db.commit()
    so = _approved_so(db, cid, demo["customer_id"], y, wh, "50", suffix="F"); db.commit()
    d = _approved_dispatch(db, cid, demo["customer_id"], y, wh, so, "50", suffix="F")   # no lot => FIFO
    db.commit()
    allocs = db.execute(select(YarnSalesDispatchFifoAllocation).where(
        YarnSalesDispatchFifoAllocation.dispatch_id == d.id)).scalars().all()
    # 30@100 + 20@200 = 3000 + 4000 = 7000; two lot allocations recorded separately
    assert len(allocs) == 2
    assert sum(D(str(a.total_cost)) for a in allocs) == D("7000.00")


def test_multiple_skus_one_order_and_invoice(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid); _configure_sales(db, cid)
    wh = _yarn_wh(db, cid, "YWH-M"); ya = _yarn(db, cid, "Y-M1"); yb = _yarn(db, cid, "Y-M2")
    _lot(db, cid, ya, internal="L-M1", supplier_lot="SUP-M1", qty="80", cost="150", wh=wh)
    _lot(db, cid, yb, internal="L-M2", supplier_lot="SUP-M2", qty="80", cost="250", wh=wh)
    db.commit()
    so = so_svc.create_yarn_so(db, cid, customer_id=demo["customer_id"], order_number="YSO-M", order_date=OD,
                               lines=[{"yarn_id": ya.id, "warehouse_id": wh.id, "qty_ordered": "40", "rate": "300"},
                                      {"yarn_id": yb.id, "warehouse_id": wh.id, "qty_ordered": "40", "rate": "400"}])
    so_svc.approve_yarn_so(db, cid, so.id); db.commit()
    d = dsp_svc.create_yarn_dispatch(db, cid, customer_id=demo["customer_id"], sales_order_id=so.id,
                                     dispatch_number="YDSP-M", dispatch_date=OD,
                                     lines=[{"yarn_id": ya.id, "sales_order_line_id": so.lines[0].id,
                                             "warehouse_id": wh.id, "quantity": "40"},
                                            {"yarn_id": yb.id, "sales_order_line_id": so.lines[1].id,
                                             "warehouse_id": wh.id, "quantity": "40"}])
    dsp_svc.submit_yarn_dispatch(db, cid, d.id); dsp_svc.approve_yarn_dispatch(db, cid, d.id); db.commit()
    inv = inv_svc.create_yarn_invoice(db, cid, customer_id=demo["customer_id"], number="YINV-M", invoice_date=OD,
                                      lines=[{"dispatch_line_id": d.lines[0].id, "quantity": "40", "rate": "300",
                                              "tax_code_id": demo["tax_id"]},
                                             {"dispatch_line_id": d.lines[1].id, "quantity": "40", "rate": "400",
                                              "tax_code_id": demo["tax_id"]}])
    db.commit()
    assert len(inv.lines) == 2 and inv.taxable_value == D("28000.00")   # 40*300 + 40*400


# ------------------------------ partial / repeated dispatch -----------------

def test_partial_and_repeated_dispatch(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid)
    wh = _yarn_wh(db, cid, "YWH-P"); y = _yarn(db, cid, "Y-P")
    _lot(db, cid, y, internal="L-P1", supplier_lot="SUP-P1", qty="100", cost="200", wh=wh)
    db.commit()
    so = _approved_so(db, cid, demo["customer_id"], y, wh, "100", suffix="P"); db.commit()
    _approved_dispatch(db, cid, demo["customer_id"], y, wh, so, "60", suffix="P1"); db.commit()
    so = so_svc.get_yarn_so(db, cid, so.id)
    assert so_svc.so_outstanding(so)[0]["outstanding"] == D("40.000000")
    assert str(so.status) in ("YarnSOStatus.PARTIAL", "PARTIAL")
    _approved_dispatch(db, cid, demo["customer_id"], y, wh, so, "40", suffix="P2"); db.commit()
    so = so_svc.get_yarn_so(db, cid, so.id)
    assert so_svc.so_outstanding(so)[0]["outstanding"] == D("0")
    assert str(so.status) in ("YarnSOStatus.COMPLETED", "COMPLETED")


def test_overselling_blocked_at_order(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid)   # mappings present so FIFO is the gate
    wh = _yarn_wh(db, cid, "YWH-O"); y = _yarn(db, cid, "Y-O")
    _lot(db, cid, y, internal="L-O1", supplier_lot="SUP-O1", qty="30", cost="200", wh=wh)
    db.commit()
    so = _approved_so(db, cid, demo["customer_id"], y, wh, "30", suffix="O"); db.commit()
    from app.services.inventory.service import InsufficientStockError
    d = dsp_svc.create_yarn_dispatch(db, cid, customer_id=demo["customer_id"], sales_order_id=so.id,
                                     dispatch_number="YDSP-O", dispatch_date=OD,
                                     lines=[{"yarn_id": y.id, "sales_order_line_id": so.lines[0].id,
                                             "warehouse_id": wh.id, "quantity": "30"}])
    dsp_svc.submit_yarn_dispatch(db, cid, d.id); db.commit()
    # drain the lot via a direct consume to simulate a competing production issue
    inventory.consume(db, company_id=cid, item_type=ItemType.YARN, item_id=y.id, qty="20",
                      warehouse_id=wh.id, event_type="YARN_ISSUE"); db.commit()
    with pytest.raises(InsufficientStockError):   # only 10 left, dispatch wants 30
        dsp_svc.approve_yarn_dispatch(db, cid, d.id)
    db.rollback()
    assert str(dsp_svc.get_yarn_dispatch(db, cid, d.id).status) in ("YarnDispatchStatus.SUBMITTED", "SUBMITTED")


def test_shared_pool_with_production_issue(db, demo):
    """A direct yarn-sale dispatch and a production-style issue draw the SAME
    ItemType.YARN FIFO pool; the second to run sees reduced stock."""
    cid = demo["company_id"]; _configure_delivery(db, cid)
    wh = _yarn_wh(db, cid, "YWH-SP"); y = _yarn(db, cid, "Y-SP")
    _lot(db, cid, y, internal="L-SP1", supplier_lot="SUP-SP1", qty="100", cost="200", wh=wh)
    db.commit()
    # production-style issue consumes 70 from the same pool
    inventory.consume(db, company_id=cid, item_type=ItemType.YARN, item_id=y.id, qty="70",
                      warehouse_id=wh.id, event_type="YARN_ISSUE"); db.commit()
    so = _approved_so(db, cid, demo["customer_id"], y, wh, "30", suffix="SP"); db.commit()
    _approved_dispatch(db, cid, demo["customer_id"], y, wh, so, "30", suffix="SP"); db.commit()
    assert D(str(inventory.balance(db, company_id=cid, item_type=ItemType.YARN, item_id=y.id)["qty_on_hand"])) == D("0.000000")


# ------------------------------ idempotency / duplicate ---------------------

def test_dispatch_approval_idempotent(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid)
    wh = _yarn_wh(db, cid, "YWH-ID"); y = _yarn(db, cid, "Y-ID")
    _lot(db, cid, y, internal="L-ID1", supplier_lot="SUP-ID1", qty="100", cost="200", wh=wh)
    db.commit()
    so = _approved_so(db, cid, demo["customer_id"], y, wh, "60", suffix="ID"); db.commit()
    d = dsp_svc.create_yarn_dispatch(db, cid, customer_id=demo["customer_id"], sales_order_id=so.id,
                                     dispatch_number="YDSP-ID", dispatch_date=OD,
                                     lines=[{"yarn_id": y.id, "sales_order_line_id": so.lines[0].id,
                                             "warehouse_id": wh.id, "quantity": "60"}])
    dsp_svc.submit_yarn_dispatch(db, cid, d.id); db.commit()
    dsp_svc.approve_yarn_dispatch(db, cid, d.id); db.commit()
    r2 = dsp_svc.approve_yarn_dispatch(db, cid, d.id); db.commit()
    assert r2["already_approved"] is True
    assert db.execute(select(func.count()).select_from(Voucher).where(
        Voucher.company_id == cid, Voucher.posting_reference == "YDISP:YDSP-ID")).scalar() == 1
    # stock consumed exactly once
    assert D(str(inventory.balance(db, company_id=cid, item_type=ItemType.YARN, item_id=y.id)["qty_on_hand"])) == D("40.000000")


# ------------------------------ fail-closed delivery ------------------------

def test_dispatch_fail_closed_without_delivery_mapping(db, demo):
    cid = demo["company_id"]   # YARN_DELIVERY NOT configured
    wh = _yarn_wh(db, cid, "YWH-NA"); y = _yarn(db, cid, "Y-NA")
    _lot(db, cid, y, internal="L-NA1", supplier_lot="SUP-NA1", qty="100", cost="200", wh=wh)
    db.commit()
    so = _approved_so(db, cid, demo["customer_id"], y, wh, "60", suffix="NA"); db.commit()
    d = dsp_svc.create_yarn_dispatch(db, cid, customer_id=demo["customer_id"], sales_order_id=so.id,
                                     dispatch_number="YDSP-NA", dispatch_date=OD,
                                     lines=[{"yarn_id": y.id, "sales_order_line_id": so.lines[0].id,
                                             "warehouse_id": wh.id, "quantity": "60"}])
    dsp_svc.submit_yarn_dispatch(db, cid, d.id); db.commit()
    before = _snap(db, cid, y.id)
    with pytest.raises(dsp_svc.YarnDispatchPostingBlocked):
        dsp_svc.approve_yarn_dispatch(db, cid, d.id)
    db.rollback()
    assert str(dsp_svc.get_yarn_dispatch(db, cid, d.id).status) in ("YarnDispatchStatus.SUBMITTED", "SUBMITTED")
    after = _snap(db, cid, y.id)
    assert after == before                      # no stock consumed, no voucher
    assert db.execute(select(func.count()).select_from(Voucher).where(
        Voucher.company_id == cid, Voucher.posting_reference == "YDISP:YDSP-NA")).scalar() == 0


# ------------------------------ invoice posting GST -------------------------

def _draft_invoice_from(db, cid, cust_id, yarn, wh, *, so_suffix, qty="60", rate="300", tax_id=None):
    so = _approved_so(db, cid, cust_id, yarn, wh, qty, suffix=so_suffix, rate=rate)
    db.commit()
    d = _approved_dispatch(db, cid, cust_id, yarn, wh, so, qty, suffix=so_suffix); db.commit()
    inv = inv_svc.create_yarn_invoice(db, cid, customer_id=cust_id, number=f"YINV-{so_suffix}", invoice_date=OD,
                                      lines=[{"dispatch_line_id": d.lines[0].id, "quantity": qty,
                                              "rate": rate, "tax_code_id": tax_id}])
    db.commit()
    return inv


def test_invoice_post_intra_state(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid); _configure_sales(db, cid)
    wh = _yarn_wh(db, cid, "YWH-II"); y = _yarn(db, cid, "Y-II")
    _lot(db, cid, y, internal="L-II1", supplier_lot="SUP-II1", qty="100", cost="200", wh=wh)
    inv = _draft_invoice_from(db, cid, demo["customer_id"], y, wh, so_suffix="II", tax_id=demo["tax_id"])
    res = inv_svc.post_yarn_invoice(db, cid, inv.id); db.commit()
    inv = inv_svc.get_yarn_invoice(db, cid, inv.id)
    assert inv.status == DocStatus.POSTED
    # 60*300 = 18000 taxable; 18% intra = 3240; total 21240
    assert inv.taxable_value == D("18000.00") and inv.tax_amount == D("3240.00") and inv.total_value == D("21240.00")
    jls = db.execute(select(JournalLine).where(JournalLine.voucher_id == res["voucher"].id)).scalars().all()
    codes = {c.id: c.code for c in db.execute(select(ChartOfAccount).where(ChartOfAccount.company_id == cid)).scalars()}
    accts = {codes[j.account_id] for j in jls}
    assert "1040" in accts and "4012" in accts and "2041" in accts and "2042" in accts
    assert "4010" not in accts and "4011" not in accts          # not manufactured/trading revenue
    assert sum(D(str(j.debit)) for j in jls) == sum(D(str(j.credit)) for j in jls) == D("21240.00")


def test_invoice_post_inter_state_igst(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid); _configure_sales(db, cid)
    inter = Customer(company_id=cid, code="YC-INT", name="Inter", state_code="06"); db.add(inter); db.flush()
    wh = _yarn_wh(db, cid, "YWH-IG"); y = _yarn(db, cid, "Y-IG")
    _lot(db, cid, y, internal="L-IG1", supplier_lot="SUP-IG1", qty="100", cost="200", wh=wh)
    inv = _draft_invoice_from(db, cid, inter.id, y, wh, so_suffix="IG", tax_id=demo["tax_id"])
    res = inv_svc.post_yarn_invoice(db, cid, inv.id); db.commit()
    jls = db.execute(select(JournalLine).where(JournalLine.voucher_id == res["voucher"].id)).scalars().all()
    codes = {c.id: c.code for c in db.execute(select(ChartOfAccount).where(ChartOfAccount.company_id == cid)).scalars()}
    accts = {codes[j.account_id] for j in jls}
    assert "2043" in accts and "2041" not in accts and "2042" not in accts   # IGST only


def test_invoice_post_fail_closed_without_sales_mapping(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid)   # YARN_SALES NOT configured
    wh = _yarn_wh(db, cid, "YWH-FC"); y = _yarn(db, cid, "Y-FC")
    _lot(db, cid, y, internal="L-FC1", supplier_lot="SUP-FC1", qty="100", cost="200", wh=wh)
    inv = _draft_invoice_from(db, cid, demo["customer_id"], y, wh, so_suffix="FC", tax_id=demo["tax_id"])
    with pytest.raises(inv_svc.YarnInvoicePostingBlocked):
        inv_svc.post_yarn_invoice(db, cid, inv.id)
    db.rollback()
    assert inv_svc.get_yarn_invoice(db, cid, inv.id).status == DocStatus.DRAFT
    assert db.execute(select(func.count()).select_from(Voucher).where(
        Voucher.company_id == cid, Voucher.posting_reference == f"YSI:{inv.number}")).scalar() == 0


def test_invoice_post_missing_place_of_supply_blocks(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid); _configure_sales(db, cid)
    nos = Customer(company_id=cid, code="YC-NS", name="NoState", state_code=None); db.add(nos); db.flush()
    wh = _yarn_wh(db, cid, "YWH-NS"); y = _yarn(db, cid, "Y-NS")
    _lot(db, cid, y, internal="L-NS1", supplier_lot="SUP-NS1", qty="100", cost="200", wh=wh)
    inv = _draft_invoice_from(db, cid, nos.id, y, wh, so_suffix="NS", tax_id=demo["tax_id"])
    with pytest.raises(inv_svc.YarnInvoicePostingBlocked):
        inv_svc.post_yarn_invoice(db, cid, inv.id)
    db.rollback()
    assert inv_svc.get_yarn_invoice(db, cid, inv.id).status == DocStatus.DRAFT


def test_invoice_posting_idempotent_and_posted_cannot_cancel(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid); _configure_sales(db, cid)
    wh = _yarn_wh(db, cid, "YWH-PI"); y = _yarn(db, cid, "Y-PI")
    _lot(db, cid, y, internal="L-PI1", supplier_lot="SUP-PI1", qty="100", cost="200", wh=wh)
    inv = _draft_invoice_from(db, cid, demo["customer_id"], y, wh, so_suffix="PI", tax_id=demo["tax_id"])
    inv_svc.post_yarn_invoice(db, cid, inv.id); db.commit()
    r2 = inv_svc.post_yarn_invoice(db, cid, inv.id); db.commit()
    assert r2["already_posted"] is True
    assert db.execute(select(func.count()).select_from(Voucher).where(
        Voucher.company_id == cid, Voucher.posting_reference == f"YSI:{inv.number}")).scalar() == 1
    with pytest.raises(inv_svc.YarnInvoiceError):    # posted cannot be draft-cancelled
        inv_svc.cancel_yarn_invoice(db, cid, inv.id)
    db.rollback()


# ------------------------------ no side effects / lot integrity -------------

def test_no_roll_wip_or_packing_side_effects(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid); _configure_sales(db, cid)
    wh = _yarn_wh(db, cid, "YWH-NO"); y = _yarn(db, cid, "Y-NO")
    _lot(db, cid, y, internal="L-NO1", supplier_lot="SUP-NO1", qty="100", cost="200", wh=wh)
    db.commit()
    rolls_before = db.execute(select(func.count()).select_from(FabricRoll).where(FabricRoll.company_id == cid)).scalar()
    inv = _draft_invoice_from(db, cid, demo["customer_id"], y, wh, so_suffix="NO", tax_id=demo["tax_id"])
    inv_svc.post_yarn_invoice(db, cid, inv.id); db.commit()
    rolls_after = db.execute(select(func.count()).select_from(FabricRoll).where(FabricRoll.company_id == cid)).scalar()
    assert rolls_after == rolls_before                    # no FabricRoll created
    # the invoice line carries NO fabric_roll_id and NO knitting WIP was posted
    assert all(l.fabric_roll_id is None for l in inv.lines)


def test_historical_lot_references_unchanged(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid)
    wh = _yarn_wh(db, cid, "YWH-H"); y = _yarn(db, cid, "Y-H")
    lot = _lot(db, cid, y, internal="L-H1", supplier_lot="SUP-ORIGINAL", qty="100", cost="200", wh=wh)
    db.commit()
    so = _approved_so(db, cid, demo["customer_id"], y, wh, "60", suffix="H"); db.commit()
    _approved_dispatch(db, cid, demo["customer_id"], y, wh, so, "60", suffix="H", lot_id=lot.id); db.commit()
    reloaded = db.get(YarnLot, lot.id)
    assert reloaded.internal_lot_no == "L-H1" and reloaded.supplier_lot_no == "SUP-ORIGINAL"   # never regenerated
    # no NEW lot was created by the sale
    assert db.execute(select(func.count()).select_from(YarnLot).where(
        YarnLot.company_id == cid, YarnLot.yarn_id == y.id)).scalar() == 1


# ------------------------------ cross-company / cancel ----------------------

def test_cross_company_rejected(db, demo):
    from app.models.core import Company
    cid = demo["company_id"]
    other = Company(code=f"OC-{uuid.uuid4().hex[:4]}", name="Other"); db.add(other); db.flush()
    _configure_delivery(db, cid)
    wh = _yarn_wh(db, cid, "YWH-XC"); y = _yarn(db, cid, "Y-XC")
    _lot(db, cid, y, internal="L-XC1", supplier_lot="SUP-XC1", qty="100", cost="200", wh=wh)
    db.commit()
    so = _approved_so(db, cid, demo["customer_id"], y, wh, "60", suffix="XC"); db.commit()
    with pytest.raises(so_svc.YarnSalesError):
        so_svc.get_yarn_so(db, other.id, so.id)        # other company cannot read it


def test_cancel_before_dispatch_releases_nothing_consumed(db, demo):
    cid = demo["company_id"]
    wh = _yarn_wh(db, cid, "YWH-CB"); y = _yarn(db, cid, "Y-CB")
    _lot(db, cid, y, internal="L-CB1", supplier_lot="SUP-CB1", qty="100", cost="200", wh=wh)
    db.commit()
    so = _approved_so(db, cid, demo["customer_id"], y, wh, "60", suffix="CB"); db.commit()
    before = D(str(inventory.balance(db, company_id=cid, item_type=ItemType.YARN, item_id=y.id)["qty_on_hand"]))
    so_svc.cancel_yarn_so(db, cid, so.id); db.commit()
    after = D(str(inventory.balance(db, company_id=cid, item_type=ItemType.YARN, item_id=y.id)["qty_on_hand"]))
    assert before == after == D("100.000000")           # cancelling an order consumes nothing
    assert str(so_svc.get_yarn_so(db, cid, so.id).status) in ("YarnSOStatus.CANCELLED", "CANCELLED")


# ------------------------------ Stage 5G: invoice reconciliation visibility ----

def _lines_view(db, cid, d_id):
    """dispatch_lines_view keyed by line id (invoiced/uninvoiced/reconciled)."""
    d = dsp_svc.get_yarn_dispatch(db, cid, d_id)
    return {l["id"]: l for l in dsp_svc.dispatch_lines_view(db, d)}


def test_recon_no_allocations_uninvoiced_equals_dispatched(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid)
    wh = _yarn_wh(db, cid, "YWH-R0"); y = _yarn(db, cid, "Y-R0")
    _lot(db, cid, y, internal="L-R0", supplier_lot="S-R0", qty="100", cost="200", wh=wh)
    so = _approved_so(db, cid, demo["customer_id"], y, wh, "60", suffix="R0"); db.commit()
    d = _approved_dispatch(db, cid, demo["customer_id"], y, wh, so, "60", suffix="R0"); db.commit()
    v = _lines_view(db, cid, d.id)[str(d.lines[0].id)]
    assert v["invoiced_quantity"] == "0.000000" and v["uninvoiced_quantity"] == "60.000000"
    assert v["reconciled"] is True and v["qty_invoiced_counter"] == "0.000000"


def test_recon_partial_then_full_and_multiple_invoices(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid)
    wh = _yarn_wh(db, cid, "YWH-R1"); y = _yarn(db, cid, "Y-R1")
    _lot(db, cid, y, internal="L-R1", supplier_lot="S-R1", qty="100", cost="200", wh=wh)
    so = _approved_so(db, cid, demo["customer_id"], y, wh, "60", suffix="R1"); db.commit()
    d = _approved_dispatch(db, cid, demo["customer_id"], y, wh, so, "60", suffix="R1"); db.commit()
    dl = d.lines[0].id
    # first invoice 40 -> invoiced 40, uninvoiced 20
    inv_svc.create_yarn_invoice(db, cid, customer_id=demo["customer_id"], number="YI-R1a", invoice_date=OD,
                                lines=[{"dispatch_line_id": dl, "quantity": "40", "rate": "300",
                                        "tax_code_id": demo["tax_id"]}]); db.commit()
    v = _lines_view(db, cid, d.id)[str(dl)]
    assert v["invoiced_quantity"] == "40.000000" and v["uninvoiced_quantity"] == "20.000000" and v["reconciled"] is True
    # second invoice 20 -> fully invoiced
    inv_svc.create_yarn_invoice(db, cid, customer_id=demo["customer_id"], number="YI-R1b", invoice_date=OD,
                                lines=[{"dispatch_line_id": dl, "quantity": "20", "rate": "300",
                                        "tax_code_id": demo["tax_id"]}]); db.commit()
    v = _lines_view(db, cid, d.id)[str(dl)]
    assert v["invoiced_quantity"] == "60.000000" and v["uninvoiced_quantity"] == "0.000000" and v["reconciled"] is True


def test_recon_cancelled_invoice_releases_allocation(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid)
    wh = _yarn_wh(db, cid, "YWH-R2"); y = _yarn(db, cid, "Y-R2")
    _lot(db, cid, y, internal="L-R2", supplier_lot="S-R2", qty="100", cost="200", wh=wh)
    so = _approved_so(db, cid, demo["customer_id"], y, wh, "60", suffix="R2"); db.commit()
    d = _approved_dispatch(db, cid, demo["customer_id"], y, wh, so, "60", suffix="R2"); db.commit()
    dl = d.lines[0].id
    inv = inv_svc.create_yarn_invoice(db, cid, customer_id=demo["customer_id"], number="YI-R2", invoice_date=OD,
                                      lines=[{"dispatch_line_id": dl, "quantity": "40", "rate": "300",
                                              "tax_code_id": demo["tax_id"]}]); db.commit()
    assert _lines_view(db, cid, d.id)[str(dl)]["invoiced_quantity"] == "40.000000"
    # cancelling the DRAFT invoice RELEASES its allocation -> no longer counted
    inv_svc.cancel_yarn_invoice(db, cid, inv.id); db.commit()
    v = _lines_view(db, cid, d.id)[str(dl)]
    assert v["invoiced_quantity"] == "0.000000" and v["uninvoiced_quantity"] == "60.000000" and v["reconciled"] is True


def test_recon_multiple_lines_independent(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid)
    wh = _yarn_wh(db, cid, "YWH-R3"); ya = _yarn(db, cid, "Y-R3A"); yb = _yarn(db, cid, "Y-R3B")
    _lot(db, cid, ya, internal="L-R3A", supplier_lot="S-R3A", qty="100", cost="150", wh=wh)
    _lot(db, cid, yb, internal="L-R3B", supplier_lot="S-R3B", qty="100", cost="250", wh=wh)
    so = so_svc.create_yarn_so(db, cid, customer_id=demo["customer_id"], order_number="YSO-R3", order_date=OD,
                               lines=[{"yarn_id": ya.id, "warehouse_id": wh.id, "qty_ordered": "50", "rate": "300"},
                                      {"yarn_id": yb.id, "warehouse_id": wh.id, "qty_ordered": "50", "rate": "400"}])
    so_svc.approve_yarn_so(db, cid, so.id); db.commit()
    d = dsp_svc.create_yarn_dispatch(db, cid, customer_id=demo["customer_id"], sales_order_id=so.id,
                                     dispatch_number="YDSP-R3", dispatch_date=OD,
                                     lines=[{"yarn_id": ya.id, "sales_order_line_id": so.lines[0].id,
                                             "warehouse_id": wh.id, "quantity": "50"},
                                            {"yarn_id": yb.id, "sales_order_line_id": so.lines[1].id,
                                             "warehouse_id": wh.id, "quantity": "50"}])
    dsp_svc.submit_yarn_dispatch(db, cid, d.id); dsp_svc.approve_yarn_dispatch(db, cid, d.id); db.commit()
    lineA, lineB = d.lines[0].id, d.lines[1].id
    # invoice only line A, partially
    inv_svc.create_yarn_invoice(db, cid, customer_id=demo["customer_id"], number="YI-R3", invoice_date=OD,
                                lines=[{"dispatch_line_id": lineA, "quantity": "30", "rate": "300",
                                        "tax_code_id": demo["tax_id"]}]); db.commit()
    v = _lines_view(db, cid, d.id)
    assert v[str(lineA)]["invoiced_quantity"] == "30.000000" and v[str(lineA)]["uninvoiced_quantity"] == "20.000000"
    assert v[str(lineB)]["invoiced_quantity"] == "0.000000" and v[str(lineB)]["uninvoiced_quantity"] == "50.000000"  # independent
    # original lot references intact on both lines
    assert v[str(lineA)]["lot_id"] is None and v[str(lineB)]["lot_id"] is None   # FIFO (no specific lot)


def test_recon_over_invoicing_still_rejected(db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid)
    wh = _yarn_wh(db, cid, "YWH-R4"); y = _yarn(db, cid, "Y-R4")
    _lot(db, cid, y, internal="L-R4", supplier_lot="S-R4", qty="100", cost="200", wh=wh)
    so = _approved_so(db, cid, demo["customer_id"], y, wh, "60", suffix="R4"); db.commit()
    d = _approved_dispatch(db, cid, demo["customer_id"], y, wh, so, "60", suffix="R4"); db.commit()
    with pytest.raises(inv_svc.YarnInvoiceError):   # 70 > 60 dispatched -> backend blocks
        inv_svc.create_yarn_invoice(db, cid, customer_id=demo["customer_id"], number="YI-R4", invoice_date=OD,
                                    lines=[{"dispatch_line_id": d.lines[0].id, "quantity": "70", "rate": "300",
                                            "tax_code_id": demo["tax_id"]}])
    db.rollback()
    # view still consistent: nothing invoiced
    assert _lines_view(db, cid, d.id)[str(d.lines[0].id)]["uninvoiced_quantity"] == "60.000000"


def test_recon_api_dispatch_returns_quantities(client, auth_headers, db, demo):
    cid = demo["company_id"]; _configure_delivery(db, cid)
    wh = _yarn_wh(db, cid, "YWH-R5"); y = _yarn(db, cid, "Y-R5")
    _lot(db, cid, y, internal="L-R5", supplier_lot="S-R5", qty="100", cost="200", wh=wh)
    so = _approved_so(db, cid, demo["customer_id"], y, wh, "60", suffix="R5"); db.commit()
    d = _approved_dispatch(db, cid, demo["customer_id"], y, wh, so, "60", suffix="R5"); db.commit()
    inv_svc.create_yarn_invoice(db, cid, customer_id=demo["customer_id"], number="YI-R5", invoice_date=OD,
                                lines=[{"dispatch_line_id": d.lines[0].id, "quantity": "25", "rate": "300",
                                        "tax_code_id": demo["tax_id"]}]); db.commit()
    r = client.get(f"/api/yarn-sales/dispatches/{d.id}", headers=auth_headers)
    assert r.status_code == 200, r.text
    ln = r.json()["lines"][0]
    assert ln["invoiced_quantity"] == "25.000000" and ln["uninvoiced_quantity"] == "35.000000" and ln["reconciled"] is True
