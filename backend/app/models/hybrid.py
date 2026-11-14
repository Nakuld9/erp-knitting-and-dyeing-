"""Hybrid-manufacturing foundation models (Stage 1, additive).

Company-scoped configuration for whether each manufacturing process may run
internally and/or be outsourced to a job worker. One row per company (like
``CompanyBranding``); lives in the company operational database and never leaks
across companies. Defaults preserve current behaviour: internal ON, outsourced OFF.

The warehouse-category enum here labels the inventory stage a physical warehouse
belongs to (``Warehouse.category``); it is separate from the physical warehouse row
and from the legacy ``Warehouse.kind`` string, which is left untouched.
"""
from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, Uuid, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AuditMixin, Base


class Process(str, enum.Enum):
    KNITTING = "KNITTING"
    DYEING = "DYEING"
    FINISHING = "FINISHING"


class WorkMode(str, enum.Enum):
    INTERNAL = "INTERNAL"
    OUTSOURCED = "OUTSOURCED"


class WarehouseCategory(str, enum.Enum):
    """Inventory stage a warehouse belongs to. The four manufacturing stages plus a
    separate Trading stream (purchased-for-resale, aggregate-only)."""
    YARN_STORE = "YARN_STORE"
    GREIGE = "GREIGE"
    SEMI_FINISHED = "SEMI_FINISHED"
    FINISHED_GOODS = "FINISHED_GOODS"
    TRADING = "TRADING"


class ProcessMode(AuditMixin, Base):
    """Per-company hybrid-manufacturing configuration.

    Six independent toggles (3 processes x internal/outsourced). Defaults: every
    internal process ON, every outsourced process OFF — so creating this row changes
    nothing about how the company currently operates.
    """

    __tablename__ = "process_modes"
    __table_args__ = (UniqueConstraint("company_id", name="uq_process_mode_company"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, index=True)

    knit_internal: Mapped[bool] = mapped_column(Boolean, default=True)
    knit_outsourced: Mapped[bool] = mapped_column(Boolean, default=False)
    dye_internal: Mapped[bool] = mapped_column(Boolean, default=True)
    dye_outsourced: Mapped[bool] = mapped_column(Boolean, default=False)
    finish_internal: Mapped[bool] = mapped_column(Boolean, default=True)
    finish_outsourced: Mapped[bool] = mapped_column(Boolean, default=False)

    # column name for a given (process, mode) toggle
    @staticmethod
    def column_for(process: Process, mode: WorkMode) -> str:
        p = {"KNITTING": "knit", "DYEING": "dye", "FINISHING": "finish"}[Process(process).value]
        m = "internal" if WorkMode(mode) == WorkMode.INTERNAL else "outsourced"
        return f"{p}_{m}"

    def as_dict(self) -> dict:
        return {
            "knit_internal": self.knit_internal, "knit_outsourced": self.knit_outsourced,
            "dye_internal": self.dye_internal, "dye_outsourced": self.dye_outsourced,
            "finish_internal": self.finish_internal, "finish_outsourced": self.finish_outsourced,
        }
