"""Stores & spares endpoints: item master, receipts, issues."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_permission
from app.models.core import User
from app.models.stores import StoreItem
from app.services import stores as svc
from app.services.inventory.service import InsufficientStockError

router = APIRouter(prefix="/api/stores", tags=["stores"])


class ItemIn(BaseModel):
    code: str
    name: str
    category: str = "OTHER"          # NEEDLE | SPARE | PACKING | OTHER
    uom: str = "PCS"
    reorder_level: Decimal = Decimal("0")


@router.post("/items", status_code=status.HTTP_201_CREATED)
def create_item(payload: ItemIn, db: Session = Depends(get_db),
                user: User = Depends(require_permission("master.write"))):
    try:
        item = svc.create_item(db, company_id=user.company_id, created_by=user.id,
                               **payload.model_dump())
        db.commit()
    except svc.StoreError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return {"id": str(item.id), "code": item.code, "name": item.name}


@router.get("/items")
def list_items(db: Session = Depends(get_db),
               user: User = Depends(require_permission("master.read"))):
    rows = db.execute(select(StoreItem).where(StoreItem.company_id == user.company_id)
                      .order_by(StoreItem.code)).scalars().all()
    return [{"id": str(i.id), "code": i.code, "name": i.name,
             "category": i.category.value if hasattr(i.category, "value") else i.category,
             "uom": i.uom, "reorder_level": str(i.reorder_level)} for i in rows]


class ReceiptIn(BaseModel):
    store_item_id: uuid.UUID
    qty: Decimal
    unit_cost: Decimal
    warehouse_id: uuid.UUID | None = None
    supplier_id: uuid.UUID | None = None
    number: str | None = None
    vdate: date | None = None


@router.post("/receipts", status_code=status.HTTP_201_CREATED)
def receive(payload: ReceiptIn, db: Session = Depends(get_db),
            user: User = Depends(require_permission("purchase.manage"))):
    try:
        res = svc.receive(db, company_id=user.company_id, created_by=user.id,
                          **payload.model_dump())
        db.commit()
    except svc.StoreError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return res


class IssueIn(BaseModel):
    store_item_id: uuid.UUID
    qty: Decimal
    warehouse_id: uuid.UUID | None = None
    machine: str | None = None
    vdate: date | None = None


@router.post("/issues", status_code=status.HTTP_201_CREATED)
def issue(payload: IssueIn, db: Session = Depends(get_db),
          user: User = Depends(require_permission("voucher.create"))):
    try:
        res = svc.issue(db, company_id=user.company_id, created_by=user.id, **payload.model_dump())
        db.commit()
    except (svc.StoreError, InsufficientStockError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return res
