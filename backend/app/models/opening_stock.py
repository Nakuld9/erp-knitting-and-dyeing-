"""Opening-stock lines + import files (Stage 6F-P2).

P2 adds the typed opening *lines* that hang off a P1 ``OpeningBatch`` for exactly
two categories — Yarn Store stock and Trading Fabric aggregate stock — plus the
uploaded import-file metadata (original bytes + hash) that produced them.

Design notes / guard-rails:

* One ``OpeningStockLine`` table carries both categories, discriminated by
  ``line_type`` (YARN / TRADE_FABRIC). It stores the opening quantity, UOM,
  optional weight, approved carrying unit cost and total value, effective date
  and source references, the per-row validation outcome, a deduplication key and
  the id of the FIFO ``StockLayer`` created when the batch is approved.
* Yarn lines additionally carry the existing-or-created ``YarnLot`` id, the
  internal lot number, the supplier + supplier lot reference (only when known)
  and an explicit legacy-lot classification. No supplier lot is ever fabricated.
* Trading lines are aggregate only: a ``TradingFabricItem`` id, quantity and
  value. They NEVER carry a roll id, lot id, batch id, genealogy or packing-list
  reference — those do not exist for trading fabric.
* No accounting fields: P2 creates FIFO stock only; no voucher is posted and no
  GL account is referenced here. Financial posting stays CA-gated for a later
  phase.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    Uuid,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.types import Money, Quantity
from app.models.base import AuditMixin, Base


class OpeningLineType(str, enum.Enum):
    """Which opening category an import belongs to. For P2 yarn/trading the stored
    value matches the inventory ``ItemType`` value so a line maps 1:1 onto the FIFO
    pool. ``MANUFACTURED_ROLLS`` (P3) is a template/import discriminator only — its
    rows live in ``opening_roll_lines`` and map to ``ItemType.FABRIC`` per roll."""

    YARN = "YARN"
    TRADING_FABRIC = "TRADE_FABRIC"
    MANUFACTURED_ROLLS = "MANUFACTURED_ROLLS"
    VENDOR_CUSTODY = "VENDOR_CUSTODY"


class OpeningLineValidation(str, enum.Enum):
    PENDING = "PENDING"       # parsed, not yet validated
    VALID = "VALID"           # passed every mandatory check; eligible to commit
    INVALID = "INVALID"       # failed one or more checks (see validation_errors)
    COMMITTED = "COMMITTED"   # stock layer created at batch approval


class OpeningLotPolicy(str, enum.Enum):
    """How a yarn line's lot identity was resolved (never silently invented)."""

    NONE = "NONE"                         # not a yarn line
    EXISTING_LOT = "EXISTING_LOT"         # matched an existing YarnLot (no stock yet)
    NEW_LOT = "NEW_LOT"                   # a new lot registered from legacy lot number
    LEGACY_PLACEHOLDER = "LEGACY_PLACEHOLDER"  # authorised placeholder for unknown legacy identity


# The template version the parser accepts. Bump when the column set changes.
OPENING_TEMPLATE_VERSION = "1"


class OpeningImportFile(AuditMixin, Base):
    """An uploaded opening-stock workbook. The original bytes and a SHA-256 hash
    are preserved so an import is auditable and a re-upload of the identical file
    can be detected. One row per successful upload into a DRAFT batch."""

    __tablename__ = "opening_import_files"
    __table_args__ = (
        # The same physical file cannot be imported into one batch twice.
        UniqueConstraint("batch_id", "file_hash", name="uq_opening_import_batch_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opening_batches.id", ondelete="CASCADE"), index=True)
    line_type: Mapped[str] = mapped_column(String(24), index=True)
    file_name: Mapped[str] = mapped_column(String(255))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    # Original uploaded bytes, preserved verbatim for audit.
    content: Mapped[bytes] = mapped_column(LargeBinary)
    template_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    valid_count: Mapped[int] = mapped_column(Integer, default=0)
    invalid_count: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OpeningStockLine(AuditMixin, Base):
    """A single opening-stock line (yarn lot or trading-fabric aggregate) under a
    DRAFT opening batch. Becomes a FIFO ``StockLayer`` only when the batch is
    approved; carries no accounting posting."""

    __tablename__ = "opening_stock_lines"
    __table_args__ = (
        # Deduplication within a batch, enforced only for VALID lines (invalid /
        # duplicate rows keep dedup_key NULL so they can still be persisted for the
        # rejection report). Partial unique index is created in the migration.
        UniqueConstraint("batch_id", "dedup_key", name="uq_opening_line_batch_dedup"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opening_batches.id", ondelete="CASCADE"), index=True)
    import_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("opening_import_files.id", ondelete="SET NULL"), nullable=True, index=True)
    line_type: Mapped[str] = mapped_column(String(24), index=True)
    row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    factory_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("warehouses.id"), nullable=True)
    # Polymorphic item reference (yarn_masters.id or trading_fabric_items.id). Not
    # a DB FK because it targets two tables; the service validates ownership.
    item_type: Mapped[str] = mapped_column(String(12), index=True)
    item_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    uom_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("units_of_measure.id"), nullable=True)

    quantity: Mapped[object] = mapped_column(Quantity, default=0)
    weight_kg: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    unit_cost: Mapped[object] = mapped_column(Money, default=0)
    total_value: Mapped[object] = mapped_column(Money, default=0)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_document: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # --- yarn-specific (NULL for trading) ---
    yarn_lot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    internal_lot_no: Mapped[str | None] = mapped_column(String(40), nullable=True)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("suppliers.id"), nullable=True)
    supplier_lot_no: Mapped[str | None] = mapped_column(String(40), nullable=True)
    lot_policy: Mapped[str | None] = mapped_column(String(20), nullable=True)
    legacy_unknown: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- validation + dedup ---
    validation_status: Mapped[str] = mapped_column(
        String(12), default=OpeningLineValidation.PENDING.value, index=True)
    validation_errors: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON [{field,reason}]
    # NULL for invalid / duplicate rows (so the partial unique index ignores them).
    dedup_key: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # --- commit tracking (set at approval) ---
    stock_layer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
