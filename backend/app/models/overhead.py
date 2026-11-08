"""Overhead cost pools and their allocation to work centres.

An overhead pool is a bucket of indirect cost (electricity, steam, maintenance,
depreciation, …) that is absorbed into production. An allocation run spreads a
pool's amount across the work centres (machines) by a configurable basis and
posts the distribution to the ledger: Dr WIP (tagged with each work centre's
cost centre) / Cr the pool's source expense account.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.types import Money, Quantity
from app.models.base import AuditMixin, Base


class AllocationBasis(str, enum.Enum):
    OUTPUT_KG = "OUTPUT_KG"        # production kg per machine
    MACHINE_HOURS = "MACHINE_HOURS"  # run time per machine
    EQUAL = "EQUAL"               # equal split across active work centres


class OverheadPool(AuditMixin, Base):
    __tablename__ = "overhead_pools"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    code: Mapped[str] = mapped_column(String(30), index=True)
    name: Mapped[str] = mapped_column(String(120))
    # The expense account the overhead sits in and is absorbed from.
    source_account_code: Mapped[str] = mapped_column(String(10))
    basis: Mapped[AllocationBasis] = mapped_column(String(16), default=AllocationBasis.OUTPUT_KG)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class OverheadAllocationRun(AuditMixin, Base):
    __tablename__ = "overhead_allocation_runs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    pool_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("overhead_pools.id"), index=True)
    period_month: Mapped[date] = mapped_column(Date, index=True)
    run_date: Mapped[date] = mapped_column(Date)
    basis: Mapped[AllocationBasis] = mapped_column(String(16))
    total_amount: Mapped[object] = mapped_column(Money, default=0)
    posting_reference: Mapped[str | None] = mapped_column(String(120), index=True)

    lines: Mapped[list["OverheadAllocationLine"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class OverheadAllocationLine(AuditMixin, Base):
    __tablename__ = "overhead_allocation_lines"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("overhead_allocation_runs.id", ondelete="CASCADE"), index=True)
    machine_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("machines.id"), nullable=True)
    cost_centre_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cost_centres.id"), nullable=True)
    basis_qty: Mapped[object] = mapped_column(Quantity, default=0)
    amount: Mapped[object] = mapped_column(Money, default=0)

    run: Mapped[OverheadAllocationRun] = relationship(back_populates="lines")
