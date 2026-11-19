"""Direct yarn-sales models (Stage 5B).

Sell purchased yarn directly from the existing Yarn Store (ItemType.YARN FIFO
lots) without routing it through knitting production. These models mirror the
trading sales-order / dispatch / FIFO-allocation shape, but key on the yarn SKU
(``yarn_masters``) and preserve the ORIGINAL supplier yarn lot: dispatch consumes
existing ``YarnLot`` stock via the shared FIFO engine and records lot-level
evidence (``source_lot_id``). No new yarn lot is ever generated, and no
FabricRoll / roll ID / knitting WIP / greige / genealogy / packing list is
created. Yarn stock never uses the TRADING warehouse or TRADING_FABRIC item type.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Uuid, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.types import Money, Quantity
from app.models.base import AuditMixin, Base


# ===========================================================================
# Yarn sales order (records commercial intent only; no stock, no accounting)
# ===========================================================================

class YarnSOStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"       # authorised for future dispatch
    PARTIAL = "PARTIAL"         # advanced only by dispatch approval
    COMPLETED = "COMPLETED"     # advanced only by dispatch approval
    CANCELLED = "CANCELLED"


class YarnSalesOrder(AuditMixin, Base):
    """A customer sales order for yarn drawn from the Yarn Store. Creating or
    approving it moves NO stock, creates NO reservation and posts NO accounting."""

    __tablename__ = "yarn_sales_orders"
    __table_args__ = (
        UniqueConstraint("company_id", "order_number", name="uq_yarn_so_company_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    factory_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True, index=True)
    order_number: Mapped[str] = mapped_column(String(40), index=True)
    order_date: Mapped[date] = mapped_column(Date)
    expected_dispatch_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    customer_po_ref: Mapped[str | None] = mapped_column(String(60), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default=YarnSOStatus.DRAFT.value, index=True)
    remarks: Mapped[str | None] = mapped_column(String(300), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lines: Mapped[list["YarnSalesOrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan")


class YarnSalesOrderLine(AuditMixin, Base):
    __tablename__ = "yarn_sales_order_lines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("yarn_sales_orders.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    yarn_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("yarn_masters.id"), index=True)
    # Yarn Store warehouse to draw from. Nullable: historical yarn stock may be
    # uncategorised / warehouse-less. When set it must NOT be a TRADING warehouse.
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    qty_ordered: Mapped[object] = mapped_column(Quantity, default=0)
    qty_dispatched: Mapped[object] = mapped_column(Quantity, default=0)   # advanced only by dispatch approval
    qty_cancelled: Mapped[object] = mapped_column(Quantity, default=0)
    uom_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("units_of_measure.id"), nullable=True)
    rate: Mapped[object] = mapped_column(Money, default=0)                # selling rate
    discount: Mapped[object] = mapped_column(Money, default=0)
    tax_code_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tax_codes.id"), nullable=True)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)

    order: Mapped[YarnSalesOrder] = relationship(back_populates="lines")


# ===========================================================================
# Yarn sales dispatch (approval consumes FIFO yarn stock + posts COGS)
# ===========================================================================

class YarnDispatchStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"       # terminal: FIFO stock consumed + balanced COGS voucher posted
    CANCELLED = "CANCELLED"


class YarnSalesDispatch(AuditMixin, Base):
    """A dispatch of yarn against an approved YarnSalesOrder. Draft/submit/cancel
    move NO stock; APPROVAL consumes existing yarn FIFO lots and posts a balanced
    YARN_DELIVERY COGS voucher. Never a FabricRoll, lot generator or packing list.
    """

    __tablename__ = "yarn_sales_dispatches"
    __table_args__ = (
        UniqueConstraint("company_id", "dispatch_number", name="uq_yarn_dispatch_company_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    factory_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True, index=True)
    sales_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("yarn_sales_orders.id"), index=True)
    dispatch_number: Mapped[str] = mapped_column(String(40), index=True)
    dispatch_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default=YarnDispatchStatus.DRAFT.value, index=True)
    vehicle_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    transporter: Mapped[str | None] = mapped_column(String(120), nullable=True)
    customer_delivery_ref: Mapped[str | None] = mapped_column(String(60), nullable=True)
    remarks: Mapped[str | None] = mapped_column(String(300), nullable=True)
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set only on APPROVED (atomic FIFO consume + COGS voucher).
    cogs_value: Mapped[object] = mapped_column(Money, default=0)
    # No index=True here (explicit index created in the migration; a model-level
    # auto-index breaks SQLite batch-downgrade table recreation).
    posting_reference: Mapped[str | None] = mapped_column(String(120))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_over_dispatched: Mapped[bool] = mapped_column(Boolean, default=False)
    over_dispatch_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    over_dispatched_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    over_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lines: Mapped[list["YarnSalesDispatchLine"]] = relationship(
        back_populates="dispatch", cascade="all, delete-orphan")
    allocations: Mapped[list["YarnSalesDispatchFifoAllocation"]] = relationship(
        back_populates="dispatch", cascade="all, delete-orphan")


class YarnSalesDispatchLine(AuditMixin, Base):
    __tablename__ = "yarn_sales_dispatch_lines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    dispatch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("yarn_sales_dispatches.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    yarn_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("yarn_masters.id"), index=True)
    sales_order_line_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("yarn_sales_order_lines.id"), index=True)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    # Optional specific supplier lot to draw from. When set, ONLY that lot is
    # consumed (never silently substituted); when null, FIFO across the SKU's lots.
    lot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("yarn_lots.id"), nullable=True, index=True)
    quantity: Mapped[object] = mapped_column(Quantity, default=0)
    uom_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("units_of_measure.id"), nullable=True)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cogs_value: Mapped[object] = mapped_column(Money, default=0)          # actual FIFO cost, set on APPROVED
    qty_invoiced: Mapped[object] = mapped_column(Quantity, default=0)     # cumulative invoiced counter

    dispatch: Mapped[YarnSalesDispatch] = relationship(back_populates="lines")


class YarnSalesDispatchFifoAllocation(AuditMixin, Base):
    """Per-layer FIFO consumption evidence for an APPROVED yarn dispatch. Records
    the ORIGINAL supplier lot (``source_lot_id`` -> yarn_lots) consumed, so an
    auditor can reconstruct dispatch -> line -> stock layer -> yarn lot -> qty ->
    unit cost -> COGS voucher. Written only at approval; never mutated."""

    __tablename__ = "yarn_sales_dispatch_fifo_allocations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    dispatch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("yarn_sales_dispatches.id", ondelete="CASCADE"), index=True)
    dispatch_line_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("yarn_sales_dispatch_lines.id", ondelete="CASCADE"), index=True)
    stock_layer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inventory_valuation_layers.id"), index=True)
    source_lot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    quantity_consumed: Mapped[object] = mapped_column(Quantity, default=0)
    unit_cost: Mapped[object] = mapped_column(Money, default=0)
    total_cost: Mapped[object] = mapped_column(Money, default=0)

    dispatch: Mapped[YarnSalesDispatch] = relationship(back_populates="allocations")


# ===========================================================================
# Yarn dispatch -> invoice quantity allocation (mirrors the trading link)
# ===========================================================================

class YarnAllocationStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"       # counts against the dispatch line's uninvoiced quantity
    RELEASED = "RELEASED"   # the draft invoice was cancelled; evidence retained


class YarnDispatchInvoiceAllocation(AuditMixin, Base):
    """Quantity-level link between a yarn sales-invoice line and the dispatch line
    it bills. One row per invoice line. Only ACTIVE rows count against a dispatch
    line's uninvoiced quantity; cancelling a draft invoice RELEASES its rows."""

    __tablename__ = "yarn_dispatch_invoice_allocations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sales_invoices.id", ondelete="CASCADE"), index=True)
    invoice_line_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sales_invoice_lines.id", ondelete="CASCADE"), index=True)
    dispatch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("yarn_sales_dispatches.id"), index=True)
    dispatch_line_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("yarn_sales_dispatch_lines.id"), index=True)
    quantity_allocated: Mapped[object] = mapped_column(Quantity, default=0)
    uom_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("units_of_measure.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(10), default=YarnAllocationStatus.ACTIVE.value, index=True)
    released_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
