"""Opening manufactured-fabric-roll lines (Stage 6F-P3).

P3 registers physical manufactured fabric rolls present at ERP cutover — in the
Greige, Semi-Finished and Finished Goods warehouses — under a P1 ``OpeningBatch``
of type ``MANUFACTURED_ROLLS``. Each line is EITHER a reference to an existing
``FabricRoll`` (no second roll created) OR the registration of a genuinely
unregistered physical roll (a new Roll ID is minted, only at approval, by the
existing authorised generator).

Design notes / guard-rails:

* One ``OpeningRollLine`` per physical roll; at approval it becomes exactly one
  FIFO ``StockLayer`` (``ItemType.FABRIC``, ``item_id = FabricRoll.id``), one
  ``InventoryTransaction`` (``event_type="OPENING"``) and — for a genuinely new
  roll — one ``IMPORT`` genealogy origin edge. No accounting voucher is posted.
* Roll carrying value is NEVER stored on ``FabricRoll``; the stock layer remains
  the authoritative valuation record. The line keeps the approved unit cost /
  total value / valuation source only as the opening instruction + audit record.
* No production, machine, operator, shift, yarn, supplier-lot, knitting, dyeing
  or finishing history is ever fabricated.
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
    String,
    Text,
    Uuid,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.types import Money, Quantity
from app.models.base import AuditMixin, Base

# The opening template version/type the parser accepts for manufactured rolls.
OPENING_ROLL_TEMPLATE_VERSION = "1"
OPENING_ROLL_LINE_TYPE = "MANUFACTURED_ROLLS"


class OpeningRollRegistration(str, enum.Enum):
    """How a roll line resolves its identity (resolved during validation)."""

    REFERENCE_EXISTING = "REFERENCE_EXISTING"   # an existing ERP FabricRoll is reused
    MINT_NEW = "MINT_NEW"                       # a new Roll ID is minted at approval


class OpeningRollKind(str, enum.Enum):
    """Physical nature / stage of the roll. Determines the required warehouse
    category and the authorised Roll-ID stream for a newly minted roll."""

    GREIGE = "GREIGE"
    DYED = "DYED"
    FINISHED = "FINISHED"


class OpeningRollLine(AuditMixin, Base):
    __tablename__ = "opening_roll_lines"
    __table_args__ = (
        # Batch-scoped dedup of VALID lines; invalid/duplicate rows keep dedup_key
        # NULL (NULLs are distinct), so they persist for the rejection report.
        UniqueConstraint("batch_id", "dedup_key", name="uq_opening_roll_batch_dedup"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opening_batches.id", ondelete="CASCADE"), index=True)
    import_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("opening_import_files.id", ondelete="SET NULL"), nullable=True, index=True)
    row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- identity ---
    erp_roll_no: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    legacy_label: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    registration_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    roll_kind: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # For a MINT_NEW FINISHED roll whose identity stream is otherwise ambiguous:
    # the verified origin stream (GREIGE / DYED). Never guessed.
    origin_stream: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Resolved existing or newly-created FabricRoll id (set at commit).
    fabric_roll_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)

    # --- placement ---
    factory_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("warehouses.id"), nullable=True)
    warehouse_category: Mapped[str | None] = mapped_column(String(20), nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # --- fabric specification ---
    fabric_type_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fabric_types.id"), nullable=True)
    structure: Mapped[str | None] = mapped_column(String(120), nullable=True)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    gsm: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    width_cm: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    colour: Mapped[str | None] = mapped_column(String(60), nullable=True)
    shade_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shade_codes.id"), nullable=True)
    dye_lot: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # --- physical quantity (kg is the manufacturing sales unit; length optional) ---
    net_weight_kg: Mapped[object] = mapped_column(Quantity, default=0)
    uom_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("units_of_measure.id"), nullable=True)
    length_m: Mapped[object | None] = mapped_column(Quantity, nullable=True)

    # --- quality & provenance (never fabricated) ---
    quality_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    job_ref: Mapped[str | None] = mapped_column(String(60), nullable=True)
    processing_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_document: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    physical_verification_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    disputed_unknown: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- valuation (sub-ledger authoritative; no value on FabricRoll) ---
    unit_cost: Mapped[object] = mapped_column(Money, default=0)
    total_value: Mapped[object] = mapped_column(Money, default=0)
    valuation_source: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # --- pipeline & audit ---
    validation_status: Mapped[str] = mapped_column(String(12), default="PENDING", index=True)
    validation_errors: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON [{field,reason}]
    dedup_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    stock_layer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
