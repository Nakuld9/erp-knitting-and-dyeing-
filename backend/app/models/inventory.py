"""Perpetual inventory models with FIFO valuation layers.

An inventory *item* is polymorphic: a chemical/dye, a yarn, or a fabric roll,
identified by (``item_type``, ``item_id``). Receipts open a ``StockLayer`` with a
remaining quantity and unit cost; issues/consumption draw down layers FIFO and
record the costed movement in ``InventoryTransaction``. Every movement can carry
the ``posting_reference`` of the accounting voucher it generated, so the stock
sub-ledger and the general ledger reconcile (accounting rule #11).
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from datetime import date

from sqlalchemy import Date, DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.types import Money, Quantity
from app.models.base import AuditMixin, Base


class ItemType(str, enum.Enum):
    CHEMICAL = "CHEMICAL"   # chemicals and dyes (chemicals.id)
    YARN = "YARN"           # yarn_masters.id
    FABRIC = "FABRIC"       # fabric_rolls.id (greige/finished)


class MoveDirection(str, enum.Enum):
    IN = "IN"
    OUT = "OUT"
    TRANSFER = "TRANSFER"
    ADJUST = "ADJUST"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StockLayer(AuditMixin, Base):
    """A FIFO cost layer created by a receipt; consumed oldest-first."""

    __tablename__ = "inventory_valuation_layers"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    item_type: Mapped[ItemType] = mapped_column(String(12), index=True)
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    lot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    qty_in: Mapped[object] = mapped_column(Quantity, default=0)
    qty_remaining: Mapped[object] = mapped_column(Quantity, default=0, index=True)
    unit_cost: Mapped[object] = mapped_column(Money, default=0)
    # Remaining *value* is tracked explicitly (received value minus costs taken
    # out), so the sub-ledger ties to the GL to the paisa despite rounding.
    value_remaining: Mapped[object] = mapped_column(Money, default=0)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    # Earliest-expiry-first consumption (FEFO) orders by this; null = no expiry.
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    source_module: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(80), nullable=True)


class StockTransfer(AuditMixin, Base):
    """A stock move between two warehouses (same item, GL-neutral)."""
    __tablename__ = "stock_transfers"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    transfer_date: Mapped[date] = mapped_column(Date)
    item_type: Mapped[ItemType] = mapped_column(String(12))
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    from_warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"))
    to_warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"))
    qty: Mapped[object] = mapped_column(Quantity, default=0)
    cost_value: Mapped[object] = mapped_column(Money, default=0)


class StockCount(AuditMixin, Base):
    """A physical stock count: the counted quantity against the system book
    quantity, with the costed variance that was posted."""
    __tablename__ = "stock_counts"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    count_date: Mapped[date] = mapped_column(Date)
    item_type: Mapped[ItemType] = mapped_column(String(12))
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    book_qty: Mapped[object] = mapped_column(Quantity, default=0)
    counted_qty: Mapped[object] = mapped_column(Quantity, default=0)
    variance_qty: Mapped[object] = mapped_column(Quantity, default=0)
    variance_value: Mapped[object] = mapped_column(Money, default=0)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    posting_reference: Mapped[str | None] = mapped_column(String(120), index=True)


class InventoryTransaction(AuditMixin, Base):
    """A single costed stock movement (one layer consumed/created)."""

    __tablename__ = "inventory_transactions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    item_type: Mapped[ItemType] = mapped_column(String(12), index=True)
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    lot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    layer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inventory_valuation_layers.id"), nullable=True)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    direction: Mapped[MoveDirection] = mapped_column(String(10), index=True)
    qty: Mapped[object] = mapped_column(Quantity, default=0)
    unit_cost: Mapped[object] = mapped_column(Money, default=0)
    total_cost: Mapped[object] = mapped_column(Money, default=0)
    event_type: Mapped[str] = mapped_column(String(50), index=True)   # GRN / CONSUME / ISSUE / PRODUCE ...
    source_module: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    posting_reference: Mapped[str | None] = mapped_column(String(120), index=True)
    moved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
