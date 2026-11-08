"""FIFO perpetual-inventory engine.

Receipts open cost layers; consumption draws layers oldest-first and returns the
exact costed amount so the caller can post the matching accounting voucher. The
sum of layer movements equals the GL inventory posting (rule #11 reconciliation).
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, QTY_SCALE, quantize
from app.models.inventory import (
    InventoryTransaction,
    ItemType,
    MoveDirection,
    StockLayer,
    StockTransfer,
)

QZERO = quantize(0, QTY_SCALE)
MZERO = quantize(0, MONEY_SCALE)
_MAXD = date.max


class InsufficientStockError(Exception):
    pass


def receive(
    db: Session,
    *,
    company_id: uuid.UUID,
    item_type: ItemType,
    item_id: uuid.UUID,
    qty,
    unit_cost,
    lot_id: uuid.UUID | None = None,
    warehouse_id: uuid.UUID | None = None,
    expiry_date=None,
    event_type: str = "GRN",
    source_module: str | None = None,
    source_id: str | None = None,
    posting_reference: str | None = None,
) -> StockLayer:
    qty = quantize(qty, QTY_SCALE)
    unit_cost = quantize(unit_cost, MONEY_SCALE)
    if qty <= QZERO:
        raise ValueError("Receipt quantity must be positive")

    received_value = quantize(qty * unit_cost, MONEY_SCALE)
    layer = StockLayer(
        company_id=company_id, item_type=item_type.value if isinstance(item_type, ItemType) else item_type,
        item_id=item_id, lot_id=lot_id, warehouse_id=warehouse_id, expiry_date=expiry_date,
        qty_in=qty, qty_remaining=qty, unit_cost=unit_cost, value_remaining=received_value,
        source_module=source_module, source_id=source_id,
    )
    db.add(layer)
    db.flush()
    db.add(InventoryTransaction(
        company_id=company_id, item_type=layer.item_type, item_id=item_id, lot_id=lot_id,
        layer_id=layer.id, warehouse_id=warehouse_id, direction=MoveDirection.IN.value,
        qty=qty, unit_cost=unit_cost, total_cost=received_value,
        event_type=event_type, source_module=source_module, source_id=source_id,
        posting_reference=posting_reference,
    ))
    db.flush()
    return layer


def consume(
    db: Session,
    *,
    company_id: uuid.UUID,
    item_type: ItemType,
    item_id: uuid.UUID,
    qty,
    lot_id: uuid.UUID | None = None,
    warehouse_id: uuid.UUID | None = None,
    method: str = "FIFO",
    event_type: str = "CONSUME",
    source_module: str | None = None,
    source_id: str | None = None,
    posting_reference: str | None = None,
) -> dict:
    """Consume ``qty`` and return {total_cost, qty, movements:[...]}.

    ``method`` selects the layer draw order:
      * FIFO — oldest receipt first (default).
      * FEFO — earliest expiry first (layers with no expiry are drawn last),
        falling back to receipt order within the same expiry.
    If ``lot_id`` / ``warehouse_id`` is given, only that lot / warehouse is drawn.
    """
    qty = quantize(qty, QTY_SCALE)
    if qty <= QZERO:
        raise ValueError("Consumption quantity must be positive")
    itype = item_type.value if isinstance(item_type, ItemType) else item_type

    stmt = select(StockLayer).where(
        StockLayer.company_id == company_id,
        StockLayer.item_type == itype,
        StockLayer.item_id == item_id,
        StockLayer.qty_remaining > QZERO,
    )
    if lot_id is not None:
        stmt = stmt.where(StockLayer.lot_id == lot_id)
    if warehouse_id is not None:
        stmt = stmt.where(StockLayer.warehouse_id == warehouse_id)
    stmt = stmt.order_by(StockLayer.received_at, StockLayer.id)

    layers = db.execute(stmt).scalars().all()
    if method.upper() == "FEFO":
        # Earliest expiry first; null expiry sorts last. Stable on received_at.
        layers = sorted(layers, key=lambda l: (l.expiry_date is None, l.expiry_date or _MAXD, l.received_at))
    available = sum((l.qty_remaining for l in layers), QZERO)
    if available < qty:
        raise InsufficientStockError(
            f"Insufficient stock for item {item_id}: need {qty}, have {available}"
        )

    remaining = qty
    total_cost = MZERO
    movements = []
    for layer in layers:
        if remaining <= QZERO:
            break
        take = layer.qty_remaining if layer.qty_remaining <= remaining else remaining
        take = quantize(take, QTY_SCALE)
        whole_layer = take >= layer.qty_remaining
        layer.qty_remaining = quantize(layer.qty_remaining - take, QTY_SCALE)
        # Take the layer's whole remaining value when it is emptied, so no
        # rounding dust is left behind; otherwise cost the partial draw.
        if whole_layer:
            line_cost = layer.value_remaining
        else:
            line_cost = quantize(take * layer.unit_cost, MONEY_SCALE)
            if line_cost > layer.value_remaining:
                line_cost = layer.value_remaining
        layer.value_remaining = quantize(layer.value_remaining - line_cost, MONEY_SCALE)
        total_cost += line_cost
        remaining = quantize(remaining - take, QTY_SCALE)
        db.add(InventoryTransaction(
            company_id=company_id, item_type=itype, item_id=item_id, lot_id=layer.lot_id,
            layer_id=layer.id, warehouse_id=layer.warehouse_id, direction=MoveDirection.OUT.value,
            qty=take, unit_cost=layer.unit_cost, total_cost=line_cost, event_type=event_type,
            source_module=source_module, source_id=source_id, posting_reference=posting_reference,
        ))
        movements.append({
            "layer_id": str(layer.id), "lot_id": str(layer.lot_id) if layer.lot_id else None,
            "qty": take, "unit_cost": layer.unit_cost, "total_cost": line_cost,
        })
    db.flush()
    return {"total_cost": total_cost, "qty": qty, "movements": movements}


def balance(db: Session, *, company_id: uuid.UUID, item_type: ItemType, item_id: uuid.UUID,
            lot_id: uuid.UUID | None = None, warehouse_id: uuid.UUID | None = None) -> dict:
    itype = item_type.value if isinstance(item_type, ItemType) else item_type
    stmt = select(StockLayer).where(
        StockLayer.company_id == company_id, StockLayer.item_type == itype,
        StockLayer.item_id == item_id,
    )
    if lot_id is not None:
        stmt = stmt.where(StockLayer.lot_id == lot_id)
    if warehouse_id is not None:
        stmt = stmt.where(StockLayer.warehouse_id == warehouse_id)
    qty_on_hand = QZERO
    value = MZERO
    for layer in db.execute(stmt).scalars():
        qty_on_hand += layer.qty_remaining
        value += layer.value_remaining or MZERO
    return {"qty_on_hand": qty_on_hand, "value": value}


def weighted_average_cost(db: Session, *, company_id: uuid.UUID, item_type: ItemType,
                          item_id: uuid.UUID, lot_id: uuid.UUID | None = None) -> dict:
    """Weighted-average valuation of an item: total value / total quantity on
    hand. A valuation basis alongside the FIFO/FEFO cost layers."""
    bal = balance(db, company_id=company_id, item_type=item_type, item_id=item_id, lot_id=lot_id)
    qty = bal["qty_on_hand"]
    value = bal["value"]
    avg = quantize(value / qty, MONEY_SCALE) if qty > QZERO else MZERO
    return {"qty_on_hand": qty, "value": value, "avg_cost": avg}


def transfer_stock(db: Session, *, company_id: uuid.UUID, item_type: ItemType, item_id: uuid.UUID,
                   from_warehouse_id: uuid.UUID, to_warehouse_id: uuid.UUID, qty,
                   method: str = "FIFO", source_module: str | None = "transfer",
                   source_id: str | None = None) -> dict:
    """Move ``qty`` of an item between warehouses at the cost drawn from source.

    GL-neutral: the inventory control account is unchanged — only the warehouse
    dimension of the sub-ledger moves — so no voucher is posted."""
    if from_warehouse_id == to_warehouse_id:
        raise ValueError("Source and destination warehouses must differ")
    res = consume(db, company_id=company_id, item_type=item_type, item_id=item_id, qty=qty,
                  warehouse_id=from_warehouse_id, method=method, event_type="TRANSFER_OUT",
                  source_module=source_module, source_id=source_id)
    moved_qty = res["qty"]
    cost = res["total_cost"]
    unit = quantize(cost / moved_qty, MONEY_SCALE) if moved_qty > QZERO else MZERO
    receive(db, company_id=company_id, item_type=item_type, item_id=item_id, qty=moved_qty,
            unit_cost=unit, warehouse_id=to_warehouse_id, event_type="TRANSFER_IN",
            source_module=source_module, source_id=source_id)
    return {"qty": moved_qty, "cost_value": cost, "unit_cost": unit}
