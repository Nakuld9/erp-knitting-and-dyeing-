"""Physical stock count (inventory adjustment) and sales-return restock."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.commercial import CreditNote, CreditNoteLine
from app.models.inventory import ItemType
from app.models.manufacturing import KnittingProduction
from app.services import credit_debit_notes as cdn, inventory, stock_count
from app.services import order_lifecycle as ol
from app.services.manufacturing.knitting import record_production


# ------------------------------ stock count ---------------------------------

def test_stock_count_shortage_posts_loss(db, demo):
    chem = demo["chemicals"]["RB-01"]; wh = demo["warehouses"]["chem"]
    before = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.CHEMICAL,
                               item_id=chem, warehouse_id=wh)["qty_on_hand"]
    # count 20 less than book -> shortage of 20 @ 1200 = 24,000.
    res = stock_count.post_stock_count(db, company_id=demo["company_id"], item_type="CHEMICAL",
                                       item_id=chem, warehouse_id=wh, counted_qty=str(before - 20))
    db.commit()
    assert res["variance_qty"] == Decimal("-20.000000")
    v = res["voucher"]
    assert v.total_debit == v.total_credit == Decimal("24000.00")   # Dr adjustment / Cr inventory
    after = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.CHEMICAL,
                              item_id=chem, warehouse_id=wh)["qty_on_hand"]
    assert before - after == Decimal("20.000000")


def test_stock_count_surplus_adds_stock(db, demo):
    chem = demo["chemicals"]["RB-01"]; wh = demo["warehouses"]["chem"]
    before = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.CHEMICAL,
                               item_id=chem, warehouse_id=wh)["qty_on_hand"]
    res = stock_count.post_stock_count(db, company_id=demo["company_id"], item_type="CHEMICAL",
                                       item_id=chem, warehouse_id=wh, counted_qty=str(before + 10))
    db.commit()
    assert res["variance_qty"] == Decimal("10.000000")
    v = res["voucher"]
    assert v.total_debit == v.total_credit == Decimal("12000.00")   # 10 @ 1200 avg
    after = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.CHEMICAL,
                              item_id=chem, warehouse_id=wh)["qty_on_hand"]
    assert after - before == Decimal("10.000000")


def test_stock_count_no_variance_no_voucher(db, demo):
    chem = demo["chemicals"]["RB-01"]; wh = demo["warehouses"]["chem"]
    bal = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.CHEMICAL,
                            item_id=chem, warehouse_id=wh)["qty_on_hand"]
    res = stock_count.post_stock_count(db, company_id=demo["company_id"], item_type="CHEMICAL",
                                       item_id=chem, warehouse_id=wh, counted_qty=str(bal))
    db.commit()
    assert res["variance_qty"] == Decimal("0.000000") and res["voucher"] is None


# --------------------------- sales-return restock ---------------------------

def _delivered_roll(db, demo):
    kp = KnittingProduction(company_id=demo["company_id"], number="KP-RET", machine_id=demo["machines"]["knit"],
                            yarn_id=demo["yarn_id"], yarn_lot_id=demo["yarn_lot_id"],
                            fabric_type_id=demo["fabric_type_id"], production_date=date(2026, 11, 8),
                            produced_kg=Decimal("250"), yarn_consumed_kg=Decimal("255"))
    db.add(kp); db.flush()
    rolls = record_production(db, kp, [{"weight_kg": "250", "warehouse_id": demo["warehouses"]["greige"]}])["rolls"]
    db.commit()
    ol.create_delivery(db, company_id=demo["company_id"], customer_id=demo["customer_id"],
                       sales_order_id=None, number="DC-RET", delivery_date=date(2026, 11, 8),
                       roll_ids=[rolls[0].id])
    db.commit()
    return rolls[0]


def test_sales_return_restocks_and_reverses_cogs(db, demo):
    roll = _delivered_roll(db, demo)
    assert roll.delivery_note_id is not None
    stock_before = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC,
                                     item_id=roll.id)["qty_on_hand"]
    assert stock_before == Decimal("0.000000")           # dispatched -> stock gone

    note = CreditNote(company_id=demo["company_id"], customer_id=demo["customer_id"],
                      number="CN-RET", note_date=date(2026, 11, 8), reason="Goods returned")
    note.lines.append(CreditNoteLine(line_no=1, qty=Decimal("250"), rate=Decimal("300"),
                                     tax_code_id=demo["tax_id"]))
    db.add(note); db.flush()
    res = cdn.post_sales_return(db, note, restock_roll_ids=[roll.id])
    db.commit()

    # financial credit note posted, plus a COGS-reversal voucher.
    assert res["credit_note_voucher"] is not None
    cogs_v = res["cogs_reversal_voucher"]
    assert cogs_v is not None and cogs_v.total_debit == cogs_v.total_credit == Decimal("61200.00")
    db.refresh(roll)
    assert roll.delivery_note_id is None and roll.status == "ACTIVE"
    back = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC,
                             item_id=roll.id)["qty_on_hand"]
    assert back == Decimal("250.000000")                 # back in stock
