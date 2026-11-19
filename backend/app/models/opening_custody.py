"""Opening vendor-custody & vendor-held-WIP lines (Stage 6F-P4).

Records company-owned goods physically held by an outside job worker / processor
on the cutover date — yarn (with its lot) or a manufactured fabric roll — under a
P1 ``OpeningBatch`` of type ``VENDOR_CUSTODY`` or ``WIP``.

An opening custody line is NOT a new physical issue. At approval it:
* creates exactly one FIFO ``StockLayer`` in the vendor's VENDOR_CUSTODY warehouse
  (company-owned storage located at the vendor), with ``event_type="OPENING"`` and
  NO accounting voucher;
* records one opening-origin ``JobWorkIssue`` (status OUTSTANDING,
  ``posting_reference="OPENING:<batch>:<row>"``, ``source_warehouse_id=NULL``) so
  the position shows in the existing vendor-wise outstanding reconciliation
  exactly once — without fabricating a transfer, a receipt or production history;
* for a genuinely new roll, records one ``IMPORT`` genealogy origin edge.

Line kinds: VENDOR_CUSTODY (material simply held) and VENDOR_HELD_WIP (held and
mid-process at the vendor). Custody types: YARN and FABRIC_ROLL. In-house
non-roll WIP, company/vendor in-transit and awaiting-acceptance stock are out of
scope and are rejected at validation — never silently mapped to vendor custody.
No roll value is stored on ``FabricRoll``; the stock layer is authoritative.
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

OPENING_CUSTODY_TEMPLATE_VERSION = "1"
OPENING_CUSTODY_LINE_TYPE = "VENDOR_CUSTODY"


class OpeningCustodyKind(str, enum.Enum):
    VENDOR_CUSTODY = "VENDOR_CUSTODY"         # company goods simply held by a vendor
    VENDOR_HELD_WIP = "VENDOR_HELD_WIP"       # held AND mid outsourced process


class OpeningCustodyType(str, enum.Enum):
    YARN = "YARN"
    FABRIC_ROLL = "FABRIC_ROLL"


class OpeningCustodyRegistration(str, enum.Enum):
    REFERENCE_EXISTING = "REFERENCE_EXISTING"   # existing FabricRoll / YarnLot reused
    MINT_NEW = "MINT_NEW"                        # new roll id / new lot registered at approval


class OpeningCustodyLine(AuditMixin, Base):
    __tablename__ = "opening_custody_lines"
    __table_args__ = (
        UniqueConstraint("batch_id", "dedup_key", name="uq_opening_custody_batch_dedup"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opening_batches.id", ondelete="CASCADE"), index=True)
    import_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("opening_import_files.id", ondelete="SET NULL"), nullable=True, index=True)
    row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    line_kind: Mapped[str] = mapped_column(String(20), index=True)   # OpeningCustodyKind
    custody_type: Mapped[str] = mapped_column(String(12), index=True)  # OpeningCustodyType
    registration_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # --- vendor & process ---
    factory_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True)
    job_worker_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("suppliers.id"), nullable=True, index=True)
    process: Mapped[str | None] = mapped_column(String(12), nullable=True)
    source_jobwork_ref: Mapped[str | None] = mapped_column(String(60), nullable=True)
    prior_vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("suppliers.id"), nullable=True)
    source_document: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # --- roll identity (FABRIC_ROLL) ---
    erp_roll_no: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    legacy_label: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    roll_kind: Mapped[str | None] = mapped_column(String(10), nullable=True)
    origin_stream: Mapped[str | None] = mapped_column(String(10), nullable=True)
    fabric_roll_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    fabric_type_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fabric_types.id"), nullable=True)
    structure: Mapped[str | None] = mapped_column(String(120), nullable=True)
    gsm: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    width_cm: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    colour: Mapped[str | None] = mapped_column(String(60), nullable=True)
    shade_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shade_codes.id"), nullable=True)
    dye_lot: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # --- yarn identity (YARN) ---
    yarn_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("yarn_masters.id"), nullable=True, index=True)
    internal_lot_no: Mapped[str | None] = mapped_column(String(40), nullable=True)
    yarn_lot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)

    # --- quantity & valuation ---
    quantity: Mapped[object] = mapped_column(Quantity, default=0)
    net_weight_kg: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    uom_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("units_of_measure.id"), nullable=True)
    length_m: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    unit_cost: Mapped[object] = mapped_column(Money, default=0)
    total_value: Mapped[object] = mapped_column(Money, default=0)
    valuation_source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    disputed_unknown: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- pipeline + commit refs ---
    validation_status: Mapped[str] = mapped_column(String(12), default="PENDING", index=True)
    validation_errors: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedup_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    custody_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    stock_layer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    job_work_issue_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
