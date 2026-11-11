"""Color Service dispensing models + the inbound integration event queue."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.types import Money, Quantity
from app.models.base import AuditMixin, Base


class DosingStatus(str, enum.Enum):
    OK = "OK"
    FAILED = "FAILED"
    REPEATED = "REPEATED"
    ADDITIONAL = "ADDITIONAL"  # correction / extra dosing


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DispensingDevice(AuditMixin, Base):
    __tablename__ = "dispensing_devices"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    code: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(120))
    device_type: Mapped[str] = mapped_column(String(40), default="COLOR_SERVICE")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class DispensingTransaction(AuditMixin, Base):
    """An immutable actual-dispensing record for one material into one bath.

    Uniqueness on (company, external_txn_id) is the first idempotency barrier
    against replayed/reconnected feeds; the consumption voucher's
    posting_reference is the second.
    """

    __tablename__ = "dispensing_transactions"
    __table_args__ = (
        UniqueConstraint("company_id", "external_txn_id", name="uq_dispensing_external_txn"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    # factory/branch owner (additive; nullable — backfill is approval-gated)
    factory_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True, index=True)
    bath_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dye_baths.id"), index=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("dispensing_devices.id"), nullable=True)
    chemical_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chemicals.id"), index=True)
    chemical_lot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chemical_lots.id"), nullable=True)
    external_txn_id: Mapped[str] = mapped_column(String(80), index=True)
    material_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_weight: Mapped[object] = mapped_column(Quantity, default=0)
    actual_weight: Mapped[object] = mapped_column(Quantity, default=0)
    unit: Mapped[str] = mapped_column(String(10), default="KG")
    dosing_status: Mapped[DosingStatus] = mapped_column(String(12), default=DosingStatus.OK)
    is_correction: Mapped[bool] = mapped_column(Boolean, default=False)
    operator: Mapped[str | None] = mapped_column(String(80), nullable=True)
    scale_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_now)
    # set once the accepted consumption has been costed & posted (idempotency)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    unit_cost: Mapped[object] = mapped_column(Money, default=0)
    consumption_cost: Mapped[object] = mapped_column(Money, default=0)
    posting_reference: Mapped[str | None] = mapped_column(String(120), index=True)


class DispensingCorrection(AuditMixin, Base):
    __tablename__ = "dispensing_corrections"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    bath_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dye_baths.id"), index=True)
    dispensing_txn_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dispensing_transactions.id"), nullable=True
    )
    chemical_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chemicals.id"))
    qty: Mapped[object] = mapped_column(Quantity, default=0)
    reason: Mapped[str] = mapped_column(String(200))
    operator: Mapped[str | None] = mapped_column(String(80), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


class IntegrationEvent(AuditMixin, Base):
    """Raw inbound/outbound integration payloads, persisted before processing
    so the pipeline is replay-safe and offline-tolerant."""

    __tablename__ = "integration_events"
    __table_args__ = (
        UniqueConstraint("company_id", "direction", "external_txn_id", name="uq_integration_event"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    direction: Mapped[str] = mapped_column(String(10), index=True)  # INBOUND / OUTBOUND
    external_txn_id: Mapped[str | None] = mapped_column(String(80), index=True)
    payload: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="PENDING")  # PENDING/PROCESSED/FAILED
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
