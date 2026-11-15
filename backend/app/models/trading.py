"""Trading-fabric item master (Stage 4B-1, additive).

Trading fabric is purchased-from-outside fabric held for resale. It is a
SEPARATE identity from manufactured fabric: a trading SKU is never a
``FabricType`` used in production and never becomes a ``FabricRoll``, lot or
genealogy node. Its inventory is tracked in aggregate under
``ItemType.TRADING_FABRIC`` keyed by ``trading_fabric_items.id``.

This model only establishes the SKU master. Trading purchasing, GRN, stock
receipts, dispatch, returns and financial posting are deliberately NOT part of
this stage.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String, Uuid, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.types import Quantity
from app.models.base import AuditMixin, Base


class TradingFabricItem(AuditMixin, Base):
    """A purchased-for-resale fabric SKU, owned by one company.

    ``fabric_type_id`` is an OPTIONAL reference to an existing manufactured
    ``FabricType`` so shared specifications need not be re-keyed; it never makes
    the trading SKU a manufactured item. SKU ``code`` is unique within a company.
    """

    __tablename__ = "trading_fabric_items"
    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_trading_item_company_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)

    code: Mapped[str] = mapped_column(String(40), index=True)   # trading SKU code
    name: Mapped[str] = mapped_column(String(160))

    # Optional link to an existing fabric specification (same company). Nullable;
    # when set it only shares construction/composition/GSM/width for display.
    fabric_type_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fabric_types.id"), nullable=True, index=True
    )

    # Trading-specific specification (used when no FabricType is referenced, or to
    # override it). All optional.
    construction: Mapped[str | None] = mapped_column(String(120), nullable=True)
    composition: Mapped[str | None] = mapped_column(String(120), nullable=True)
    gsm: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    width_cm: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    colour: Mapped[str | None] = mapped_column(String(60), nullable=True)

    uom_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("units_of_measure.id"), nullable=True)
    # GST/HSN is carried by the existing tax-code master (TaxCode.hsn_sac).
    tax_code_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tax_codes.id"), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
