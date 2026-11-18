"""Stage 4B-2 — trading purchase order + GRN service tests.

Covers both routes (PO-based and direct), approval/stock boundary, idempotency,
FIFO, over-receipt control, GST, the CA posting gate, warehouse isolation and
separation from manufactured/chemical stock.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.accounting import AccountType, ChartOfAccount, PostingRule, Voucher
from app.models.core import Branch, Company
from app.models.genealogy import RollGenealogyEdge
from app.models.hybrid import WarehouseCategory
from app.models.inventory import ItemType, StockLayer
from app.models.manufacturing import FabricRoll
from app.models.masters import Supplier, Warehouse
from app.models.trading import TradingGRNStatus, TradingPOStatus
from app.services import inventory, trading
from app.services import trading_purchase as tp

D = Decimal
GRN_DATE = date(2026, 11, 10)


# ------------------------------- fixtures -----------------------------------

def _wh(db, cid, code, category):
    w = Warehouse(company_id=cid, code=code, name=code, kind="STORE", category=category)
    db.add(w); db.flush()
    return w


def _trade_wh(db, cid, code="WH-TRADE"):
    return _wh(db, cid, code, WarehouseCategory.TRADING.value)


def _item(db, cid, code="TF-P", active=True):
    it = trading.create_trading_item(db, cid, code=code, name=code, is_active=active)
    db.flush()
    return it


def _supplier(db, cid, code, state="03"):
    s = Supplier(company_id=cid, code=code, name=code, state_code=state)
    db.add(s); db.flush()
    return s


def _configure_grn_accounts(db, cid):
    """Map TRADING_GRN slots to real postable accounts (a dedicated trading
    inventory control account + existing input-GST/payable), as a CA would."""
    if db.execute(select(ChartOfAccount).where(
            ChartOfAccount.company_id == cid, ChartOfAccount.code == "1105")).scalar_one_or_none() is None:
        db.add(ChartOfAccount(company_id=cid, code="1105", name="Trading Fabric Inventory",
                              account_type=AccountType.ASSET.value, is_group=False,
                              allow_posting=True, is_active=True, is_control=True))
    mapping = {"inventory": "1105", "input_cgst": "1121", "input_sgst": "1122",
               "input_igst": "1123", "payable": "2010"}
    for slot, code in mapping.items():
        if db.execute(select(PostingRule).where(
                PostingRule.company_id == cid, PostingRule.event_type == "TRADING_GRN",
                PostingRule.role == slot)).scalar_one_or_none() is None:
            db.add(PostingRule(company_id=cid, event_type="TRADING_GRN", role=slot, account_code=code))
    db.flush()


def _bal(db, cid, item_id):
    return inventory.balance(db, company_id=cid, item_type=ItemType.TRADING_FABRIC, item_id=item_id)["qty_on_hand"]


def _direct_grn(db, cid, supplier_id, item_id, wh_id, *, number, invoice, qty="100", rate="120",
                tax_code_id=None):
    return tp.create_trading_grn(
        db, cid, supplier_id=supplier_id, number=number, grn_date=GRN_DATE,
        supplier_invoice_no=invoice, lines=[{
            "trading_item_id": item_id, "warehouse_id": wh_id, "qty_received": qty,
            "rate": rate, "tax_code_id": tax_code_id}])


# ============================ purchase orders ===============================

def test_po_create_approve_outstanding(db, demo):
    cid = demo["company_id"]
    a, b = _item(db, cid, "TF-A"), _item(db, cid, "TF-B")
    db.commit()
    po = tp.create_trading_po(db, cid, supplier_id=demo["supplier_id"], number="TPO-1",
                              order_date=GRN_DATE, lines=[
                                  {"trading_item_id": a.id, "qty_ordered": "100", "rate": "50"},
                                  {"trading_item_id": b.id, "qty_ordered": "200", "rate": "60"}])
    db.commit()
    assert len(po.lines) == 2 and po.status == TradingPOStatus.DRAFT.value
    tp.approve_trading_po(db, cid, po.id); db.commit()
    assert tp.get_trading_po(db, cid, po.id).status == TradingPOStatus.APPROVED.value
    out = {r["trading_item_id"]: r["outstanding"] for r in tp.po_outstanding(po)}
    assert out[str(a.id)] == D("100.000000") and out[str(b.id)] == D("200.000000")


def test_po_cancel_without_receipt(db, demo):
    cid = demo["company_id"]
    a = _item(db, cid, "TF-CANC"); db.commit()
    po = tp.create_trading_po(db, cid, supplier_id=demo["supplier_id"], number="TPO-C",
                              order_date=GRN_DATE, lines=[{"trading_item_id": a.id, "qty_ordered": "10"}])
    db.commit()
    tp.cancel_trading_po(db, cid, po.id); db.commit()
    assert tp.get_trading_po(db, cid, po.id).status == TradingPOStatus.CANCELLED.value


def test_po_invalid_references_rejected(db, demo):
    cid = demo["company_id"]
    other = Company(code=f"OC-{uuid.uuid4().hex[:4]}", name="Other"); db.add(other); db.flush()
    foreign_item = trading.create_trading_item(db, other.id, code="FX", name="FX"); db.flush()
    # cross-company SKU on our PO
    with pytest.raises(tp.TradingPurchaseError):
        tp.create_trading_po(db, cid, supplier_id=demo["supplier_id"], number="TPO-X",
                             order_date=GRN_DATE, lines=[{"trading_item_id": foreign_item.id, "qty_ordered": "5"}])
    db.rollback()
    # cross-company supplier
    with pytest.raises(tp.TradingPurchaseError):
        tp.create_trading_po(db, cid, supplier_id=uuid.uuid4(), number="TPO-Y",
                             order_date=GRN_DATE, lines=[{"trading_item_id": _item(db, cid, "TF-Z").id, "qty_ordered": "5"}])
    db.rollback()


# ============================== direct GRN ==================================

def test_direct_grn_creates_stock_on_approval(db, demo):
    cid = demo["company_id"]
    _configure_grn_accounts(db, cid)
    it = _item(db, cid, "TF-DIR"); wh = _trade_wh(db, cid); db.commit()
    grn = _direct_grn(db, cid, demo["supplier_id"], it.id, wh.id, number="TGRN-D1",
                      invoice="INV-D1", tax_code_id=demo["tax_id"])
    db.commit()
    assert grn.source_type == "DIRECT" and grn.purchase_order_id is None
    assert _bal(db, cid, it.id) == D("0.000000")          # draft: no stock
    tp.submit_trading_grn(db, cid, grn.id); db.commit()
    assert _bal(db, cid, it.id) == D("0.000000")          # submitted: no stock
    res = tp.approve_trading_grn(db, cid, grn.id); db.commit()
    assert res["voucher"] is not None
    assert _bal(db, cid, it.id) == D("100.000000")        # approved: stock exists
    assert tp.get_trading_grn(db, cid, grn.id).status == TradingGRNStatus.APPROVED.value


def test_duplicate_supplier_invoice_rejected(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-DUP"); wh = _trade_wh(db, cid); db.commit()
    _direct_grn(db, cid, demo["supplier_id"], it.id, wh.id, number="TGRN-U1", invoice="SAME-INV")
    db.commit()
    with pytest.raises(tp.TradingDuplicateInvoice):
        _direct_grn(db, cid, demo["supplier_id"], it.id, wh.id, number="TGRN-U2", invoice="same-inv")  # normalised
    db.rollback()


def test_rejected_and_cancelled_grn_create_no_stock(db, demo):
    cid = demo["company_id"]
    _configure_grn_accounts(db, cid)
    it = _item(db, cid, "TF-REJ"); wh = _trade_wh(db, cid); db.commit()
    g1 = _direct_grn(db, cid, demo["supplier_id"], it.id, wh.id, number="TGRN-R1", invoice="INV-R1")
    db.commit()
    tp.submit_trading_grn(db, cid, g1.id); tp.reject_trading_grn(db, cid, g1.id, reason="damaged"); db.commit()
    assert tp.get_trading_grn(db, cid, g1.id).status == TradingGRNStatus.REJECTED.value
    assert _bal(db, cid, it.id) == D("0.000000")
    g2 = _direct_grn(db, cid, demo["supplier_id"], it.id, wh.id, number="TGRN-R2", invoice="INV-R2")
    db.commit()
    tp.cancel_trading_grn(db, cid, g2.id); db.commit()
    assert tp.get_trading_grn(db, cid, g2.id).status == TradingGRNStatus.CANCELLED.value
    assert _bal(db, cid, it.id) == D("0.000000")


def test_approval_idempotent_single_layer(db, demo):
    cid = demo["company_id"]
    _configure_grn_accounts(db, cid)
    it = _item(db, cid, "TF-IDEM"); wh = _trade_wh(db, cid); db.commit()
    grn = _direct_grn(db, cid, demo["supplier_id"], it.id, wh.id, number="TGRN-I1", invoice="INV-I1")
    db.commit()
    tp.submit_trading_grn(db, cid, grn.id); db.commit()
    r1 = tp.approve_trading_grn(db, cid, grn.id); db.commit()
    r2 = tp.approve_trading_grn(db, cid, grn.id); db.commit()   # re-approve: idempotent
    assert r2["already_approved"] is True
    layers = db.execute(select(StockLayer).where(
        StockLayer.company_id == cid, StockLayer.item_type == ItemType.TRADING_FABRIC.value,
        StockLayer.item_id == it.id)).scalars().all()
    assert len(layers) == 1                                  # exactly one layer
    vouchers = db.execute(select(Voucher).where(
        Voucher.company_id == cid, Voucher.posting_reference == f"TGRN:{grn.number}")).scalars().all()
    assert len(vouchers) == 1                                # exactly one voucher


# ============================ PO-based GRN ==================================

def _open_po(db, cid, supplier_id, item_id, qty="100", rate="50"):
    po = tp.create_trading_po(db, cid, supplier_id=supplier_id, number=f"TPO-{uuid.uuid4().hex[:5]}",
                              order_date=GRN_DATE, lines=[{"trading_item_id": item_id, "qty_ordered": qty, "rate": rate}])
    tp.approve_trading_po(db, cid, po.id)
    db.flush()
    return po


def test_partial_receipts_update_outstanding(db, demo):
    cid = demo["company_id"]
    _configure_grn_accounts(db, cid)
    it = _item(db, cid, "TF-PART"); wh = _trade_wh(db, cid); db.commit()
    po = _open_po(db, cid, demo["supplier_id"], it.id, qty="100"); db.commit()
    pol = po.lines[0]
    # first GRN: 60
    g1 = tp.create_trading_grn(db, cid, supplier_id=demo["supplier_id"], number="TGRN-P1",
                               grn_date=GRN_DATE, supplier_invoice_no="INV-P1", purchase_order_id=po.id,
                               lines=[{"trading_item_id": it.id, "warehouse_id": wh.id, "qty_received": "60",
                                       "rate": "50", "purchase_order_line_id": pol.id}])
    db.commit()
    tp.submit_trading_grn(db, cid, g1.id); tp.approve_trading_grn(db, cid, g1.id); db.commit()
    po = tp.get_trading_po(db, cid, po.id)
    assert po.status == TradingPOStatus.PARTIAL.value
    assert tp.po_outstanding(po)[0]["outstanding"] == D("40.000000")
    # second GRN: 40 -> completed
    g2 = tp.create_trading_grn(db, cid, supplier_id=demo["supplier_id"], number="TGRN-P2",
                               grn_date=GRN_DATE, supplier_invoice_no="INV-P2", purchase_order_id=po.id,
                               lines=[{"trading_item_id": it.id, "warehouse_id": wh.id, "qty_received": "40",
                                       "rate": "55", "purchase_order_line_id": pol.id}])
    db.commit()
    tp.submit_trading_grn(db, cid, g2.id); tp.approve_trading_grn(db, cid, g2.id); db.commit()
    po = tp.get_trading_po(db, cid, po.id)
    assert po.status == TradingPOStatus.COMPLETED.value
    assert tp.po_outstanding(po)[0]["outstanding"] == D("0.000000")
    # FIFO: two distinct layers at different rates
    layers = db.execute(select(StockLayer).where(
        StockLayer.company_id == cid, StockLayer.item_type == ItemType.TRADING_FABRIC.value,
        StockLayer.item_id == it.id).order_by(StockLayer.received_at)).scalars().all()
    assert len(layers) == 2
    assert {str(l.unit_cost) for l in layers} == {"50.00", "55.00"}


def test_excess_receipt_blocked_without_authorisation(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-OVR"); wh = _trade_wh(db, cid); db.commit()
    po = _open_po(db, cid, demo["supplier_id"], it.id, qty="100"); db.commit()
    pol = po.lines[0]
    with pytest.raises(tp.TradingOverReceiptError):
        tp.create_trading_grn(db, cid, supplier_id=demo["supplier_id"], number="TGRN-O1",
                              grn_date=GRN_DATE, supplier_invoice_no="INV-O1", purchase_order_id=po.id,
                              over_receive_authorized=False,
                              lines=[{"trading_item_id": it.id, "warehouse_id": wh.id, "qty_received": "150",
                                      "rate": "50", "purchase_order_line_id": pol.id}])
    db.rollback()


def test_authorised_excess_receipt_requires_reason(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-OVR2"); wh = _trade_wh(db, cid); db.commit()
    po = _open_po(db, cid, demo["supplier_id"], it.id, qty="100"); db.commit()
    pol = po.lines[0]
    # authorised but NO reason -> rejected
    with pytest.raises(tp.TradingOverReceiptError):
        tp.create_trading_grn(db, cid, supplier_id=demo["supplier_id"], number="TGRN-O2",
                              grn_date=GRN_DATE, supplier_invoice_no="INV-O2", purchase_order_id=po.id,
                              over_receive_authorized=True, over_receive_reason="  ",
                              lines=[{"trading_item_id": it.id, "warehouse_id": wh.id, "qty_received": "150",
                                      "rate": "50", "purchase_order_line_id": pol.id}])
    db.rollback()
    # authorised WITH reason -> accepted + flagged
    grn = tp.create_trading_grn(db, cid, supplier_id=demo["supplier_id"], number="TGRN-O3",
                                grn_date=GRN_DATE, supplier_invoice_no="INV-O3", purchase_order_id=po.id,
                                over_receive_authorized=True, over_receive_reason="supplier shipped extra",
                                authorized_by=uuid.uuid4(),
                                lines=[{"trading_item_id": it.id, "warehouse_id": wh.id, "qty_received": "150",
                                        "rate": "50", "purchase_order_line_id": pol.id}])
    db.commit()
    assert grn.is_over_received and grn.over_receive_reason == "supplier shipped extra"


def test_concurrent_approval_cannot_over_receive(db, demo):
    """Two GRNs each created for the full outstanding; the second to approve must
    be blocked at approval time (authoritative outstanding re-check)."""
    cid = demo["company_id"]
    _configure_grn_accounts(db, cid)
    it = _item(db, cid, "TF-RACE"); wh = _trade_wh(db, cid); db.commit()
    po = _open_po(db, cid, demo["supplier_id"], it.id, qty="100"); db.commit()
    pol = po.lines[0]
    g1 = tp.create_trading_grn(db, cid, supplier_id=demo["supplier_id"], number="TGRN-RC1",
                               grn_date=GRN_DATE, supplier_invoice_no="INV-RC1", purchase_order_id=po.id,
                               lines=[{"trading_item_id": it.id, "warehouse_id": wh.id, "qty_received": "100",
                                       "rate": "50", "purchase_order_line_id": pol.id}])
    g2 = tp.create_trading_grn(db, cid, supplier_id=demo["supplier_id"], number="TGRN-RC2",
                               grn_date=GRN_DATE, supplier_invoice_no="INV-RC2", purchase_order_id=po.id,
                               lines=[{"trading_item_id": it.id, "warehouse_id": wh.id, "qty_received": "100",
                                       "rate": "50", "purchase_order_line_id": pol.id}])
    db.commit()
    tp.submit_trading_grn(db, cid, g1.id); tp.submit_trading_grn(db, cid, g2.id); db.commit()
    tp.approve_trading_grn(db, cid, g1.id); db.commit()        # consumes the 100 outstanding
    with pytest.raises(tp.TradingOverReceiptError):
        tp.approve_trading_grn(db, cid, g2.id)                 # now outstanding 0 -> blocked
    db.rollback()


# ============================ warehouse isolation ===========================

@pytest.mark.parametrize("cat", ["GREIGE", "SEMI_FINISHED", "FINISHED_GOODS", "YARN_STORE",
                                 "VENDOR_CUSTODY", None])
def test_grn_rejects_non_trading_warehouse(db, demo, cat):
    cid = demo["company_id"]
    it = _item(db, cid, f"TF-W{cat or 'N'}"); wh = _wh(db, cid, f"WH-{cat or 'NONE'}", cat); db.commit()
    with pytest.raises(tp.TradingPurchaseError):
        _direct_grn(db, cid, demo["supplier_id"], it.id, wh.id, number=f"TGRN-W{cat or 'N'}",
                    invoice=f"INV-W{cat or 'N'}")
    db.rollback()


# ================================ FIFO / separation =========================

def test_fifo_separate_warehouses_and_no_manufacturing_artifacts(db, demo):
    cid = demo["company_id"]
    _configure_grn_accounts(db, cid)
    it = _item(db, cid, "TF-FIFO")
    w1 = _trade_wh(db, cid, "WH-T1"); w2 = _trade_wh(db, cid, "WH-T2")
    db.commit()
    rolls0 = db.execute(select(FabricRoll).where(FabricRoll.company_id == cid)).scalars().all()
    edges0 = db.execute(select(RollGenealogyEdge).where(RollGenealogyEdge.company_id == cid)).scalars().all()
    chem0 = inventory.balance(db, company_id=cid, item_type=ItemType.CHEMICAL, item_id=demo["chemicals"]["RB-01"])["qty_on_hand"]
    for i, (w, q) in enumerate([(w1, "100"), (w2, "40")], start=1):
        g = _direct_grn(db, cid, demo["supplier_id"], it.id, w.id, number=f"TGRN-F{i}", invoice=f"INV-F{i}", qty=q)
        tp.submit_trading_grn(db, cid, g.id); tp.approve_trading_grn(db, cid, g.id); db.commit()
    # per-warehouse balances separate
    assert inventory.balance(db, company_id=cid, item_type=ItemType.TRADING_FABRIC, item_id=it.id,
                             warehouse_id=w1.id)["qty_on_hand"] == D("100.000000")
    assert inventory.balance(db, company_id=cid, item_type=ItemType.TRADING_FABRIC, item_id=it.id,
                             warehouse_id=w2.id)["qty_on_hand"] == D("40.000000")
    # manufactured/chemical untouched; no roll/genealogy created
    assert inventory.balance(db, company_id=cid, item_type=ItemType.CHEMICAL, item_id=demo["chemicals"]["RB-01"])["qty_on_hand"] == chem0
    assert len(db.execute(select(FabricRoll).where(FabricRoll.company_id == cid)).scalars().all()) == len(rolls0)
    assert len(db.execute(select(RollGenealogyEdge).where(RollGenealogyEdge.company_id == cid)).scalars().all()) == len(edges0)


def test_po_vs_invoice_tax_difference_audited_and_po_preserved(db, demo):
    """The GRN uses the actual invoice rate/tax; the PO line keeps its own values,
    and the difference is recorded in the audit trail (safeguard 3)."""
    import json as _json
    from app.models.audit import AuditLog
    cid = demo["company_id"]
    it = _item(db, cid, "TF-TAXDIFF"); wh = _trade_wh(db, cid); db.commit()
    po = _open_po(db, cid, demo["supplier_id"], it.id, qty="100", rate="50"); db.commit()
    pol = po.lines[0]
    grn = tp.create_trading_grn(db, cid, supplier_id=demo["supplier_id"], number="TGRN-TD",
                                grn_date=GRN_DATE, supplier_invoice_no="INV-TD", purchase_order_id=po.id,
                                lines=[{"trading_item_id": it.id, "warehouse_id": wh.id, "qty_received": "10",
                                        "rate": "55", "purchase_order_line_id": pol.id}])  # invoice rate 55 != PO 50
    db.commit()
    # GRN line carries the ACTUAL invoice rate; the PO line is untouched.
    assert grn.lines[0].rate == D("55.00")
    assert tp.get_trading_po(db, cid, po.id).lines[0].rate == D("50.00")
    # the difference is auditable
    logs = db.execute(select(AuditLog).where(AuditLog.entity == "trading_purchase",
                                             AuditLog.entity_id == str(grn.id))).scalars().all()
    create_log = [l for l in logs if "po_tax_differences" in (l.detail or "")]
    assert create_log
    diffs = _json.loads(create_log[0].detail)["po_tax_differences"]
    assert diffs and diffs[0]["po_rate"] == "50.00" and diffs[0]["invoice_rate"] == "55.00"


# ============================ company / factory isolation ===================

def test_cross_company_grn_access_rejected(db, demo):
    cid = demo["company_id"]
    _configure_grn_accounts(db, cid)
    it = _item(db, cid, "TF-XCO"); wh = _trade_wh(db, cid); db.commit()
    grn = _direct_grn(db, cid, demo["supplier_id"], it.id, wh.id, number="TGRN-XCO", invoice="INV-XCO")
    db.commit()
    other = Company(code=f"OC-{uuid.uuid4().hex[:4]}", name="Other"); db.add(other); db.flush()
    with pytest.raises(tp.TradingPurchaseError):
        tp.get_trading_grn(db, other.id, grn.id)      # company B cannot see company A's GRN


def test_wrong_factory_warehouse_rejected_on_grn(db, demo):
    cid = demo["company_id"]
    b1 = Branch(company_id=cid, code="FX1", name="F1"); b2 = Branch(company_id=cid, code="FX2", name="F2")
    db.add_all([b1, b2]); db.flush()
    it = _item(db, cid, "TF-FAC")
    wh = Warehouse(company_id=cid, code="WH-FAC", name="WH-FAC", kind="STORE",
                   category=WarehouseCategory.TRADING.value, branch_id=b1.id)
    db.add(wh); db.commit()
    # GRN scoped to factory 2, but the TRADING warehouse belongs to factory 1
    with pytest.raises(tp.TradingPurchaseError):
        tp.create_trading_grn(db, cid, supplier_id=demo["supplier_id"], number="TGRN-FAC",
                              grn_date=GRN_DATE, supplier_invoice_no="INV-FAC", factory_id=b2.id,
                              lines=[{"trading_item_id": it.id, "warehouse_id": wh.id, "qty_received": "5",
                                      "rate": "1"}])
    db.rollback()


def test_inactive_sku_receipt_rejected(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-INACT", active=False); wh = _trade_wh(db, cid); db.commit()
    with pytest.raises(tp.TradingPurchaseError):
        _direct_grn(db, cid, demo["supplier_id"], it.id, wh.id, number="TGRN-IN", invoice="INV-IN")
    db.rollback()


# ================================ GST =======================================

def test_intra_state_cgst_sgst(db, demo):
    cid = demo["company_id"]
    it = _item(db, cid, "TF-GST1"); wh = _trade_wh(db, cid); db.commit()
    grn = _direct_grn(db, cid, demo["supplier_id"], it.id, wh.id, number="TGRN-G1", invoice="INV-G1",
                      qty="100", rate="100", tax_code_id=demo["tax_id"])
    db.commit()
    ln = grn.lines[0]
    assert ln.taxable_value == D("10000.00")
    assert ln.cgst == D("900.00") and ln.sgst == D("900.00") and ln.igst == D("0.00")  # 9%+9% intra


def test_inter_state_igst(db, demo):
    cid = demo["company_id"]
    inter_supplier = _supplier(db, cid, "SUP-INTER", state="06")   # different state than company (03)
    it = _item(db, cid, "TF-GST2"); wh = _trade_wh(db, cid); db.commit()
    grn = _direct_grn(db, cid, inter_supplier.id, it.id, wh.id, number="TGRN-G2", invoice="INV-G2",
                      qty="100", rate="100", tax_code_id=demo["tax_id"])
    db.commit()
    ln = grn.lines[0]
    assert ln.igst == D("1800.00") and ln.cgst == D("0.00") and ln.sgst == D("0.00")   # 18% inter


# ============================ CA posting gate ===============================

def test_missing_mappings_block_posting_no_stock_no_fallback(db, demo):
    """With TRADING_GRN mappings unconfigured, approval is blocked: no stock, no
    voucher, and NO fall-back to chemical/manufactured accounts."""
    cid = demo["company_id"]
    # deliberately DO NOT configure trading accounts
    it = _item(db, cid, "TF-NOACCT"); wh = _trade_wh(db, cid); db.commit()
    grn = _direct_grn(db, cid, demo["supplier_id"], it.id, wh.id, number="TGRN-NA", invoice="INV-NA",
                      tax_code_id=demo["tax_id"])
    db.commit()
    tp.submit_trading_grn(db, cid, grn.id); db.commit()
    with pytest.raises(tp.TradingPostingBlocked):
        tp.approve_trading_grn(db, cid, grn.id)
    db.rollback()
    # still submitted, no stock, no voucher
    assert tp.get_trading_grn(db, cid, grn.id).status == TradingGRNStatus.SUBMITTED.value
    assert _bal(db, cid, it.id) == D("0.000000")
    assert db.execute(select(Voucher).where(
        Voucher.company_id == cid, Voucher.posting_reference == f"TGRN:{grn.number}")).scalar_one_or_none() is None


def test_configured_mappings_post_via_engine(db, demo):
    cid = demo["company_id"]
    _configure_grn_accounts(db, cid)
    it = _item(db, cid, "TF-ACCT"); wh = _trade_wh(db, cid); db.commit()
    grn = _direct_grn(db, cid, demo["supplier_id"], it.id, wh.id, number="TGRN-OK", invoice="INV-OK",
                      qty="100", rate="100", tax_code_id=demo["tax_id"])
    db.commit()
    tp.submit_trading_grn(db, cid, grn.id); db.commit()
    res = tp.approve_trading_grn(db, cid, grn.id); db.commit()
    v = res["voucher"]
    assert v is not None
    # voucher debits trading inventory 1105 + input GST, credits payable; balanced
    from app.models.accounting import JournalLine
    jls = db.execute(select(JournalLine).where(JournalLine.voucher_id == v.id)).scalars().all()
    code_by_id = {a.id: a.code for a in db.execute(
        select(ChartOfAccount).where(ChartOfAccount.company_id == cid)).scalars()}
    accts = {code_by_id.get(j.account_id) for j in jls}
    assert "1105" in accts and "2010" in accts            # trading inventory + payable used
    assert "1100" not in accts and "1070" not in accts and "1085" not in accts  # no chemical/manufactured
    assert sum(D(str(j.debit)) for j in jls) == sum(D(str(j.credit)) for j in jls)
