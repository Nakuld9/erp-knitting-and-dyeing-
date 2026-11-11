"""Same-company factory-to-factory stock transfer with goods-in-transit.

A stateful transfer document (REQUESTED -> AUTHORIZED -> DISPATCHED -> RECEIVED)
between warehouses of two *different factories of the same company*. It is
GL-neutral: it moves the warehouse/factory dimension of the inventory sub-ledger
and posts NO sales/purchase invoice (business rule: factory transfers must not
create artificial inter-factory invoices). Quantity shortage and quality
rejection are recorded and flagged for reconciliation, never silently written off.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.types import Money, Quantity
from app.models.base import AuditMixin, Base
from app.models.inventory import ItemType


class TransferStatus(str, enum.Enum):
    REQUESTED = "REQUESTED"
    AUTHORIZED = "AUTHORIZED"
    DISPATCHED = "DISPATCHED"   # consumed from source; goods in transit
    RECEIVED = "RECEIVED"       # received at destination (less any shortage/rejection)
    CANCELLED = "CANCELLED"


class FactoryStockTransfer(AuditMixin, Base):
    __tablename__ = "factory_stock_transfers"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    transfer_date: Mapped[date] = mapped_column(Date, index=True)

    from_branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), index=True)
    to_branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), index=True)
    from_warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"))
    to_warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"))

    item_type: Mapped[ItemType] = mapped_column(String(12))
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    requested_qty: Mapped[object] = mapped_column(Quantity, default=0)

    status: Mapped[TransferStatus] = mapped_column(String(12), default=TransferStatus.REQUESTED, index=True)

    dispatched_qty: Mapped[object] = mapped_column(Quantity, default=0)
    dispatched_cost: Mapped[object] = mapped_column(Money, default=0)
    unit_cost: Mapped[object] = mapped_column(Money, default=0)
    received_qty: Mapped[object] = mapped_column(Quantity, default=0)
    shortage_qty: Mapped[object] = mapped_column(Quantity, default=0)
    rejected_qty: Mapped[object] = mapped_column(Quantity, default=0)
    # True while any shortage/rejection awaits reconciliation (not auto-posted).
    needs_reconciliation: Mapped[bool] = mapped_column(default=False)

    requested_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    authorized_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    dispatched_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    received_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
