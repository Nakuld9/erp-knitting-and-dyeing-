"""Trading-fabric dispatch API (Stage 4B-3A.1b.1 — draft lifecycle only).

Create/list/get/submit/cancel a trading dispatch DRAFT against an approved
trading sales order. No approval, stock movement, FIFO, COGS or accounting — the
/approve route is intentionally absent until a later, separately-authorised
stage. Company scope comes from the authenticated session. Reuses ``sales.manage``.
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
from app.services import trading_dispatch as svc

router = APIRouter(prefix="/api/trading", tags=["trading-dispatch"])


def _authz_factory(db, user, factory_id):
    if factory_id is not None:
        try:
            authorize_factory(db, user, factory_id, user.company_id)
        except FactoryAuthError:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not authorized for this factory")


def _422(exc):
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))


class DispatchLineIn(BaseModel):
    trading_item_id: uuid.UUID
    sales_order_line_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity: Decimal
    uom_id: uuid.UUID | None = None
    description: str | None = None


class DispatchIn(BaseModel):
    customer_id: uuid.UUID
    sales_order_id: uuid.UUID
    dispatch_number: str
    dispatch_date: date
    factory_id: uuid.UUID | None = None
    vehicle_number: str | None = None
    transporter: str | None = None
    customer_delivery_ref: str | None = None
    remarks: str | None = None
    lines: list[DispatchLineIn] = Field(min_length=1)


def _out(d) -> dict:
    return {
        "id": str(d.id), "dispatch_number": d.dispatch_number, "customer_id": str(d.customer_id),
        "sales_order_id": str(d.sales_order_id),
        "factory_id": str(d.factory_id) if d.factory_id else None,
        "dispatch_date": d.dispatch_date.isoformat(), "status": d.status,
        "vehicle_number": d.vehicle_number, "transporter": d.transporter,
        "customer_delivery_ref": d.customer_delivery_ref, "remarks": d.remarks,
        "lines": svc.dispatch_lines_view(d),
    }


@router.post("/dispatches", status_code=status.HTTP_201_CREATED)
def create_dispatch(payload: DispatchIn, db: Session = Depends(get_company_db),
                    user: User = Depends(require_permission("sales.manage"))):
    _authz_factory(db, user, payload.factory_id)
    try:
        d = svc.create_trading_dispatch(db, user.company_id, created_by=user.id,
                                        lines=[ln.model_dump() for ln in payload.lines],
                                        **payload.model_dump(exclude={"lines"}))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Dispatch number already exists")
    except svc.TradingDispatchError as exc:
        db.rollback(); _422(exc)
    return _out(d)


@router.get("/dispatches")
def list_dispatches(status_filter: str | None = None, sales_order_id: uuid.UUID | None = None,
                    limit: int = 100, offset: int = 0, db: Session = Depends(get_company_db),
                    user: User = Depends(require_permission("sales.manage"))):
    rows, total = svc.list_trading_dispatches(db, user.company_id, status=status_filter,
                                              sales_order_id=sales_order_id, limit=limit, offset=offset)
    return {"total": total, "limit": limit, "offset": offset, "items": [_out(r) for r in rows]}


@router.get("/dispatches/{dispatch_id}")
def get_dispatch(dispatch_id: uuid.UUID, db: Session = Depends(get_company_db),
                 user: User = Depends(require_permission("sales.manage"))):
    try:
        return _out(svc.get_trading_dispatch(db, user.company_id, dispatch_id))
    except svc.TradingDispatchError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.post("/dispatches/{dispatch_id}/submit")
def submit_dispatch(dispatch_id: uuid.UUID, db: Session = Depends(get_company_db),
                    user: User = Depends(require_permission("sales.manage"))):
    try:
        d = svc.submit_trading_dispatch(db, user.company_id, dispatch_id, submitted_by=user.id)
        db.commit()
    except svc.TradingDispatchError as exc:
        db.rollback(); _422(exc)
    return _out(d)


@router.post("/dispatches/{dispatch_id}/cancel")
def cancel_dispatch(dispatch_id: uuid.UUID, db: Session = Depends(get_company_db),
                    user: User = Depends(require_permission("sales.manage"))):
    try:
        d = svc.cancel_trading_dispatch(db, user.company_id, dispatch_id, cancelled_by=user.id)
        db.commit()
    except svc.TradingDispatchError as exc:
        db.rollback(); _422(exc)
    return _out(d)


class ApproveIn(BaseModel):
    over_dispatch_reason: str | None = None


@router.post("/dispatches/{dispatch_id}/approve")
def approve_dispatch(dispatch_id: uuid.UUID, payload: ApproveIn | None = None,
                     db: Session = Depends(get_company_db),
                     user: User = Depends(require_permission("voucher.post"))):
    """Approve a SUBMITTED dispatch: consume trading FIFO stock and post a balanced
    COGS voucher atomically. Blocked (409) when TRADING_DELIVERY mappings are
    unconfigured — no stock movement occurs. Over-dispatch needs the
    'trading.over_dispatch' permission (resolved server-side) plus a reason."""
    from app.services.inventory.service import InsufficientStockError
    over_ok = user.has_permission(svc.OVER_DISPATCH_PERMISSION)
    reason = payload.over_dispatch_reason if payload else None
    try:
        res = svc.approve_trading_dispatch(db, user.company_id, dispatch_id, approved_by=user.id,
                                           over_dispatch_authorized=over_ok, over_dispatch_reason=reason)
        db.commit()
    except svc.TradingDispatchPostingBlocked as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except svc.TradingDispatchInconsistent as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except (svc.TradingDispatchOverDispatchError, svc.TradingDispatchError, InsufficientStockError) as exc:
        db.rollback(); _422(exc)
    d = svc.get_trading_dispatch(db, user.company_id, dispatch_id)
    out = _out(d)
    v = res.get("voucher")
    out["voucher_number"] = getattr(v, "number", None)
    out["cogs_value"] = str(res.get("cogs_value"))
    out["already_approved"] = res.get("already_approved", False)
    return out


@router.get("/dispatches/{dispatch_id}/allocations")
def dispatch_allocations(dispatch_id: uuid.UUID, db: Session = Depends(get_company_db),
                         user: User = Depends(require_permission("sales.manage"))):
    try:
        svc.get_trading_dispatch(db, user.company_id, dispatch_id)   # company-scope check
    except svc.TradingDispatchError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return {"dispatch_id": str(dispatch_id),
            "allocations": svc.dispatch_allocations_view(db, user.company_id, dispatch_id)}
