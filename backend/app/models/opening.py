"""Opening-balance batch foundation (Stage 6F-P1).

A controlled container for recording a company's opening position when it first
starts using the ERP (opening yarn stock, manufactured rolls, trading fabric,
vendor custody, WIP, customer / supplier balances, GL balances, fixed assets).

P1 is the FOUNDATION ONLY: the batch master, its controlled status lifecycle,
maker-checker approval, permissions and audit trail. A batch in P1 carries NO
opening quantities, values or line items; posts NO accounting voucher; creates
NO stock layer / FabricRoll / YarnLot / vendor-custody record; and moves NO
inventory. Those belong to the later line-item phases (P2-P5) and are
deliberately absent here. There is NO POSTED status in P1 — a batch's lifecycle
ends at CLOSED (or CANCELLED before approval).
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, String, Uuid, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AuditMixin, Base


class OpeningBatchType(str, enum.Enum):
    """The class of opening position a batch will carry (via P2+ line items).

    The enum fixes the vocabulary now; no line-item tables exist in P1.
    """

    YARN = "YARN"
    MANUFACTURED_ROLLS = "MANUFACTURED_ROLLS"
    TRADING_FABRIC = "TRADING_FABRIC"
    VENDOR_CUSTODY = "VENDOR_CUSTODY"
    WIP = "WIP"
    CUSTOMER_BALANCES = "CUSTOMER_BALANCES"
    SUPPLIER_BALANCES = "SUPPLIER_BALANCES"
    GL_BALANCES = "GL_BALANCES"
    FIXED_ASSETS = "FIXED_ASSETS"


class OpeningBatchStatus(str, enum.Enum):
    """Controlled lifecycle.

    DRAFT -> SUBMITTED -> APPROVED -> CLOSED, with CANCELLED reachable only
    before approval (from DRAFT or SUBMITTED). There is deliberately NO POSTED
    state in P1: no opening transaction is ever created or posted here.
    """

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


# Convenience sets used by the service and tests.
OPENING_BATCH_TYPES = frozenset(t.value for t in OpeningBatchType)
OPENING_TERMINAL_STATUSES = frozenset(
    {OpeningBatchStatus.CLOSED.value, OpeningBatchStatus.CANCELLED.value}
)


class OpeningBatch(AuditMixin, Base):
    """A company-scoped opening-balance batch (foundation only).

    Holds batch identity, type, effective opening date, descriptive metadata, an
    optional source-document reference, the controlled status and the
    maker-checker trail. It stores NO financial or stock quantities and NO line
    items; those arrive in later phases. ``AuditMixin`` supplies the row-level
    created/updated by+at trail; ``updated_by``/``updated_at`` are the
    "last updated by" fields.
    """

    __tablename__ = "opening_batches"
    __table_args__ = (
        UniqueConstraint("company_id", "batch_number", name="uq_opening_batch_company_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    # Nullable: a batch may be company-wide (e.g. GL, customer or supplier
    # balances) or scoped to a single factory (physical stock).
    factory_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True)
    # Company-scoped, gap-free document number (minted by the service).
    batch_number: Mapped[str] = mapped_column(String(40), index=True)
    batch_type: Mapped[str] = mapped_column(String(24), index=True)
    effective_date: Mapped[date] = mapped_column(Date, index=True)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    source_document: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(
        String(12), default=OpeningBatchStatus.DRAFT.value, index=True)
    # Caller-supplied idempotency token for safe create retries. Unique per
    # company (enforced by a partial unique index in the migration). No
    # index=True here: the migration owns the index, because a model-level
    # auto-index breaks SQLite batch-downgrade table recreation (same reason as
    # the yarn-dispatch posting_reference column).
    idempotency_key: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # Maker-checker / lifecycle trail. All identities are server-resolved by the
    # caller; the client never supplies them.
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
