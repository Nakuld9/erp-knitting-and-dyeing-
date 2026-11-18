"""Trading-fabric sales invoice API — DRAFT foundation (Stage 4B-3A.1c.1).

Create/list/get/cancel DRAFT trading invoices allocated to approved dispatch
lines. No posting endpoint (revenue/GST/receivable posting is the CA-gated
4B-3A.1c.2 stage). Company scope from the authenticated session; reuses
``sales.manage``.
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
from app.services import trading_invoice as svc

router = APIRouter(prefix="/api/trading", tags=["trading-invoice"])


def _authz_factory(db, user, factory_id):
    if factory_id is not None:
        try:
            authorize_factory(db, user, factory_id, user.company_id)
        except FactoryAuthError:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not authorized for this factory")


def _422(exc):
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))


class InvoiceLineIn(BaseModel):
    dispatch_line_id: uuid.UUID
    trading_item_id: uuid.UUID
    quantity: Decimal
    rate: Decimal = Decimal("0")
    discount: Decimal = Decimal("0")
    tax_code_id: uuid.UUID | None = None
    uom_id: uuid.UUID | None = None
    description: str | None = None


class InvoiceIn(BaseModel):
    customer_id: uuid.UUID
    number: str
    invoice_date: date
    factory_id: uuid.UUID | None = None
    lines: list[InvoiceLineIn] = Field(min_length=1)


def _out(inv) -> dict:
    status_val = inv.status.value if hasattr(inv.status, "value") else inv.status
    return {
        "id": str(inv.id), "number": inv.number, "source": inv.source,
        "customer_id": str(inv.customer_id),
        "factory_id": str(inv.factory_id) if inv.factory_id else None,
        "invoice_date": inv.invoice_date.isoformat(), "status": status_val,
        "taxable_value": str(inv.taxable_value), "tax_amount": str(inv.tax_amount),
        "total_value": str(inv.total_value), "posted": status_val == "POSTED",
        "lines": svc.invoice_lines_view(inv),
    }


@router.post("/sales-invoices", status_code=status.HTTP_201_CREATED)
def create_invoice(payload: InvoiceIn, db: Session = Depends(get_company_db),
                   user: User = Depends(require_permission("sales.manage"))):
    _authz_factory(db, user, payload.factory_id)
    try:
        inv = svc.create_trading_invoice(db, user.company_id, created_by=user.id,
                                         lines=[ln.model_dump() for ln in payload.lines],
                                         **payload.model_dump(exclude={"lines"}))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Trading invoice number already exists")
    except svc.TradingInvoiceInconsistent as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except svc.TradingInvoiceError as exc:
        db.rollback()
        code = status.HTTP_409_CONFLICT if "already exists" in str(exc).lower() else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(code, str(exc))
    return _out(inv)


@router.get("/sales-invoices")
def list_invoices(customer_id: uuid.UUID | None = None, status_filter: str | None = None,
                  limit: int = 100, offset: int = 0, db: Session = Depends(get_company_db),
                  user: User = Depends(require_permission("sales.manage"))):
    rows, total = svc.list_trading_invoices(db, user.company_id, customer_id=customer_id,
                                            status=status_filter, limit=limit, offset=offset)
    return {"total": total, "limit": limit, "offset": offset, "items": [_out(r) for r in rows]}


@router.get("/sales-invoices/{invoice_id}")
def get_invoice(invoice_id: uuid.UUID, db: Session = Depends(get_company_db),
                user: User = Depends(require_permission("sales.manage"))):
    try:
        return _out(svc.get_trading_invoice(db, user.company_id, invoice_id))
    except svc.TradingInvoiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.post("/sales-invoices/{invoice_id}/cancel")
def cancel_invoice(invoice_id: uuid.UUID, db: Session = Depends(get_company_db),
                   user: User = Depends(require_permission("sales.manage"))):
    try:
        inv = svc.cancel_trading_invoice(db, user.company_id, invoice_id, cancelled_by=user.id)
        db.commit()
    except svc.TradingInvoiceError as exc:
        db.rollback()
        code = status.HTTP_404_NOT_FOUND if "not found" in str(exc).lower() else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(code, str(exc))
    return _out(inv)


@router.post("/sales-invoices/{invoice_id}/post")
def post_invoice(invoice_id: uuid.UUID, db: Session = Depends(get_company_db),
                 user: User = Depends(require_permission("voucher.post"))):
    """Post a DRAFT trading invoice: revenue + output GST + customer receivable, in
    one balanced voucher. Blocked (409) when TRADING_SALES mappings or GST
    place-of-supply inputs are missing — no voucher, invoice stays DRAFT. Trading
    invoices only (the shared manufactured route is never used)."""
    try:
        res = svc.post_trading_invoice(db, user.company_id, invoice_id, posted_by=user.id)
        db.commit()
    except svc.TradingInvoicePostingBlocked as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except svc.TradingInvoiceInconsistent as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except svc.TradingInvoiceError as exc:
        db.rollback()
        code = status.HTTP_404_NOT_FOUND if "not found" in str(exc).lower() else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(code, str(exc))
    inv = svc.get_trading_invoice(db, user.company_id, invoice_id)
    out = _out(inv)
    v = res.get("voucher")
    out["voucher_number"] = getattr(v, "number", None)
    out["already_posted"] = res.get("already_posted", False)
    return out
