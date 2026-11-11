"""Physical stock count → a controlled inventory adjustment.

Compares the counted quantity to the system book quantity for an item in a
warehouse and posts the costed variance:
  * shortage — relieve stock (FIFO) and debit Inventory Adjustment / credit the
    inventory control account;
  * surplus — add stock at the current average cost and debit the inventory
    account / credit Inventory Adjustment.
The sub-ledger and the GL move by the same amount, so TEST 17 keeps tying out.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, QTY_SCALE, quantize
from app.models.inventory import ItemType, StockCount
from app.models.masters import Chemical
from app.services import inventory
from app.services.accounting import posting_engine as pe
from app.services.accounting.posting_rules import resolve_account
from app.services.accounting.posting_engine import fiscal_year_for
from app.services.sequences import next_document_number

MZERO = quantize(0, MONEY_SCALE)
QZERO = quantize(0, QTY_SCALE)


class StockCountError(Exception):
    """An invalid stock-count request."""


def _inventory_account(db: Session, company_id: uuid.UUID, item_type: ItemType, item_id: uuid.UUID) -> str:
    if item_type == ItemType.YARN:
        return resolve_account(db, company_id, "STOCK_ADJUSTMENT", "yarn")
    if item_type == ItemType.CHEMICAL:
        chem = db.get(Chemical, item_id)
        slot = "dye" if (chem and chem.is_dye) else "chemical"
        return resolve_account(db, company_id, "STOCK_ADJUSTMENT", slot)
    raise StockCountError("Stock count supports chemical and yarn items")


def post_stock_count(db: Session, *, company_id: uuid.UUID, item_type, item_id: uuid.UUID,
                     warehouse_id: uuid.UUID | None, counted_qty, count_date: date | None = None,
                     reason: str | None = None, number: str | None = None,
                     created_by: uuid.UUID | None = None) -> dict:
    it = item_type if isinstance(item_type, ItemType) else ItemType(item_type)
    count_date = count_date or date.today()
    number = number or next_document_number(db, company_id=company_id, doc_type="STKCOUNT",
                                             fiscal_year=fiscal_year_for(count_date), prefix="SC-")
    ref = f"STKADJ:{number}"
    if pe.find_existing(db, company_id, ref) is not None:
        raise StockCountError("Stock count already posted")

    bal = inventory.balance(db, company_id=company_id, item_type=it, item_id=item_id, warehouse_id=warehouse_id)
    book_qty = quantize(Decimal(str(bal["qty_on_hand"])), QTY_SCALE)
    book_value = quantize(Decimal(str(bal["value"])), MONEY_SCALE)
    counted = quantize(Decimal(str(counted_qty)), QTY_SCALE)
    if counted < 0:
        raise StockCountError("Counted quantity cannot be negative")
    variance = quantize(counted - book_qty, QTY_SCALE)

    inv_acct = _inventory_account(db, company_id, it, item_id)
    adj_acct = resolve_account(db, company_id, "STOCK_ADJUSTMENT", "adjustment")
    voucher = None
    variance_value = MZERO

    if variance < 0:   # shortage -> relieve stock
        res = inventory.consume(db, company_id=company_id, item_type=it, item_id=item_id,
                                qty=quantize(-variance, QTY_SCALE), warehouse_id=warehouse_id,
                                event_type="STOCK_COUNT", source_module="stock_count",
                                source_id=number, posting_reference=ref)
        variance_value = quantize(-res["total_cost"], MONEY_SCALE)
        voucher = pe.post_voucher(db, pe.PostingRequest(
            company_id=company_id, voucher_type_code="SJ", voucher_date=count_date, posting_reference=ref,
            narration=f"Stock shortage {number}", source_module="stock_count", source_id=number,
            created_by=created_by,
            lines=[pe.PostingLine(adj_acct, debit=res["total_cost"], narration="Inventory shortage"),
                   pe.PostingLine(inv_acct, credit=res["total_cost"], narration="Reduce inventory")]))
    elif variance > 0:   # surplus -> add stock at current average cost
        avg = quantize(book_value / book_qty, MONEY_SCALE) if book_qty > 0 else MZERO
        if avg <= 0:
            chem = db.get(Chemical, item_id) if it == ItemType.CHEMICAL else None
            avg = quantize(Decimal(str(chem.standard_cost)), MONEY_SCALE) if chem else MZERO
        add_value = quantize(variance * avg, MONEY_SCALE)
        variance_value = add_value
        inventory.receive(db, company_id=company_id, item_type=it, item_id=item_id, qty=variance,
                          unit_cost=avg, warehouse_id=warehouse_id, event_type="STOCK_COUNT",
                          source_module="stock_count", source_id=number, posting_reference=ref)
        if add_value > 0:
            voucher = pe.post_voucher(db, pe.PostingRequest(
                company_id=company_id, voucher_type_code="SJ", voucher_date=count_date, posting_reference=ref,
                narration=f"Stock surplus {number}", source_module="stock_count", source_id=number,
                created_by=created_by,
                lines=[pe.PostingLine(inv_acct, debit=add_value, narration="Increase inventory"),
                       pe.PostingLine(adj_acct, credit=add_value, narration="Inventory surplus")]))

    sc = StockCount(company_id=company_id, number=number, count_date=count_date,
                    item_type=it.value, item_id=item_id, warehouse_id=warehouse_id,
                    book_qty=book_qty, counted_qty=counted, variance_qty=variance,
                    variance_value=variance_value, reason=reason,
                    posting_reference=ref if voucher else None, created_by=created_by)
    db.add(sc)
    db.flush()
    return {"stock_count_id": str(sc.id), "number": number, "book_qty": book_qty,
            "counted_qty": counted, "variance_qty": variance, "variance_value": variance_value,
            "voucher": voucher}
