"""Trading-fabric item master (Stage 4B-1, additive).

Trading fabric is purchased-from-outside fabric held for resale. It is a
SEPARATE identity from manufactured fabric: a trading SKU is never a
``FabricType`` used in production and never becomes a ``FabricRoll``, lot or
genealogy node. Its inventory is tracked in aggregate under
``ItemType.TRADING_FABRIC`` keyed by ``trading_fabric_items.id``.

This model only establishes the SKU master. Trading purchasing, GRN, stock
receipts, dispatch, returns and financial posting are deliberately NOT part of
this stage.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, String, Text, Uuid, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.types import Money, Quantity
from app.models.base import AuditMixin, Base


class TradingFabricItem(AuditMixin, Base):
    """A purchased-for-resale fabric SKU, owned by one company.

    ``fabric_type_id`` is an OPTIONAL reference to an existing manufactured
    ``FabricType`` so shared specifications need not be re-keyed; it never makes
    the trading SKU a manufactured item. SKU ``code`` is unique within a company.
    """

    __tablename__ = "trading_fabric_items"
    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_trading_item_company_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)

    code: Mapped[str] = mapped_column(String(40), index=True)   # trading SKU code
    name: Mapped[str] = mapped_column(String(160))

    # Optional link to an existing fabric specification (same company). Nullable;
    # when set it only shares construction/composition/GSM/width for display.
    fabric_type_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fabric_types.id"), nullable=True, index=True
    )

    # Trading-specific specification (used when no FabricType is referenced, or to
    # override it). All optional.
    construction: Mapped[str | None] = mapped_column(String(120), nullable=True)
    composition: Mapped[str | None] = mapped_column(String(120), nullable=True)
    gsm: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    width_cm: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    colour: Mapped[str | None] = mapped_column(String(60), nullable=True)

    uom_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("units_of_measure.id"), nullable=True)
    # GST/HSN is carried by the existing tax-code master (TaxCode.hsn_sac).
    tax_code_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tax_codes.id"), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# ===========================================================================
# Trading purchase order (Stage 4B-2)
# ===========================================================================

class TradingPOStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"       # authorised for receipt
    PARTIAL = "PARTIAL"         # some, not all, ordered qty received
    COMPLETED = "COMPLETED"     # fully received
    CANCELLED = "CANCELLED"     # cancelled before any receipt


class TradingPurchaseOrder(AuditMixin, Base):
    """A purchase order for trading fabric. Optional in the workflow — a GRN may
    be raised directly against a supplier invoice without a PO."""

    __tablename__ = "trading_purchase_orders"
    __table_args__ = (
        UniqueConstraint("company_id", "number", name="uq_trading_po_company_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"), index=True)
    factory_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True, index=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    order_date: Mapped[date] = mapped_column(Date)
    expected_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default=TradingPOStatus.DRAFT.value, index=True)
    remarks: Mapped[str | None] = mapped_column(String(300), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lines: Mapped[list["TradingPurchaseOrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan")


class TradingPurchaseOrderLine(AuditMixin, Base):
    __tablename__ = "trading_purchase_order_lines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trading_purchase_orders.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    trading_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trading_fabric_items.id"), index=True)
    qty_ordered: Mapped[object] = mapped_column(Quantity, default=0)
    qty_received: Mapped[object] = mapped_column(Quantity, default=0)     # cumulative across GRNs
    qty_cancelled: Mapped[object] = mapped_column(Quantity, default=0)
    uom_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("units_of_measure.id"), nullable=True)
    rate: Mapped[object] = mapped_column(Money, default=0)
    tax_code_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tax_codes.id"), nullable=True)

    order: Mapped[TradingPurchaseOrder] = relationship(back_populates="lines")


# ===========================================================================
# Trading goods receipt note (Stage 4B-2) — shared by PO-based and direct routes
# ===========================================================================

class TradingGRNStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"       # the ONLY state that has created stock + posted
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class TradingGRNSource(str, enum.Enum):
    PO = "PO"
    DIRECT = "DIRECT"


class TradingGRN(AuditMixin, Base):
    """A goods receipt for trading fabric. Aggregate-quantity based — never a
    FabricRoll, lot, batch, genealogy node or packing list.

    Supplier invoice numbers are unique per (company, supplier) to stop the same
    invoice being received twice. Only the APPROVED state creates inventory.
    """

    __tablename__ = "trading_goods_receipt_notes"
    __table_args__ = (
        UniqueConstraint("company_id", "number", name="uq_trading_grn_company_number"),
        UniqueConstraint("company_id", "supplier_id", "supplier_invoice_no",
                         name="uq_trading_grn_supplier_invoice"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"), index=True)
    factory_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True, index=True)
    purchase_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("trading_purchase_orders.id"), nullable=True, index=True)
    source_type: Mapped[str] = mapped_column(String(8), default=TradingGRNSource.DIRECT.value)

    number: Mapped[str] = mapped_column(String(40), index=True)
    grn_date: Mapped[date] = mapped_column(Date)
    # Supplier invoice is required for both routes (stored normalised, upper/trim).
    supplier_invoice_no: Mapped[str] = mapped_column(String(40), index=True)
    supplier_invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    status: Mapped[str] = mapped_column(String(12), default=TradingGRNStatus.DRAFT.value, index=True)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    posting_reference: Mapped[str | None] = mapped_column(String(120), index=True)

    # Document taxable/tax/total roll-ups (frozen at creation; not recomputed post-approval).
    taxable_value: Mapped[object] = mapped_column(Money, default=0)
    tax_amount: Mapped[object] = mapped_column(Money, default=0)
    total_value: Mapped[object] = mapped_column(Money, default=0)

    submitted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Over-receipt beyond PO outstanding (authorised server-side + reason).
    is_over_received: Mapped[bool] = mapped_column(Boolean, default=False)
    over_receive_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    over_received_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    over_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lines: Mapped[list["TradingGRNLine"]] = relationship(
        back_populates="grn", cascade="all, delete-orphan")


class TradingGRNLine(AuditMixin, Base):
    __tablename__ = "trading_goods_receipt_lines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    grn_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trading_goods_receipt_notes.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    trading_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trading_fabric_items.id"), index=True)
    # Matched PO line for PO-based receipts (null for direct).
    purchase_order_line_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("trading_purchase_order_lines.id"), nullable=True, index=True)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"), index=True)

    qty_received: Mapped[object] = mapped_column(Quantity, default=0)   # accepted into stock
    qty_rejected: Mapped[object] = mapped_column(Quantity, default=0)   # recorded, NOT stocked
    uom_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("units_of_measure.id"), nullable=True)
    rate: Mapped[object] = mapped_column(Money, default=0)              # unit purchase cost

    tax_code_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tax_codes.id"), nullable=True)
    taxable_value: Mapped[object] = mapped_column(Money, default=0)
    cgst: Mapped[object] = mapped_column(Money, default=0)
    sgst: Mapped[object] = mapped_column(Money, default=0)
    igst: Mapped[object] = mapped_column(Money, default=0)
    total_value: Mapped[object] = mapped_column(Money, default=0)
    remarks: Mapped[str | None] = mapped_column(String(200), nullable=True)

    grn: Mapped[TradingGRN] = relationship(back_populates="lines")


# ===========================================================================
# Trading sales order (Stage 4B-3A.1a)
# ===========================================================================

class TradingSOStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"       # authorised for future dispatch
    PARTIAL = "PARTIAL"         # set only by a future dispatch stage (not here)
    COMPLETED = "COMPLETED"     # set only by a future dispatch stage (not here)
    CANCELLED = "CANCELLED"


class TradingSalesOrder(AuditMixin, Base):
    """A customer sales order for trading fabric. Creating or approving it moves
    NO stock and posts NO accounting — it only records intent. Dispatch, COGS and
    invoicing are later, separately-approved stages.
    """

    __tablename__ = "trading_sales_orders"
    __table_args__ = (
        UniqueConstraint("company_id", "order_number", name="uq_trading_so_company_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    factory_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True, index=True)
    order_number: Mapped[str] = mapped_column(String(40), index=True)
    order_date: Mapped[date] = mapped_column(Date)
    expected_dispatch_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    customer_po_ref: Mapped[str | None] = mapped_column(String(60), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default=TradingSOStatus.DRAFT.value, index=True)
    remarks: Mapped[str | None] = mapped_column(String(300), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lines: Mapped[list["TradingSalesOrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan")


class TradingSalesOrderLine(AuditMixin, Base):
    __tablename__ = "trading_sales_order_lines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trading_sales_orders.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    trading_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trading_fabric_items.id"), index=True)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"), index=True)
    qty_ordered: Mapped[object] = mapped_column(Quantity, default=0)
    qty_dispatched: Mapped[object] = mapped_column(Quantity, default=0)   # advanced only by a future dispatch stage
    qty_cancelled: Mapped[object] = mapped_column(Quantity, default=0)
    uom_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("units_of_measure.id"), nullable=True)
    rate: Mapped[object] = mapped_column(Money, default=0)               # selling rate
    discount: Mapped[object] = mapped_column(Money, default=0)           # per-line discount amount
    tax_code_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tax_codes.id"), nullable=True)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)

    order: Mapped[TradingSalesOrder] = relationship(back_populates="lines")


# ===========================================================================
# Trading dispatch (Stage 4B-3A.1b.1 — draft lifecycle only; no stock/accounting)
# ===========================================================================

class TradingDispatchStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"       # terminal: stock consumed (FIFO) + balanced COGS voucher posted
    CANCELLED = "CANCELLED"


class TradingDispatch(AuditMixin, Base):
    """An aggregate-quantity dispatch of trading fabric against an approved
    TradingSalesOrder. In this stage it is a commercial document only: creating,
    submitting or cancelling it moves NO stock, posts NO accounting and does NOT
    change the sales order's dispatched quantity. It is never a FabricRoll, lot,
    genealogy node or packing list.
    """

    __tablename__ = "trading_dispatches"
    __table_args__ = (
        UniqueConstraint("company_id", "dispatch_number", name="uq_trading_dispatch_company_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    factory_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True, index=True)
    # Dispatch is always against an approved trading sales order (no direct dispatch).
    sales_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trading_sales_orders.id"), index=True)
    dispatch_number: Mapped[str] = mapped_column(String(40), index=True)
    dispatch_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default=TradingDispatchStatus.DRAFT.value, index=True)
    vehicle_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    transporter: Mapped[str | None] = mapped_column(String(120), nullable=True)
    customer_delivery_ref: Mapped[str | None] = mapped_column(String(60), nullable=True)
    remarks: Mapped[str | None] = mapped_column(String(300), nullable=True)
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Stage 4B-3A.1b.2 — set only on APPROVED (atomic FIFO consume + COGS voucher).
    cogs_value: Mapped[object] = mapped_column(Money, default=0)
    # No ``index=True`` here: the explicit index is created in migration 0026, and a
    # model-level auto-index breaks SQLite batch-downgrade table recreation.
    posting_reference: Mapped[str | None] = mapped_column(String(120))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Over-dispatch beyond the sales-order line outstanding (authorised server-side + reason).
    is_over_dispatched: Mapped[bool] = mapped_column(Boolean, default=False)
    over_dispatch_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    over_dispatched_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    over_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lines: Mapped[list["TradingDispatchLine"]] = relationship(
        back_populates="dispatch", cascade="all, delete-orphan")
    allocations: Mapped[list["TradingDispatchFifoAllocation"]] = relationship(
        back_populates="dispatch", cascade="all, delete-orphan")


class TradingDispatchLine(AuditMixin, Base):
    __tablename__ = "trading_dispatch_lines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    dispatch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trading_dispatches.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    trading_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trading_fabric_items.id"), index=True)
    # Always tied to a specific line of the referenced sales order.
    sales_order_line_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trading_sales_order_lines.id"), index=True)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"), index=True)
    quantity: Mapped[object] = mapped_column(Quantity, default=0)
    uom_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("units_of_measure.id"), nullable=True)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Actual FIFO cost of goods sold for this line, set only on APPROVED.
    cogs_value: Mapped[object] = mapped_column(Money, default=0)

    dispatch: Mapped[TradingDispatch] = relationship(back_populates="lines")


class TradingDispatchFifoAllocation(AuditMixin, Base):
    """Per-layer FIFO consumption evidence for an APPROVED trading dispatch, so an
    auditor can reconstruct: dispatch -> line -> stock layer -> receipt -> qty ->
    unit cost -> total cost -> COGS voucher. One row per consumed FIFO layer.
    Written only at approval and never mutated afterwards.
    """

    __tablename__ = "trading_dispatch_fifo_allocations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    dispatch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trading_dispatches.id", ondelete="CASCADE"), index=True)
    dispatch_line_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trading_dispatch_lines.id", ondelete="CASCADE"), index=True)
    stock_layer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inventory_valuation_layers.id"), index=True)
    source_lot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    quantity_consumed: Mapped[object] = mapped_column(Quantity, default=0)
    unit_cost: Mapped[object] = mapped_column(Money, default=0)
    total_cost: Mapped[object] = mapped_column(Money, default=0)

    dispatch: Mapped[TradingDispatch] = relationship(back_populates="allocations")
