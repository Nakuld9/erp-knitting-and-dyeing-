"""Commercial documents: sales orders, purchase orders, GRNs, sales invoices."""
from __future__ import annotations

import enum
import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.types import Money, Quantity
from app.models.base import AuditMixin, Base


class DocStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    PARTIAL = "PARTIAL"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    POSTED = "POSTED"


# --- sales order ------------------------------------------------------------

class SalesOrder(AuditMixin, Base):
    __tablename__ = "sales_orders"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    order_date: Mapped[date] = mapped_column(Date)
    delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[DocStatus] = mapped_column(String(12), default=DocStatus.DRAFT)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    lines: Mapped[list["SalesOrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class SalesOrderLine(AuditMixin, Base):
    __tablename__ = "sales_order_lines"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales_orders.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    fabric_type_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fabric_types.id"), nullable=True)
    shade_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shade_codes.id"), nullable=True)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    gsm: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    width_cm: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    qty_kg: Mapped[object] = mapped_column(Quantity, default=0)
    qty_m: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    rate: Mapped[object] = mapped_column(Money, default=0)
    tax_code_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tax_codes.id"), nullable=True)

    order: Mapped[SalesOrder] = relationship(back_populates="lines")


# --- purchase order ---------------------------------------------------------

class PurchaseOrder(AuditMixin, Base):
    __tablename__ = "purchase_orders"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"), index=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    order_date: Mapped[date] = mapped_column(Date)
    status: Mapped[DocStatus] = mapped_column(String(12), default=DocStatus.DRAFT)

    lines: Mapped[list["PurchaseOrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class PurchaseOrderLine(AuditMixin, Base):
    __tablename__ = "purchase_order_lines"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    chemical_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chemicals.id"), nullable=True)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    qty: Mapped[object] = mapped_column(Quantity, default=0)
    rate: Mapped[object] = mapped_column(Money, default=0)
    tax_code_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tax_codes.id"), nullable=True)

    order: Mapped[PurchaseOrder] = relationship(back_populates="lines")


# --- goods receipt note -----------------------------------------------------

class GoodsReceiptNote(AuditMixin, Base):
    __tablename__ = "goods_receipt_notes"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"), index=True)
    purchase_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("purchase_orders.id"), nullable=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    grn_date: Mapped[date] = mapped_column(Date)
    supplier_invoice_no: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[DocStatus] = mapped_column(String(12), default=DocStatus.DRAFT)
    posting_reference: Mapped[str | None] = mapped_column(String(120), index=True)

    lines: Mapped[list["GRNLine"]] = relationship(back_populates="grn", cascade="all, delete-orphan")


class GRNLine(AuditMixin, Base):
    __tablename__ = "goods_receipt_lines"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    grn_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("goods_receipt_notes.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    chemical_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chemicals.id"), index=True)
    chemical_lot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chemical_lots.id"), nullable=True)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    # Matched to the ordered PO line, so receipts roll up against the order.
    purchase_order_line_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("purchase_order_lines.id"), nullable=True, index=True
    )
    qty: Mapped[object] = mapped_column(Quantity, default=0)
    unit_cost: Mapped[object] = mapped_column(Money, default=0)
    tax_code_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tax_codes.id"), nullable=True)

    grn: Mapped[GoodsReceiptNote] = relationship(back_populates="lines")


# --- sales invoice ----------------------------------------------------------

class SalesInvoice(AuditMixin, Base):
    __tablename__ = "sales_invoices"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    sales_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sales_orders.id"), nullable=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    invoice_date: Mapped[date] = mapped_column(Date)
    status: Mapped[DocStatus] = mapped_column(String(12), default=DocStatus.DRAFT)
    taxable_value: Mapped[object] = mapped_column(Money, default=0)
    tax_amount: Mapped[object] = mapped_column(Money, default=0)
    total_value: Mapped[object] = mapped_column(Money, default=0)
    posting_reference: Mapped[str | None] = mapped_column(String(120), index=True)

    lines: Mapped[list["SalesInvoiceLine"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )


class SalesInvoiceLine(AuditMixin, Base):
    __tablename__ = "sales_invoice_lines"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales_invoices.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    fabric_roll_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    qty: Mapped[object] = mapped_column(Quantity, default=0)
    rate: Mapped[object] = mapped_column(Money, default=0)
    amount: Mapped[object] = mapped_column(Money, default=0)
    tax_code_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tax_codes.id"), nullable=True)

    invoice: Mapped[SalesInvoice] = relationship(back_populates="lines")


# --- delivery note (dispatch challan) ---------------------------------------

class DeliveryNote(AuditMixin, Base):
    """Dispatch of finished rolls against a sales order.

    Posting a delivery note takes the fabric out of stock (FIFO, at actual
    sub-ledger cost) and books Cost of Goods Sold; the revenue invoice is a
    separate document that follows the dispatch.
    """
    __tablename__ = "delivery_notes"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    sales_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sales_orders.id"), nullable=True, index=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    delivery_date: Mapped[date] = mapped_column(Date)
    status: Mapped[DocStatus] = mapped_column(String(12), default=DocStatus.DRAFT)
    total_qty_kg: Mapped[object] = mapped_column(Quantity, default=0)
    cogs_value: Mapped[object] = mapped_column(Money, default=0)
    posting_reference: Mapped[str | None] = mapped_column(String(120), index=True)

    lines: Mapped[list["DeliveryNoteLine"]] = relationship(
        back_populates="delivery", cascade="all, delete-orphan"
    )


class DeliveryNoteLine(AuditMixin, Base):
    __tablename__ = "delivery_note_lines"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    delivery_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("delivery_notes.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    fabric_roll_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fabric_rolls.id"), index=True)
    qty_kg: Mapped[object] = mapped_column(Quantity, default=0)
    cogs_value: Mapped[object] = mapped_column(Money, default=0)

    delivery: Mapped[DeliveryNote] = relationship(back_populates="lines")


# --- sales credit note (customer: price adjustment / return value) ----------

class CreditNote(AuditMixin, Base):
    """Reduces a customer's receivable and reverses output GST (price
    adjustment, discount or short-shipment)."""
    __tablename__ = "credit_notes"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    original_invoice_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sales_invoices.id"), nullable=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    note_date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[DocStatus] = mapped_column(String(12), default=DocStatus.DRAFT)
    taxable_value: Mapped[object] = mapped_column(Money, default=0)
    tax_amount: Mapped[object] = mapped_column(Money, default=0)
    total_value: Mapped[object] = mapped_column(Money, default=0)
    posting_reference: Mapped[str | None] = mapped_column(String(120), index=True)

    lines: Mapped[list["CreditNoteLine"]] = relationship(
        back_populates="note", cascade="all, delete-orphan"
    )


class CreditNoteLine(AuditMixin, Base):
    __tablename__ = "credit_note_lines"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    note_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("credit_notes.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    qty: Mapped[object] = mapped_column(Quantity, default=0)
    rate: Mapped[object] = mapped_column(Money, default=0)
    amount: Mapped[object] = mapped_column(Money, default=0)
    tax_code_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tax_codes.id"), nullable=True)

    note: Mapped[CreditNote] = relationship(back_populates="lines")


# --- purchase debit note (supplier: goods return) ---------------------------

class DebitNote(AuditMixin, Base):
    """Returns chemicals to a supplier: reduces stock (FIFO), the supplier
    payable and the input GST originally claimed."""
    __tablename__ = "debit_notes"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"), index=True)
    original_grn_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("goods_receipt_notes.id"), nullable=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    note_date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[DocStatus] = mapped_column(String(12), default=DocStatus.DRAFT)
    taxable_value: Mapped[object] = mapped_column(Money, default=0)
    tax_amount: Mapped[object] = mapped_column(Money, default=0)
    total_value: Mapped[object] = mapped_column(Money, default=0)
    posting_reference: Mapped[str | None] = mapped_column(String(120), index=True)

    lines: Mapped[list["DebitNoteLine"]] = relationship(
        back_populates="note", cascade="all, delete-orphan"
    )


class DebitNoteLine(AuditMixin, Base):
    __tablename__ = "debit_note_lines"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    note_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("debit_notes.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    chemical_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chemicals.id"), index=True)
    chemical_lot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chemical_lots.id"), nullable=True)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    qty: Mapped[object] = mapped_column(Quantity, default=0)
    tax_code_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tax_codes.id"), nullable=True)
    # Stamped from the FIFO cost actually relieved, so GST ties to the ledger.
    cost_value: Mapped[object] = mapped_column(Money, default=0)

    note: Mapped[DebitNote] = relationship(back_populates="lines")
