"""Trading-fabric item-master API (Stage 4B-1).

Minimal CRUD for the purchased-for-resale fabric SKU master. Company scope comes
from the authenticated session (``get_company_db`` + ``user.company_id``), so one
company's trading SKUs are never visible to another. Reuses the master-data
permissions (``master.read`` / ``master.write``).

No trading purchasing, GRN, stock, dispatch, returns or posting here — later,
separately-approved stages.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_company_db, require_permission
from app.models.core import User
from app.services import trading as svc

router = APIRouter(prefix="/api/trading", tags=["trading"])


class TradingItemIn(BaseModel):
    code: str
    name: str
    fabric_type_id: uuid.UUID | None = None
    construction: str | None = None
    composition: str | None = None
    gsm: Decimal | None = None
    width_cm: Decimal | None = None
    colour: str | None = None
    uom_id: uuid.UUID | None = None
    tax_code_id: uuid.UUID | None = None
    is_active: bool = True


class TradingItemUpdate(BaseModel):
    name: str | None = None
    fabric_type_id: uuid.UUID | None = None
    construction: str | None = None
    composition: str | None = None
    gsm: Decimal | None = None
    width_cm: Decimal | None = None
    colour: str | None = None
    uom_id: uuid.UUID | None = None
    tax_code_id: uuid.UUID | None = None


class ActiveIn(BaseModel):
    is_active: bool


def _out(item) -> dict:
    return {
        "id": str(item.id), "code": item.code, "name": item.name,
        "fabric_type_id": str(item.fabric_type_id) if item.fabric_type_id else None,
        "construction": item.construction, "composition": item.composition,
        "gsm": str(item.gsm) if item.gsm is not None else None,
        "width_cm": str(item.width_cm) if item.width_cm is not None else None,
        "colour": item.colour,
        "uom_id": str(item.uom_id) if item.uom_id else None,
        "tax_code_id": str(item.tax_code_id) if item.tax_code_id else None,
        "is_active": item.is_active,
    }


@router.post("/items", status_code=status.HTTP_201_CREATED)
def create_item(payload: TradingItemIn, db: Session = Depends(get_company_db),
                user: User = Depends(require_permission("master.write"))):
    try:
        item = svc.create_trading_item(db, user.company_id, created_by=user.id, **payload.model_dump())
        db.commit()
    except svc.TradingItemError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return _out(item)


@router.get("/items")
def list_items(search: str | None = None, active_only: bool = False,
               db: Session = Depends(get_company_db),
               user: User = Depends(require_permission("master.read"))):
    rows = svc.list_trading_items(db, user.company_id, search=search, active_only=active_only)
    return [_out(r) for r in rows]


@router.get("/items/{item_id}")
def get_item(item_id: uuid.UUID, db: Session = Depends(get_company_db),
             user: User = Depends(require_permission("master.read"))):
    try:
        return _out(svc.get_trading_item(db, user.company_id, item_id))
    except svc.TradingItemError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.patch("/items/{item_id}")
def update_item(item_id: uuid.UUID, payload: TradingItemUpdate, db: Session = Depends(get_company_db),
                user: User = Depends(require_permission("master.write"))):
    changes = payload.model_dump(exclude_unset=True)
    try:
        item = svc.update_trading_item(db, user.company_id, item_id, changes=changes, updated_by=user.id)
        db.commit()
    except svc.TradingItemError as exc:
        db.rollback()
        code = status.HTTP_404_NOT_FOUND if "not found" in str(exc).lower() else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(code, str(exc))
    return _out(item)


@router.post("/items/{item_id}/active")
def set_active(item_id: uuid.UUID, payload: ActiveIn, db: Session = Depends(get_company_db),
               user: User = Depends(require_permission("master.write"))):
    try:
        item = svc.set_trading_item_active(db, user.company_id, item_id, active=payload.is_active,
                                           updated_by=user.id)
        db.commit()
    except svc.TradingItemError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return _out(item)
