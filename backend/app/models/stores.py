"""Store items — consumables like knitting needles, machine spares, packing aids.

Low-value items that are bought into a store, then issued to a machine / section
and expensed on issue. Valuation rides on the same FIFO layers as other stock
(ItemType.STORE), so store items also appear in the stock-on-hand position.
"""
from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.types import Quantity
from app.models.base import AuditMixin, Base


class StoreCategory(str, enum.Enum):
    NEEDLE = "NEEDLE"
    SPARE = "SPARE"
    PACKING = "PACKING"
    OTHER = "OTHER"


class StoreItem(AuditMixin, Base):
    __tablename__ = "store_items"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    code: Mapped[str] = mapped_column(String(30), index=True)
    name: Mapped[str] = mapped_column(String(160))
    category: Mapped[StoreCategory] = mapped_column(String(10), default=StoreCategory.OTHER)
    uom: Mapped[str] = mapped_column(String(16), default="PCS")
    reorder_level: Mapped[object] = mapped_column(Quantity, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
