"""Stores & spares: receive consumables into stock, issue them to a machine.

Receipt opens FIFO stock (Dr Stores Inventory / Cr supplier payable or cash).
Issue draws FIFO at cost and expenses it (Dr Stores Consumption / Cr Stores
Inventory). Valuation shares the inventory engine, so store items also show up
in the stock-on-hand position.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.inventory import ItemType
from app.models.stores import StoreCategory, StoreItem
from app.services import inventory
from app.services.accounting import posting_engine as pe

STORES_INVENTORY = "1115"
STORES_CONSUMPTION = "6035"
PAYABLE = "2010"
CASH = "1010"


class StoreError(Exception):
    """An invalid stores action."""


def create_item(db: Session, *, company_id: uuid.UUID, code: str, name: str,
                category: str = "OTHER", uom: str = "PCS", reorder_level="0",
                created_by: uuid.UUID | None = None) -> StoreItem:
    if db.execute(select(StoreItem).where(StoreItem.company_id == company_id,
                                          StoreItem.code == code)).scalar_one_or_none():
        raise StoreError(f"Store item code {code} already exists")
    item = StoreItem(company_id=company_id, code=code.strip(), name=name.strip(),
                     category=StoreCategory(category), uom=uom,
                     reorder_level=quantize(Decimal(str(reorder_level or 0)), MONEY_SCALE),
                     created_by=created_by)
    db.add(item)
    db.flush()
    return item


def receive(db: Session, *, company_id: uuid.UUID, store_item_id: uuid.UUID, qty, unit_cost,
            warehouse_id: uuid.UUID | None = None, supplier_id: uuid.UUID | None = None,
            vdate: date | None = None, number: str | None = None,
            created_by: uuid.UUID | None = None) -> dict:
    item = db.get(StoreItem, store_item_id)
    if item is None or item.company_id != company_id:
        raise StoreError("Store item not found")
    q = Decimal(str(qty or 0)); c = Decimal(str(unit_cost or 0))
    if q <= 0 or c < 0:
        raise StoreError("Quantity must be positive and cost non-negative")
    value = quantize(q * c, MONEY_SCALE)
    vdate = vdate or date.today()
    ref = f"STRCV:{number or item.code}:{vdate.isoformat()}:{value}"
    inventory.receive(db, company_id=company_id, item_type=ItemType.STORE, item_id=store_item_id,
                      qty=q, unit_cost=c, warehouse_id=warehouse_id, source_module="stores",
                      source_id=str(store_item_id))
    credit = PAYABLE if supplier_id else CASH
    lines = [pe.PostingLine(STORES_INVENTORY, debit=value, narration=f"Stores: {item.name}")]
    if supplier_id:
        lines.append(pe.PostingLine(credit, credit=value, narration="Stores supplier",
                                    party_type="supplier", party_id=supplier_id))
    else:
        lines.append(pe.PostingLine(credit, credit=value, narration="Stores cash purchase"))
    voucher = pe.post_voucher(db, pe.PostingRequest(
        company_id=company_id, voucher_type_code="PI", voucher_date=vdate, posting_reference=ref,
        narration=f"Store receipt {item.code}", source_module="stores", source_id=str(item.id),
        created_by=created_by, lines=lines))
    db.flush()
    return {"store_item_id": str(item.id), "qty": str(q), "value": str(value),
            "voucher_number": voucher.number}


def issue(db: Session, *, company_id: uuid.UUID, store_item_id: uuid.UUID, qty,
          warehouse_id: uuid.UUID | None = None, machine: str | None = None,
          vdate: date | None = None, created_by: uuid.UUID | None = None) -> dict:
    item = db.get(StoreItem, store_item_id)
    if item is None or item.company_id != company_id:
        raise StoreError("Store item not found")
    q = Decimal(str(qty or 0))
    if q <= 0:
        raise StoreError("Issue quantity must be positive")
    res = inventory.consume(db, company_id=company_id, item_type=ItemType.STORE,
                            item_id=store_item_id, qty=q, warehouse_id=warehouse_id,
                            event_type="STORE_ISSUE", source_module="stores",
                            source_id=str(store_item_id))
    cost = quantize(res["total_cost"], MONEY_SCALE)
    vdate = vdate or date.today()
    ref = f"STRIS:{item.code}:{vdate.isoformat()}:{res['qty']}:{cost}"
    narr = f"{item.name} issued" + (f" to {machine}" if machine else "")
    voucher = pe.post_voucher(db, pe.PostingRequest(
        company_id=company_id, voucher_type_code="SJ", voucher_date=vdate, posting_reference=ref,
        narration=narr, source_module="stores", source_id=str(item.id), created_by=created_by,
        lines=[pe.PostingLine(STORES_CONSUMPTION, debit=cost, narration=narr),
               pe.PostingLine(STORES_INVENTORY, credit=cost, narration=f"Stores: {item.name}")]))
    db.flush()
    return {"store_item_id": str(item.id), "qty": str(res["qty"]), "cost": str(cost),
            "voucher_number": voucher.number}
