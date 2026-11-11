"""E-way bill records for sales invoices (movement of goods).

An e-way bill is required for a consignment above the threshold (₹50,000). It is
generated from the invoice plus transport details, and — when the invoice already
has an IRN — can be linked to it. The EWB number, validity and the GSP payloads
are stored verbatim for audit and reprint; a cancelled EWB keeps its history.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AuditMixin, Base


class EWayBillStatus(str, enum.Enum):
    PENDING = "PENDING"
    GENERATED = "GENERATED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class EWayBill(AuditMixin, Base):
    __tablename__ = "eway_bills"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sales_invoices.id"), unique=True, index=True)
    status: Mapped[EWayBillStatus] = mapped_column(
        String(10), default=EWayBillStatus.PENDING, index=True)

    ewb_no: Mapped[str | None] = mapped_column(String(20), index=True)
    ewb_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    valid_until: Mapped[str | None] = mapped_column(String(32), nullable=True)
    irn: Mapped[str | None] = mapped_column(String(64), nullable=True)   # linked IRN, if any

    # transport details supplied at generation
    trans_mode: Mapped[str | None] = mapped_column(String(2), nullable=True)   # 1 Road 2 Rail 3 Air 4 Ship
    vehicle_no: Mapped[str | None] = mapped_column(String(20), nullable=True)
    transporter_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    distance_km: Mapped[int | None] = mapped_column(Integer, nullable=True)

    gsp: Mapped[str | None] = mapped_column(String(40), nullable=True)
    request_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
