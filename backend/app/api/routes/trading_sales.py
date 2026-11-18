"""Trading-fabric sales order API (Stage 4B-3A.1a).

Sales ORDER only — create/list/get/outstanding/approve/cancel plus an
informational availability read. No dispatch, inventory movement, invoice or
accounting. Company scope comes from the authenticated session; a client cannot
supply a company id. Reuses the existing ``sales.manage`` permission.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.context import FactoryAuthError, authorize_factory
from app.core.deps import get_company_db, require_permission
from app.models.core import User
from app.services import trading_sales as svc

router = APIRouter(prefix="/api/trading", tags=["trading-sales"])


def _authz_factory(db, user, factory_id):
    if factory_id is not None:
        try:
            authorize_factory(db, user, factory_id, user.company_id)
        except FactoryAuthError:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not authorized for this factory")


def _422(exc):
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))


class SOLineIn(BaseModel):
    trading_item_id: uuid.UUID
    warehouse_id: uuid.UUID
    qty_ordered: Decimal
    rate: Decimal = Decimal("0")
    discount: Decimal = Decimal("0")
    uom_id: uuid.UUID | None = None
    tax_code_id: uuid.UUID | None = None
    description: str | None = None


class SOIn(BaseModel):
    customer_id: uuid.UUID
    order_number: str
    order_date: date
    expected_dispatch_date: date | None = None
    customer_po_ref: str | None = None
    factory_id: uuid.UUID | None = None
    remarks: str | None = None
    lines: list[SOLineIn] = Field(min_length=1)


def _so_out(so) -> dict:
    return {
        "id": str(so.id), "order_number": so.order_number, "customer_id": str(so.customer_id),
        "factory_id": str(so.factory_id) if so.factory_id else None,
        "order_date": so.order_date.isoformat(),
        "expected_dispatch_date": so.expected_dispatch_date.isoformat() if so.expected_dispatch_date else None,
        "customer_po_ref": so.customer_po_ref, "status": so.status, "remarks": so.remarks,
        "lines": [{"id": str(l.id), "line_no": l.line_no, "trading_item_id": str(l.trading_item_id),
                   "warehouse_id": str(l.warehouse_id), "qty_ordered": str(l.qty_ordered),
                   "qty_dispatched": str(l.qty_dispatched), "qty_cancelled": str(l.qty_cancelled),
                   "rate": str(l.rate), "discount": str(l.discount),
                   "tax_code_id": str(l.tax_code_id) if l.tax_code_id else None,
                   "description": l.description} for l in so.lines],
    }


@router.post("/sales-orders", status_code=status.HTTP_201_CREATED)
def create_so(payload: SOIn, db: Session = Depends(get_company_db),
              user: User = Depends(require_permission("sales.manage"))):
    _authz_factory(db, user, payload.factory_id)
    try:
        so = svc.create_trading_so(db, user.company_id, created_by=user.id,
                                   lines=[ln.model_dump() for ln in payload.lines],
                                   **payload.model_dump(exclude={"lines"}))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Sales order number already exists")
    except svc.TradingSalesError as exc:
        db.rollback(); _422(exc)
    return _so_out(so)


@router.get("/sales-orders")
def list_sos(status_filter: str | None = None, customer_id: uuid.UUID | None = None,
             limit: int = 100, offset: int = 0, db: Session = Depends(get_company_db),
             user: User = Depends(require_permission("sales.manage"))):
    rows, total = svc.list_trading_sos(db, user.company_id, status=status_filter,
                                       customer_id=customer_id, limit=limit, offset=offset)
    return {"total": total, "limit": limit, "offset": offset, "items": [_so_out(r) for r in rows]}


@router.get("/sales-orders/{so_id}")
def get_so(so_id: uuid.UUID, db: Session = Depends(get_company_db),
           user: User = Depends(require_permission("sales.manage"))):
    try:
        return _so_out(svc.get_trading_so(db, user.company_id, so_id))
    except svc.TradingSalesError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.get("/sales-orders/{so_id}/outstanding")
def so_outstanding(so_id: uuid.UUID, db: Session = Depends(get_company_db),
                   user: User = Depends(require_permission("sales.manage"))):
    try:
        so = svc.get_trading_so(db, user.company_id, so_id)
    except svc.TradingSalesError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return {"sales_order_id": str(so.id), "status": so.status,
            "lines": [{**r, "ordered": str(r["ordered"]), "dispatched": str(r["dispatched"]),
                       "cancelled": str(r["cancelled"]), "outstanding": str(r["outstanding"])}
                      for r in svc.so_outstanding(so)]}


@router.post("/sales-orders/{so_id}/approve")
def approve_so(so_id: uuid.UUID, db: Session = Depends(get_company_db),
               user: User = Depends(require_permission("sales.manage"))):
    try:
        so = svc.approve_trading_so(db, user.company_id, so_id, approved_by=user.id)
        db.commit()
    except svc.TradingSalesError as exc:
        db.rollback(); _422(exc)
    return _so_out(so)


@router.post("/sales-orders/{so_id}/cancel")
def cancel_so(so_id: uuid.UUID, db: Session = Depends(get_company_db),
              user: User = Depends(require_permission("sales.manage"))):
    try:
        so = svc.cancel_trading_so(db, user.company_id, so_id, cancelled_by=user.id)
        db.commit()
    except svc.TradingSalesError as exc:
        db.rollback(); _422(exc)
    return _so_out(so)


@router.get("/availability")
def availability(trading_item_id: uuid.UUID, warehouse_id: uuid.UUID | None = None,
                 db: Session = Depends(get_company_db),
                 user: User = Depends(require_permission("sales.manage"))):
    """Informational on-hand trading stock. NOT a reservation or guaranteed
    allocation — stock may be consumed by another order before dispatch."""
    try:
        res = svc.availability(db, user.company_id, trading_item_id, warehouse_id)
    except svc.TradingSalesError as exc:
        _422(exc)
    return {**res, "on_hand_qty": str(res["on_hand_qty"]), "on_hand_value": str(res["on_hand_value"])}
