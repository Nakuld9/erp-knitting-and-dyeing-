"""Finishing and quality models (brief §12).

Finishing transactions (hydro/slitting/drying/stenter/compacting/raising/heat
setting), quality inspections (Delta E, shade match, colour fastness ->
accept/reject/rework), and customer complaints.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.types import Quantity
from app.models.base import AuditMixin, Base


class FinishingProcess(str, enum.Enum):
    HYDRO = "HYDRO"
    SLITTING = "SLITTING"
    DRYING = "DRYING"
    STENTER = "STENTER"
    COMPACTING = "COMPACTING"
    RAISING = "RAISING"
    HEAT_SETTING = "HEAT_SETTING"


class InspectionResult(str, enum.Enum):
    ACCEPT = "ACCEPT"
    REWORK = "REWORK"
    REJECT = "REJECT"


class ComplaintStatus(str, enum.Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


# Processes that finish the fabric (mark the roll FINISHED when done).
FINISHING_PROCESSES = {FinishingProcess.COMPACTING, FinishingProcess.HEAT_SETTING, FinishingProcess.RAISING}


class FinishingTransaction(AuditMixin, Base):
    __tablename__ = "finishing_transactions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    # factory/branch owner (additive; nullable — backfill is approval-gated)
    factory_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True, index=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    process_type: Mapped[FinishingProcess] = mapped_column(String(16), index=True)
    # BOM-driven production order this finishing step belongs to (additive; nullable).
    production_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("production_orders.id"), nullable=True, index=True)
    fabric_roll_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fabric_rolls.id"), nullable=True)
    dye_batch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("dye_batches.id"), nullable=True)
    machine_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("machines.id"), nullable=True)
    process_date: Mapped[date] = mapped_column(Date, index=True)
    input_weight_kg: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    output_weight_kg: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    width_cm: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    gsm: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    shrinkage_pct: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    operator_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)


class QualityInspection(AuditMixin, Base):
    __tablename__ = "quality_inspections"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    # factory/branch owner (additive; nullable — backfill is approval-gated)
    factory_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True, index=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    fabric_roll_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fabric_rolls.id"), nullable=True)
    dye_batch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("dye_batches.id"), nullable=True)
    inspection_date: Mapped[date] = mapped_column(Date, index=True)
    inspector_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    delta_e: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    l_star: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    a_star: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    b_star: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    shade_match: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    colour_fastness: Mapped[str | None] = mapped_column(String(20), nullable=True)   # e.g. "4-5"
    result: Mapped[InspectionResult] = mapped_column(String(10), index=True)
    inspected_qty: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    rejection_qty: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)


class CustomerComplaint(AuditMixin, Base):
    __tablename__ = "customer_complaints"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    reference: Mapped[str | None] = mapped_column(String(80), nullable=True)   # invoice / roll ref
    complaint_date: Mapped[date] = mapped_column(Date, index=True)
    severity: Mapped[str] = mapped_column(String(10), default="MEDIUM")        # LOW/MEDIUM/HIGH
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[ComplaintStatus] = mapped_column(String(10), default=ComplaintStatus.OPEN, index=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
