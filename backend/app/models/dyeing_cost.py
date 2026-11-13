"""Dyeing operational + cost tracking — WITHOUT chemical-BOM automation.

This is a deliberately chemical-free layer: it tracks grey fabric going into
dyeing, the dyed output, process loss / rejection / reprocessing, and the actual
costs (internal dyeing charges or outside job-work, plus optional chemical /
finishing / testing / other costs entered *manually when available*). It does
**not** explode chemical recipes or reserve chemicals — the existing dye recipe
and chemical masters are untouched and remain available for later.

Traceability: a :class:`DyeingJob` links back to the grey
:class:`~app.models.fabric_bom.ProductionOrder` (and, through its inputs, to the
grey FABRIC inventory layers → yarn lots), so dyed fabric can be traced to its
grey production order, yarn lots and inventory issues.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.types import Money, Quantity
from app.models.base import AuditMixin, Base


class DyeingMode(str, enum.Enum):
    INTERNAL = "INTERNAL"   # dyed in-house; cost = internal dyeing charge
    JOBWORK = "JOBWORK"     # sent outside; cost = job-work charge billed to us


class DyeingStatus(str, enum.Enum):
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class DyeingJob(AuditMixin, Base):
    """A dyeing job: grey fabric in → dyed fabric out, with actual costs."""

    __tablename__ = "dyeing_jobs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    factory_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True, index=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    # traceability / references
    grey_production_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("production_orders.id"), nullable=True, index=True)
    sales_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sales_orders.id"), nullable=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    shade_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shade_codes.id"), nullable=True)
    dye_lot: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    mode: Mapped[DyeingMode] = mapped_column(String(10), default=DyeingMode.INTERNAL)
    jobwork_supplier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("suppliers.id"), nullable=True)
    dye_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # quantities (kg)
    input_qty_kg: Mapped[object] = mapped_column(Quantity, default=0)       # grey sent (Σ inputs)
    output_qty_kg: Mapped[object] = mapped_column(Quantity, default=0)      # saleable dyed out (Σ outputs)
    process_loss_kg: Mapped[object] = mapped_column(Quantity, default=0)
    rejection_kg: Mapped[object] = mapped_column(Quantity, default=0)
    reprocess_qty_kg: Mapped[object] = mapped_column(Quantity, default=0)

    # actual costs (each nullable → "not recorded"; never defaulted from standard)
    grey_cost_consumed: Mapped[object] = mapped_column(Money, default=0)    # Σ input FIFO cost (genuine)
    dyeing_charge: Mapped[object | None] = mapped_column(Money, nullable=True)   # internal processing
    jobwork_charge: Mapped[object | None] = mapped_column(Money, nullable=True)  # outside job-work
    chemical_cost: Mapped[object | None] = mapped_column(Money, nullable=True)   # manual, when available
    finishing_cost: Mapped[object | None] = mapped_column(Money, nullable=True)
    testing_cost: Mapped[object | None] = mapped_column(Money, nullable=True)
    other_cost: Mapped[object | None] = mapped_column(Money, nullable=True)
    reprocess_cost: Mapped[object | None] = mapped_column(Money, nullable=True)

    dyed_inventory_value: Mapped[object] = mapped_column(Money, default=0)  # value of dyed output received
    status: Mapped[DyeingStatus] = mapped_column(String(12), default=DyeingStatus.PLANNED, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    inputs: Mapped[list["DyeingInput"]] = relationship(
        back_populates="job", cascade="all, delete-orphan")
    outputs: Mapped[list["DyeingOutput"]] = relationship(
        back_populates="job", cascade="all, delete-orphan")


class DyeingInput(AuditMixin, Base):
    """A grey-fabric issue into a dyeing job (FIFO-consumed from FABRIC stock)."""

    __tablename__ = "dyeing_inputs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dyeing_jobs.id", ondelete="CASCADE"), index=True)
    # grey fabric inventory item consumed (ItemType.FABRIC: a production order's
    # grey lot, or a specific roll) + its source grey production order.
    item_type: Mapped[str] = mapped_column(String(10), default="FABRIC")
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    source_production_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("production_orders.id"), nullable=True, index=True)
    qty_kg: Mapped[object] = mapped_column(Quantity, default=0)
    cost: Mapped[object] = mapped_column(Money, default=0)   # FIFO draw cost (genuine)
    posting_reference: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)

    job: Mapped[DyeingJob] = relationship(back_populates="inputs")


class DyeingOutput(AuditMixin, Base):
    """A saleable dyed-fabric output lot, with its allocated actual cost."""

    __tablename__ = "dyeing_outputs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dyeing_jobs.id", ondelete="CASCADE"), index=True)
    lot_code: Mapped[str] = mapped_column(String(40), index=True)
    shade_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shade_codes.id"), nullable=True)
    qty_kg: Mapped[object] = mapped_column(Quantity, default=0)
    allocated_cost: Mapped[object] = mapped_column(Money, default=0)   # share of total accumulated cost
    # the dyed FABRIC inventory item this lot was received as (for traceability).
    dyed_item_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    is_allocated: Mapped[bool] = mapped_column(Boolean, default=False)

    job: Mapped[DyeingJob] = relationship(back_populates="outputs")
