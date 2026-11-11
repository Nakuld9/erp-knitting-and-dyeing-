"""Fixed-asset register and depreciation runs.

An asset carries its capitalised cost, the GL account the cost sits in, and a
depreciation method (straight-line or written-down value). A depreciation run
computes one period's charge for every active asset and posts a single
balanced DEP voucher (Dr Depreciation expense / Cr Accumulated depreciation),
keeping each asset's accumulated depreciation and book value in step with the
ledger.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.types import Money, Quantity
from app.models.base import AuditMixin, Base


class DepMethod(str, enum.Enum):
    SLM = "SLM"   # straight-line
    WDV = "WDV"   # written-down value (reducing balance)


class AssetStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DISPOSED = "DISPOSED"


class FixedAsset(AuditMixin, Base):
    __tablename__ = "fixed_assets"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    code: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # GL account the capitalised cost is carried in (e.g. 1531 Knitting Machines).
    asset_account_code: Mapped[str] = mapped_column(String(10))
    acquisition_date: Mapped[date] = mapped_column(Date)
    capitalized_cost: Mapped[object] = mapped_column(Money, default=0)
    salvage_value: Mapped[object] = mapped_column(Money, default=0)
    method: Mapped[DepMethod] = mapped_column(String(3), default=DepMethod.SLM)
    useful_life_months: Mapped[int | None] = mapped_column(Integer, nullable=True)   # SLM
    dep_rate_pct: Mapped[object | None] = mapped_column(Quantity, nullable=True)     # WDV annual %
    accumulated_depreciation: Mapped[object] = mapped_column(Money, default=0)
    status: Mapped[AssetStatus] = mapped_column(String(12), default=AssetStatus.ACTIVE)
    disposal_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    posting_reference: Mapped[str | None] = mapped_column(String(120), index=True)


class DepreciationRun(AuditMixin, Base):
    __tablename__ = "depreciation_runs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    period_month: Mapped[date] = mapped_column(Date, index=True)   # first day of the charged month
    run_date: Mapped[date] = mapped_column(Date)
    total_amount: Mapped[object] = mapped_column(Money, default=0)
    posting_reference: Mapped[str | None] = mapped_column(String(120), index=True)

    entries: Mapped[list["DepreciationEntry"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class DepreciationEntry(AuditMixin, Base):
    __tablename__ = "depreciation_entries"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("depreciation_runs.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fixed_assets.id"), index=True)
    amount: Mapped[object] = mapped_column(Money, default=0)
    book_value_after: Mapped[object] = mapped_column(Money, default=0)

    run: Mapped[DepreciationRun] = relationship(back_populates="entries")
