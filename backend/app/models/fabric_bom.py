"""Fabric Bill of Materials (BOM) — version-controlled, knit→dye→finish.

A :class:`FabricBom` is the *parent* definition of a finished fabric. Each
:class:`FabricBomVersion` is an immutable, approvable revision with effective
dates, so an old production order keeps the version it was created against even
after the BOM is revised. A version links the three stages while keeping each
stage separately identifiable:

* **Yarn BOM** — :class:`FabricBomYarn` rows (multiple components, blend %,
  feeder allocation, standard consumption, wastage, process loss).
* **Knitting process** — :class:`FabricBomKnitting` (machine type, gauge,
  diameter, stitch length, settings, expected output).
* **Dyeing recipe** — a link to an existing approved
  :class:`~app.models.masters.RecipeVersion` (reused, not duplicated).
* **Finishing** — :class:`FabricBomFinishing` steps (compacting, stentering…).

Standard costing (yarn + dyes/chemicals + processing + labour + overhead →
expected cost/kg) is rolled up and **snapshotted onto the version at approval**,
so historical versions retain the cost they were approved with.

Production integration lives in :class:`ProductionOrder` (which *stamps* the BOM
version), :class:`ProductionReservation` and :class:`ProductionConsumption`.
All tables are company-scoped and (where operational) factory-aware, so under
database-per-company they live in the acting company's database.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.types import Money, Quantity
from app.models.base import AuditMixin, Base


class BomStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"   # replaced by a newer approved version
    OBSOLETE = "OBSOLETE"       # retired; not usable for new orders


class ProductionStage(str, enum.Enum):
    KNITTING = "KNITTING"
    DYEING = "DYEING"
    FINISHING = "FINISHING"


class ProductionOrderStatus(str, enum.Enum):
    PLANNED = "PLANNED"
    RELEASED = "RELEASED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class ReservationStatus(str, enum.Enum):
    RESERVED = "RESERVED"
    PARTIALLY_ISSUED = "PARTIALLY_ISSUED"
    ISSUED = "ISSUED"
    RELEASED = "RELEASED"


# --- parent BOM -------------------------------------------------------------

class FabricBom(AuditMixin, Base):
    """The finished-fabric definition. Versions hold the actual spec/recipe."""

    __tablename__ = "fabric_boms"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_fabric_boms_company_code"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    code: Mapped[str] = mapped_column(String(40), index=True)         # unique per company
    name: Mapped[str] = mapped_column(String(160))
    fabric_type_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fabric_types.id"), nullable=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    shade_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shade_codes.id"), nullable=True)
    finished_quality: Mapped[str | None] = mapped_column(String(80), nullable=True)
    uom_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("units_of_measure.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    versions: Mapped[list["FabricBomVersion"]] = relationship(
        back_populates="bom", cascade="all, delete-orphan", order_by="FabricBomVersion.version_no"
    )


class FabricBomVersion(AuditMixin, Base):
    """A version-controlled revision of a fabric BOM.

    Only one APPROVED version is effective at a given date. Approving a newer
    version supersedes the previously-effective one (keeps the history chain via
    ``supersedes_id``). Costs are snapshotted at approval.
    """

    __tablename__ = "fabric_bom_versions"
    __table_args__ = (
        UniqueConstraint("bom_id", "version_no", name="uq_fabric_bom_versions_bom_versionno"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    bom_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fabric_boms.id", ondelete="CASCADE"), index=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[BomStatus] = mapped_column(String(12), default=BomStatus.DRAFT, index=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revision_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fabric_bom_versions.id"), nullable=True
    )

    # --- finished-fabric technical spec (versioned) ---
    construction: Mapped[str | None] = mapped_column(String(120), nullable=True)
    composition: Mapped[str | None] = mapped_column(String(160), nullable=True)
    yarn_count: Mapped[str | None] = mapped_column(String(60), nullable=True)
    gsm: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    width_cm: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    gauge: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    diameter_inch: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    stitch_length_mm: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    finished_gsm: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    finished_width_cm: Mapped[object | None] = mapped_column(Quantity, nullable=True)

    # --- dyeing recipe link (reuse existing versioned recipe) ---
    dye_recipe_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recipe_versions.id"), nullable=True
    )

    # --- conversion-cost standards (per kg finished fabric) ---
    std_knitting_cost: Mapped[object] = mapped_column(Money, default=0)
    std_dyeing_cost: Mapped[object] = mapped_column(Money, default=0)
    std_finishing_cost: Mapped[object] = mapped_column(Money, default=0)
    std_labour_cost: Mapped[object] = mapped_column(Money, default=0)
    std_overhead_cost: Mapped[object] = mapped_column(Money, default=0)

    # --- rolled-up standard cost snapshot (set at approval) ---
    std_yarn_cost: Mapped[object] = mapped_column(Money, default=0)
    std_dye_chem_cost: Mapped[object] = mapped_column(Money, default=0)
    std_total_cost_per_kg: Mapped[object] = mapped_column(Money, default=0)
    cost_rolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    bom: Mapped[FabricBom] = relationship(back_populates="versions")
    yarns: Mapped[list["FabricBomYarn"]] = relationship(
        back_populates="version", cascade="all, delete-orphan", order_by="FabricBomYarn.sequence"
    )
    knitting: Mapped["FabricBomKnitting | None"] = relationship(
        back_populates="version", cascade="all, delete-orphan", uselist=False
    )
    finishing_steps: Mapped[list["FabricBomFinishing"]] = relationship(
        back_populates="version", cascade="all, delete-orphan", order_by="FabricBomFinishing.sequence"
    )


class FabricBomYarn(AuditMixin, Base):
    """A yarn component of a fabric BOM version (blend)."""

    __tablename__ = "fabric_bom_yarns"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fabric_bom_versions.id", ondelete="CASCADE"), index=True
    )
    yarn_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("yarn_masters.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer, default=1)
    blend_percent: Mapped[object] = mapped_column(Quantity, default=0)        # % of blend
    feeder_allocation: Mapped[str | None] = mapped_column(String(60), nullable=True)  # e.g. "F1-F4" / "2 of 6"
    # kg of this yarn per kg of finished fabric at the yarn's blend share (net).
    std_consumption_per_kg: Mapped[object] = mapped_column(Quantity, default=0)
    wastage_percent: Mapped[object] = mapped_column(Quantity, default=0)
    process_loss_percent: Mapped[object] = mapped_column(Quantity, default=0)
    # Standard yarn price used for the BOM cost roll-up (version-controlled).
    std_unit_cost: Mapped[object] = mapped_column(Money, default=0)

    version: Mapped[FabricBomVersion] = relationship(back_populates="yarns")


class FabricBomKnitting(AuditMixin, Base):
    """Knitting process spec for a BOM version (one per version)."""

    __tablename__ = "fabric_bom_knitting"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fabric_bom_versions.id", ondelete="CASCADE"), unique=True, index=True
    )
    machine_type: Mapped[str | None] = mapped_column(String(12), nullable=True)   # masters.MachineType
    machine_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("machines.id"), nullable=True)
    gauge: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    diameter_inch: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    feeders: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stitch_length_mm: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    rpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    efficiency_percent: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    expected_output_kg_per_shift: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    settings: Mapped[str | None] = mapped_column(Text, nullable=True)

    version: Mapped[FabricBomVersion] = relationship(back_populates="knitting")


class FabricBomFinishing(AuditMixin, Base):
    """A finishing step of a BOM version (compacting, stentering, …)."""

    __tablename__ = "fabric_bom_finishing"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fabric_bom_versions.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, default=1)
    process_type: Mapped[str] = mapped_column(String(16), index=True)   # quality.FinishingProcess
    machine_type: Mapped[str | None] = mapped_column(String(12), nullable=True)
    target_gsm: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    target_width_cm: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    params: Mapped[str | None] = mapped_column(Text, nullable=True)
    std_cost_per_kg: Mapped[object] = mapped_column(Money, default=0)

    version: Mapped[FabricBomVersion] = relationship(back_populates="finishing_steps")


# --- production integration --------------------------------------------------

class ProductionOrder(AuditMixin, Base):
    """A production order that *stamps* the BOM version it was created against.

    ``bom_version_id`` never changes even if the BOM is later revised, so the
    order keeps its originally-approved version (historical preservation).
    """

    __tablename__ = "production_orders"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    factory_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True, index=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    bom_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fabric_bom_versions.id"), index=True)
    sales_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sales_orders.id"), nullable=True)
    shade_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shade_codes.id"), nullable=True)
    planned_qty_kg: Mapped[object] = mapped_column(Quantity, default=0)
    produced_qty_kg: Mapped[object] = mapped_column(Quantity, default=0)
    status: Mapped[ProductionOrderStatus] = mapped_column(String(12), default=ProductionOrderStatus.PLANNED, index=True)
    planned_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    planned_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # --- grey fabric actual cost (knitting) ---
    # Actual knitting conversion cost, recorded INDEPENDENTLY (never derived from
    # the standard). ``None`` means "not entered" → excluded from actual, flagged.
    actual_knitting_conversion_cost: Mapped[object | None] = mapped_column(Money, nullable=True)
    # Set once the grey fabric output has been valued into FABRIC inventory.
    grey_inventory_value: Mapped[object] = mapped_column(Money, default=0)
    grey_valued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    reservations: Mapped[list["ProductionReservation"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    consumptions: Mapped[list["ProductionConsumption"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class ProductionReservation(AuditMixin, Base):
    """A planned material requirement for an order, exploded from the BOM."""

    __tablename__ = "production_reservations"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    production_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("production_orders.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(12), index=True)       # ProductionStage
    item_type: Mapped[str] = mapped_column(String(10), index=True)   # inventory.ItemType (YARN/CHEMICAL)
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    planned_qty: Mapped[object] = mapped_column(Quantity, default=0)
    issued_qty: Mapped[object] = mapped_column(Quantity, default=0)
    std_unit_cost: Mapped[object] = mapped_column(Money, default=0)
    std_cost: Mapped[object] = mapped_column(Money, default=0)
    status: Mapped[ReservationStatus] = mapped_column(String(18), default=ReservationStatus.RESERVED, index=True)

    order: Mapped[ProductionOrder] = relationship(back_populates="reservations")


class ProductionConsumption(AuditMixin, Base):
    """Actual material consumption / output recorded against an order stage."""

    __tablename__ = "production_consumptions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    production_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("production_orders.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(12), index=True)       # ProductionStage
    item_type: Mapped[str] = mapped_column(String(10), index=True)   # inventory.ItemType
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    lot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    qty: Mapped[object] = mapped_column(Quantity, default=0)
    actual_cost: Mapped[object] = mapped_column(Money, default=0)
    wastage_qty: Mapped[object] = mapped_column(Quantity, default=0)
    rejection_qty: Mapped[object] = mapped_column(Quantity, default=0)
    is_reprocess: Mapped[bool] = mapped_column(Boolean, default=False)
    posting_reference: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)

    order: Mapped[ProductionOrder] = relationship(back_populates="consumptions")
