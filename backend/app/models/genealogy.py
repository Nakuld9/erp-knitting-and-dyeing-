"""Manufacturing roll genealogy (traceability layer).

An additive, append-only edge table that records how physical fabric rolls are
produced from, or transformed into, other rolls as they move through the plant
(knitting -> greige, dyeing -> dyed, finishing -> finished goods, partial
processing, rejection, scrap, correction/reversal).

Design notes
------------
* This is a **traceability layer only**. It never creates inventory movements,
  FIFO layers or GL entries; the inventory ledger and posting engine remain the
  sole authorities for stock and accounting (Stage-2 requirement 8).
* ``parent_roll_id`` on :class:`FabricRoll` is left untouched and is NOT
  reinterpreted; this table is the richer, forward-looking record.
* Weight (kg) and length (m) are kept in **separate** fields and are never
  treated as interchangeable (Stage-2 requirement 7).
* Rows are append-only. A mistake is corrected by recording a REVERSAL/CORRECTION
  edge that references the original via ``correction_of_id`` — the original is
  preserved, never overwritten.
* Edges produced by one physical operation share an ``event_group_id`` so a
  many-source / many-output operation reads back as a single event.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.types import Quantity
from app.models.base import AuditMixin, Base


class RollGenealogyEdge(AuditMixin, Base):
    """One directed source-roll -> output-roll traceability edge.

    For an origin event (e.g. knitting, where the input is yarn, not a roll)
    ``source_roll_id`` is NULL. For a pure consumption/rejection event with no
    surviving output ``output_roll_id`` may be NULL. At least one of the two is
    always set (enforced by the service, not the database, so historical/import
    rows can never be rejected by a check constraint).
    """

    __tablename__ = "roll_genealogy_edges"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    # factory/branch where the event happened (nullable; historical rows stay NULL)
    factory_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True, index=True)
    # links every edge created by one physical operation (many:many within a batch)
    event_group_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)

    source_roll_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fabric_rolls.id"), nullable=True, index=True)
    output_roll_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fabric_rolls.id"), nullable=True, index=True)

    # KNIT / DYE / FINISH / REWORK / REJECT / SCRAP / CORRECTION / IMPORT
    process_type: Mapped[str] = mapped_column(String(20), index=True)
    # PRODUCE / CONSUME / PARTIAL / REJECT / SCRAP / LOSS / REVERSAL
    event_type: Mapped[str] = mapped_column(String(20), default="PRODUCE")
    # production order / job-work / knitting or dyeing document reference
    production_ref: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)

    # Quantities — weight (kg) and length (m) are DISTINCT and never converted.
    uom: Mapped[str | None] = mapped_column(String(10), nullable=True)
    input_qty: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    output_qty: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    input_weight_kg: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    output_weight_kg: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    input_length_m: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    output_length_m: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    process_loss: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    rejected_qty: Mapped[object | None] = mapped_column(Quantity, nullable=True)

    event_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    audit_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # reversal/correction link back to the edge being corrected (same table)
    correction_of_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("roll_genealogy_edges.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
