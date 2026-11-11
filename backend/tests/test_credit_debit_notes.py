"""Credit notes (sales) and debit notes (purchase returns).

A sales credit note reverses the financial side of a sale (receivable + output
GST). A purchase debit note returns chemicals: it relieves FIFO stock, reduces
the payable and reverses input GST. Both post balanced vouchers, are
idempotent, and flow into the GST registers as negative entries.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models.commercial import (
    CreditNote,
    CreditNoteLine,
    DebitNote,
    DebitNoteLine,
    DocStatus,
    GoodsReceiptNote,
    GRNLine,
)
from app.models.inventory import ItemType
from app.services import credit_debit_notes as cdn, inventory
from app.services.accounting import gst
from app.services.procurement import post_chemical_grn


# ------------------------------- credit note --------------------------------

def test_sales_credit_note_balances_and_reverses(db, demo):
    note = CreditNote(company_id=demo["company_id"], customer_id=demo["customer_id"],
                      number="CN-001", note_date=date(2026, 11, 8), reason="Rate difference")
    note.lines.append(CreditNoteLine(line_no=1, description="Price adjustment",
                                     qty=Decimal("1"), rate=Decimal("10000"), tax_code_id=demo["tax_id"]))
    db.add(note); db.flush()
    v = cdn.post_sales_credit_note(db, note)
    db.commit()
    # 10,000 taxable; GST18 intra -> 900 + 900; gross 11,800.
    assert v.total_debit == v.total_credit == Decimal("11800.00")
    assert note.taxable_value == Decimal("10000.00") and note.tax_amount == Decimal("1800.00")
    assert note.status == DocStatus.POSTED


def test_credit_note_is_idempotent(db, demo):
    note = CreditNote(company_id=demo["company_id"], customer_id=demo["customer_id"],
                      number="CN-002", note_date=date(2026, 11, 8))
    note.lines.append(CreditNoteLine(line_no=1, qty=Decimal("1"), rate=Decimal("5000"), tax_code_id=demo["tax_id"]))
    db.add(note); db.flush()
    v1 = cdn.post_sales_credit_note(db, note); db.commit()
    v2 = cdn.post_sales_credit_note(db, note); db.commit()
    assert v1.id == v2.id


def test_credit_note_shows_negative_in_sales_register(db, demo):
    note = CreditNote(company_id=demo["company_id"], customer_id=demo["customer_id"],
                      number="CN-003", note_date=date(2026, 11, 8))
    note.lines.append(CreditNoteLine(line_no=1, qty=Decimal("1"), rate=Decimal("10000"), tax_code_id=demo["tax_id"]))
    db.add(note); db.flush()
    cdn.post_sales_credit_note(db, note); db.commit()
    reg = gst.sales_register(db, demo["company_id"], date(2026, 11, 1), date(2026, 11, 30))
    cn_rows = [r for r in reg["rows"] if r.get("doc_type") == "CREDIT_NOTE"]
    assert len(cn_rows) == 1 and cn_rows[0]["taxable"] == Decimal("-10000.00")
    assert reg["totals"]["taxable"] == Decimal("-10000.00")   # only the CN in range


# -------------------------------- debit note --------------------------------

def _receive_extra_stock(db, demo, qty="100"):
    grn = GoodsReceiptNote(company_id=demo["company_id"], supplier_id=demo["supplier_id"],
                           number="GRN-DN", grn_date=date(2026, 11, 8))
    grn.lines.append(GRNLine(line_no=1, chemical_id=demo["chemicals"]["RB-01"],
                             chemical_lot_id=demo["lots"]["RB-01"], warehouse_id=demo["warehouses"]["chem"],
                             qty=Decimal(qty), unit_cost=Decimal("1200"), tax_code_id=demo["tax_id"]))
    db.add(grn); db.flush()
    post_chemical_grn(db, grn); db.commit()


def test_purchase_debit_note_returns_stock_and_balances(db, demo):
    _receive_extra_stock(db, demo, "100")
    before = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.CHEMICAL,
                               item_id=demo["chemicals"]["RB-01"])["qty_on_hand"]

    note = DebitNote(company_id=demo["company_id"], supplier_id=demo["supplier_id"],
                     number="DN-001", note_date=date(2026, 11, 8), reason="Damaged goods")
    note.lines.append(DebitNoteLine(line_no=1, chemical_id=demo["chemicals"]["RB-01"],
                                    chemical_lot_id=demo["lots"]["RB-01"], warehouse_id=demo["warehouses"]["chem"],
                                    qty=Decimal("20"), tax_code_id=demo["tax_id"]))
    db.add(note); db.flush()
    v = cdn.post_purchase_debit_note(db, note)
    db.commit()

    # 20 kg returned at 1200 = 24,000 taxable; GST18 intra -> 2,160 + 2,160; gross 28,320.
    assert v.total_debit == v.total_credit == Decimal("28320.00")
    assert note.taxable_value == Decimal("24000.00")
    after = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.CHEMICAL,
                              item_id=demo["chemicals"]["RB-01"])["qty_on_hand"]
    assert before - after == Decimal("20.000000")             # stock relieved


def test_debit_note_shows_negative_in_purchase_register(db, demo):
    _receive_extra_stock(db, demo, "100")
    note = DebitNote(company_id=demo["company_id"], supplier_id=demo["supplier_id"],
                     number="DN-002", note_date=date(2026, 11, 8))
    note.lines.append(DebitNoteLine(line_no=1, chemical_id=demo["chemicals"]["RB-01"],
                                    chemical_lot_id=demo["lots"]["RB-01"], warehouse_id=demo["warehouses"]["chem"],
                                    qty=Decimal("10"), tax_code_id=demo["tax_id"]))
    db.add(note); db.flush()
    cdn.post_purchase_debit_note(db, note); db.commit()
    reg = gst.purchase_register(db, demo["company_id"], date(2026, 11, 1), date(2026, 11, 30))
    dn_rows = [r for r in reg["rows"] if r.get("doc_type") == "DEBIT_NOTE"]
    assert len(dn_rows) == 1 and dn_rows[0]["taxable"] == Decimal("-12000.00")  # 10 * 1200
