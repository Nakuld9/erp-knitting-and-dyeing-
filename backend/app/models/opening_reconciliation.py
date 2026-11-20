"""Opening reconciliation foundation (Stage 6F-P6).

Persistent, company-scoped, factory-aware reconciliation runs and discrepancy
records that consolidate the existing P1-P5 reconciliation primitives
(``opening_stock``/``opening_rolls``/``opening_custody``/``opening_financial``
previews, ``stock_reconciliation``, ``trial_balance`` and the GST/ITC module) into
an auditable opening-cutover reconciliation.

A reconciliation run is **read/compute only** — it NEVER posts a voucher or creates
a stock layer / inventory transaction / roll / lot. It records only its own run,
status, discrepancy and audit rows. Discrepancies are append-only and history is
preserved across re-runs; a stable ``discrepancy_key`` links a discrepancy to its
earlier occurrences and resolution.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.types import Money
from app.models.base import AuditMixin, Base


class ReconType(str, enum.Enum):
    INVENTORY = "INVENTORY"
    CUSTOMER_AR = "CUSTOMER_AR"
    SUPPLIER_AP = "SUPPLIER_AP"
    BANK_CASH = "BANK_CASH"
    FIXED_ASSET = "FIXED_ASSET"
    GST_STATUTORY = "GST_STATUTORY"
    TRIAL_BALANCE = "TRIAL_BALANCE"
    CONSOLIDATED = "CONSOLIDATED"


class ReconStatus(str, enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    DISCREPANCY_FOUND = "DISCREPANCY_FOUND"
    UNDER_REVIEW = "UNDER_REVIEW"
    RESOLVED = "RESOLVED"
    CA_APPROVAL_PENDING = "CA_APPROVAL_PENDING"
    READY_FOR_CUTOVER = "READY_FOR_CUTOVER"


class DiscrepancyStatus(str, enum.Enum):
    UNRESOLVED = "UNRESOLVED"
    UNDER_REVIEW = "UNDER_REVIEW"
    RESOLVED = "RESOLVED"
    REOPENED = "REOPENED"


# Permitted status transitions (enforced by the service).
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    ReconStatus.NOT_STARTED.value: {ReconStatus.IN_PROGRESS.value},
    ReconStatus.IN_PROGRESS.value: {
        ReconStatus.RECONCILIATION_REQUIRED.value, ReconStatus.DISCREPANCY_FOUND.value,
        ReconStatus.RESOLVED.value, ReconStatus.UNDER_REVIEW.value},
    ReconStatus.RECONCILIATION_REQUIRED.value: {
        ReconStatus.IN_PROGRESS.value, ReconStatus.DISCREPANCY_FOUND.value,
        ReconStatus.UNDER_REVIEW.value, ReconStatus.RESOLVED.value},
    ReconStatus.DISCREPANCY_FOUND.value: {
        ReconStatus.UNDER_REVIEW.value, ReconStatus.RESOLVED.value,
        ReconStatus.IN_PROGRESS.value},
    ReconStatus.UNDER_REVIEW.value: {
        ReconStatus.RESOLVED.value, ReconStatus.DISCREPANCY_FOUND.value,
        ReconStatus.IN_PROGRESS.value},
    ReconStatus.RESOLVED.value: {
        ReconStatus.CA_APPROVAL_PENDING.value, ReconStatus.READY_FOR_CUTOVER.value,
        ReconStatus.IN_PROGRESS.value},
    ReconStatus.CA_APPROVAL_PENDING.value: {
        ReconStatus.READY_FOR_CUTOVER.value, ReconStatus.UNDER_REVIEW.value,
        ReconStatus.IN_PROGRESS.value},
    ReconStatus.READY_FOR_CUTOVER.value: {ReconStatus.IN_PROGRESS.value},
}


class OpeningReconciliationRun(AuditMixin, Base):
    __tablename__ = "opening_reconciliation_runs"
    __table_args__ = (
        UniqueConstraint("company_id", "recon_type", "run_number",
                         name="uq_openrecon_run_number"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    factory_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True)
    recon_type: Mapped[str] = mapped_column(String(20), index=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    run_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(24), default=ReconStatus.NOT_STARTED.value, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    initiated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)        # JSON
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    discrepancy_count: Mapped[int] = mapped_column(Integer, default=0)
    incomplete: Mapped[bool] = mapped_column(default=False)                 # missing mapping, etc.


class OpeningDiscrepancy(AuditMixin, Base):
    __tablename__ = "opening_discrepancies"
    __table_args__ = (
        UniqueConstraint("run_id", "discrepancy_key", name="uq_opendisc_run_key"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    factory_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opening_reconciliation_runs.id", ondelete="CASCADE"), index=True)
    recon_category: Mapped[str] = mapped_column(String(24), index=True)
    account_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    inventory_category: Mapped[str | None] = mapped_column(String(60), nullable=True)
    source_batch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Stable across runs for the same company+category+account/inventory+batch, so a
    # discrepancy can be traced to its earlier occurrences and resolution.
    discrepancy_key: Mapped[str] = mapped_column(String(200), index=True)
    expected_value: Mapped[object] = mapped_column(Money, default=0)
    actual_value: Mapped[object] = mapped_column(Money, default=0)
    difference: Mapped[object] = mapped_column(Money, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    qty_difference: Mapped[object | None] = mapped_column(Numeric(20, 6), nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    responsible_user: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    resolution_status: Mapped[str] = mapped_column(
        String(16), default=DiscrepancyStatus.UNRESOLVED.value, index=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    supporting_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
