"""Trading-fabric foundation services (Stage 4B-1).

Scope is deliberately narrow:
* Trading-fabric item-master CRUD with company-scoped validation.
* A strict trading-warehouse validator used by future trading stock operations.

NO trading purchasing, GRN, stock receipts, dispatch, returns or financial
posting is implemented here — those are later, separately-approved stages.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.hybrid import WarehouseCategory
from app.models.masters import FabricType, TaxCode, UnitOfMeasure, Warehouse
from app.models.trading import TradingFabricItem

# Fields a client may set via update (identity + spec), excluding company
# ownership, audit columns and the primary key. ``is_active`` is deliberately
# NOT here: activation/deactivation is only via ``set_trading_item_active`` (the
# dedicated /active endpoint), so a general update can never flip it.
_EDITABLE = (
    "name", "fabric_type_id", "construction", "composition", "gsm",
    "width_cm", "colour", "uom_id", "tax_code_id",
)


class TradingItemError(ValueError):
    """An invalid trading item-master request (bad code, duplicate, or a
    reference that does not belong to the active company)."""


class TradingWarehouseError(ValueError):
    """A warehouse that is not a valid TRADING location for the active company
    (missing, wrong company/factory, or not categorised exactly TRADING)."""


# --------------------------- reference validation ---------------------------

def _validate_refs(db: Session, company_id: uuid.UUID, *, fabric_type_id, uom_id, tax_code_id) -> None:
    """Every optional reference must belong to the SAME company. Cross-company
    references are rejected (no leakage of one company's masters into another)."""
    if fabric_type_id is not None:
        ft = db.get(FabricType, fabric_type_id)
        if ft is None or ft.company_id != company_id:
            raise TradingItemError("Referenced fabric type is not in the active company")
    if uom_id is not None:
        uom = db.get(UnitOfMeasure, uom_id)
        if uom is None or uom.company_id != company_id:
            raise TradingItemError("Referenced unit of measure is not in the active company")
    if tax_code_id is not None:
        tc = db.get(TaxCode, tax_code_id)
        if tc is None or tc.company_id != company_id:
            raise TradingItemError("Referenced tax code is not in the active company")


# ------------------------------- item master --------------------------------

def create_trading_item(db: Session, company_id: uuid.UUID, *, code: str, name: str,
                        fabric_type_id: uuid.UUID | None = None, construction: str | None = None,
                        composition: str | None = None, gsm=None, width_cm=None,
                        colour: str | None = None, uom_id: uuid.UUID | None = None,
                        tax_code_id: uuid.UUID | None = None, is_active: bool = True,
                        created_by: uuid.UUID | None = None) -> TradingFabricItem:
    code = (code or "").strip()
    if not code:
        raise TradingItemError("Trading SKU code is required")
    if not (name or "").strip():
        raise TradingItemError("Trading SKU name is required")
    dup = db.execute(select(TradingFabricItem).where(
        TradingFabricItem.company_id == company_id, TradingFabricItem.code == code)).scalar_one_or_none()
    if dup is not None:
        raise TradingItemError(f"Trading SKU code '{code}' already exists")
    _validate_refs(db, company_id, fabric_type_id=fabric_type_id, uom_id=uom_id, tax_code_id=tax_code_id)
    item = TradingFabricItem(
        company_id=company_id, code=code, name=name.strip(), fabric_type_id=fabric_type_id,
        construction=construction, composition=composition, gsm=gsm, width_cm=width_cm,
        colour=colour, uom_id=uom_id, tax_code_id=tax_code_id, is_active=is_active,
        created_by=created_by,
    )
    db.add(item)
    db.flush()
    return item


def get_trading_item(db: Session, company_id: uuid.UUID, item_id: uuid.UUID) -> TradingFabricItem:
    item = db.get(TradingFabricItem, item_id)
    if item is None or item.company_id != company_id:
        raise TradingItemError("Trading SKU not found in the active company")
    return item


def list_trading_items(db: Session, company_id: uuid.UUID, *, search: str | None = None,
                       active_only: bool = False) -> list[TradingFabricItem]:
    stmt = select(TradingFabricItem).where(TradingFabricItem.company_id == company_id)
    if active_only:
        stmt = stmt.where(TradingFabricItem.is_active.is_(True))
    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(TradingFabricItem.code.ilike(like) | TradingFabricItem.name.ilike(like))
    return db.execute(stmt.order_by(TradingFabricItem.code)).scalars().all()


def update_trading_item(db: Session, company_id: uuid.UUID, item_id: uuid.UUID, *,
                        changes: dict, updated_by: uuid.UUID | None = None) -> TradingFabricItem:
    """Update permitted fields. The SKU code and company are immutable here (code
    identity is stable); references are re-validated against the same company."""
    item = get_trading_item(db, company_id, item_id)
    fields = {k: v for k, v in changes.items() if k in _EDITABLE}
    _validate_refs(
        db, company_id,
        fabric_type_id=fields.get("fabric_type_id", item.fabric_type_id),
        uom_id=fields.get("uom_id", item.uom_id),
        tax_code_id=fields.get("tax_code_id", item.tax_code_id),
    )
    if "name" in fields and not (fields["name"] or "").strip():
        raise TradingItemError("Trading SKU name is required")
    for k, v in fields.items():
        setattr(item, k, v)
    item.updated_by = updated_by
    db.flush()
    return item


def set_trading_item_active(db: Session, company_id: uuid.UUID, item_id: uuid.UUID, *,
                            active: bool, updated_by: uuid.UUID | None = None) -> TradingFabricItem:
    item = get_trading_item(db, company_id, item_id)
    item.is_active = active
    item.updated_by = updated_by
    db.flush()
    return item


# -------------------------- trading warehouse rule --------------------------

def assert_trading_warehouse(db: Session, company_id: uuid.UUID, warehouse_id: uuid.UUID | None, *,
                             factory_id: uuid.UUID | None = None) -> Warehouse:
    """Validate a warehouse as a TRADING location for the active company.

    Unlike the manufacturing ``require_warehouse_stage`` (which is lenient when a
    warehouse has no category), trading is STRICT: the category must be exactly
    TRADING. Trading stock therefore can never silently fall back to an
    uncategorised or manufacturing warehouse.

    Checks, in order:
      1. a warehouse id is given and the row exists;
      2. it belongs to ``company_id``;
      3. when ``factory_id`` is given and the warehouse is tied to a factory, it
         is that factory;
      4. its category is exactly ``TRADING``.
    """
    if warehouse_id is None:
        raise TradingWarehouseError("A TRADING warehouse must be specified for trading stock")
    wh = db.get(Warehouse, warehouse_id)
    if wh is None or wh.company_id != company_id:
        raise TradingWarehouseError("Warehouse not found in the active company")
    if factory_id is not None and wh.branch_id is not None and wh.branch_id != factory_id:
        raise TradingWarehouseError(
            f"Warehouse '{wh.code}' belongs to a different factory; select a TRADING "
            "warehouse in this factory.")
    if wh.category != WarehouseCategory.TRADING.value:
        got = wh.category or "uncategorised"
        raise TradingWarehouseError(
            f"Warehouse '{wh.code}' is a {got} warehouse; trading stock requires a "
            "TRADING warehouse.")
    return wh
