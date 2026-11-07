"""Manufacturing transactions: knitting, greige rolls, dye batches/baths, lab dips."""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, Table, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.types import Money, Quantity
from app.models.base import AuditMixin, Base


class RollKind(str, enum.Enum):
    GREIGE = "GREIGE"
    FINISHED = "FINISHED"


class BatchStatus(str, enum.Enum):
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    REPROCESS = "REPROCESS"
    CANCELLED = "CANCELLED"


# rolls <-> dye batch (a batch dyes many rolls; a roll keeps its batch link)
dye_batch_rolls = Table(
    "dye_batch_rolls",
    Base.metadata,
    Column("batch_id", Uuid, ForeignKey("dye_batches.id", ondelete="CASCADE"), primary_key=True),
    Column("roll_id", Uuid, ForeignKey("fabric_rolls.id", ondelete="CASCADE"), primary_key=True),
)


class KnittingProduction(AuditMixin, Base):
    __tablename__ = "knitting_production"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    machine_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("machines.id"), index=True)
    sales_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sales_orders.id"), nullable=True)
    yarn_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("yarn_masters.id"), index=True)
    yarn_lot: Mapped[str | None] = mapped_column(String(40), nullable=True)
    yarn_lot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("yarn_lots.id"), nullable=True, index=True)
    fabric_type_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fabric_types.id"), nullable=True)
    production_date: Mapped[date] = mapped_column(Date, index=True)
    shift: Mapped[str | None] = mapped_column(String(20), nullable=True)
    operator_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    produced_kg: Mapped[object] = mapped_column(Quantity, default=0)
    yarn_consumed_kg: Mapped[object] = mapped_column(Quantity, default=0)
    wastage_kg: Mapped[object] = mapped_column(Quantity, default=0)
    rejection_kg: Mapped[object] = mapped_column(Quantity, default=0)
    run_time_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    downtime_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    posting_reference: Mapped[str | None] = mapped_column(String(120), index=True)

    rolls: Mapped[list["FabricRoll"]] = relationship(back_populates="knitting_production")


class FabricRoll(AuditMixin, Base):
    __tablename__ = "fabric_rolls"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    roll_no: Mapped[str] = mapped_column(String(40), index=True)
    barcode: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    kind: Mapped[RollKind] = mapped_column(String(10), default=RollKind.GREIGE)
    knitting_production_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knitting_production.id"), nullable=True, index=True
    )
    fabric_type_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fabric_types.id"), nullable=True)
    sales_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sales_orders.id"), nullable=True)
    dye_batch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("dye_batches.id"), nullable=True)
    weight_kg: Mapped[object] = mapped_column(Quantity, default=0)
    length_m: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    gsm: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    width_cm: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    quality_status: Mapped[str] = mapped_column(String(20), default="OK")
    # roll lineage (split/merge): points to the parent roll it came from.
    parent_roll_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fabric_rolls.id"), nullable=True)

    knitting_production: Mapped[KnittingProduction | None] = relationship(back_populates="rolls")


class DyeBatch(AuditMixin, Base):
    __tablename__ = "dye_batches"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    batch_code: Mapped[str] = mapped_column(String(40), index=True)   # e.g. DY-26001
    sales_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sales_orders.id"), nullable=True)
    shade_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shade_codes.id"), nullable=True)
    fabric_kg: Mapped[object] = mapped_column(Quantity, default=0)
    status: Mapped[BatchStatus] = mapped_column(String(12), default=BatchStatus.PLANNED)
    planned_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    baths: Mapped[list["DyeBath"]] = relationship(back_populates="batch", cascade="all, delete-orphan")
    rolls: Mapped[list[FabricRoll]] = relationship(secondary=dye_batch_rolls)


class DyeBath(AuditMixin, Base):
    __tablename__ = "dye_baths"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dye_batches.id", ondelete="CASCADE"), index=True)
    bath_code: Mapped[str] = mapped_column(String(50), index=True)   # e.g. DY-26001-B01
    machine_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("machines.id"))
    recipe_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recipe_versions.id"), nullable=True)
    fabric_kg: Mapped[object] = mapped_column(Quantity, default=0)
    liquor_ratio: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    water_litres: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    operator_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    status: Mapped[BatchStatus] = mapped_column(String(12), default=BatchStatus.PLANNED)
    actual_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    batch: Mapped[DyeBatch] = relationship(back_populates="baths")


class LabDip(AuditMixin, Base):
    __tablename__ = "lab_dips"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    shade_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shade_codes.id"), nullable=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    recipe_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recipe_versions.id"), nullable=True)
    delta_e: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    l_star: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    a_star: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    b_star: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")  # DRAFT/APPROVED/REJECTED
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
