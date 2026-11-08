"""E-invoice (IRN) records for sales invoices.

Each sales invoice gets at most one e-invoice. The IRN, acknowledgement and the
signed QR returned by the IRP (via a GSP) are stored verbatim so the document is
reproducible and auditable, and the request/response payloads are kept for
dispute resolution. A cancelled IRN keeps its history — nothing is deleted.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AuditMixin, Base


class EInvoiceStatus(str, enum.Enum):
    PENDING = "PENDING"
    GENERATED = "GENERATED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class EInvoice(AuditMixin, Base):
    __tablename__ = "einvoices"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sales_invoices.id"), unique=True, index=True)
    status: Mapped[EInvoiceStatus] = mapped_column(
        String(10), default=EInvoiceStatus.PENDING, index=True)

    irn: Mapped[str | None] = mapped_column(String(64), index=True)
    ack_no: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ack_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    signed_invoice: Mapped[str | None] = mapped_column(Text, nullable=True)
    signed_qr: Mapped[str | None] = mapped_column(Text, nullable=True)
    ewb_no: Mapped[str | None] = mapped_column(String(20), nullable=True)

    gsp: Mapped[str | None] = mapped_column(String(40), nullable=True)
    request_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
