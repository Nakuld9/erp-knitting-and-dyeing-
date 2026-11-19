"""Direct yarn-sales API (Stage 5B).

Order -> lot allocation (at dispatch) -> approval -> dispatch (FIFO COGS) ->
invoice -> posting. Reuses ``sales.manage`` for documents and ``voucher.post`` for
ledger-affecting steps (dispatch COGS, invoice posting); company from session;
factory scoping via ``authorize_factory``. No printing/PDF here.
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
from app.services import yarn_sales_order as so_svc
from app.services import yarn_sales_dispatch as dsp_svc
from app.services import yarn_sales_invoice as inv_svc
from app.services.inventory.service import InsufficientStockError

router = APIRouter(prefix="/api/yarn-sales", tags=["yarn-sales"])


def _authz_factory(db, user, factory_id):
    if factory_id is not None:
        try:
            authorize_factory(db, user, factory_id, user.company_id)
        except FactoryAuthError:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not authorized for this factory")


def _422(exc):
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))


# ------------------------------ sales orders --------------------------------

class SOLineIn(BaseModel):
    yarn_id: uuid.UUID
    warehouse_id: uuid.UUID | None = None
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
    factory_id: uuid.UUID | None = None
    expected_dispatch_date: date | None = None
    customer_po_ref: str | None = None
    remarks: str | None = None
    lines: list[SOLineIn] = Field(min_length=1)


def _so_out(so) -> dict:
    return {
        "id": str(so.id), "order_number": so.order_number, "status": so.status,
        "customer_id": str(so.customer_id),
        "factory_id": str(so.factory_id) if so.factory_id else None,
        "order_date": so.order_date.isoformat(),
        "lines": [{"id": str(l.id), "line_no": l.line_no, "yarn_id": str(l.yarn_id),
                   "warehouse_id": str(l.warehouse_id) if l.warehouse_id else None,
                   "qty_ordered": str(l.qty_ordered), "qty_dispatched": str(l.qty_dispatched),
                   "qty_cancelled": str(l.qty_cancelled), "rate": str(l.rate),
                   "discount": str(l.discount),
                   "tax_code_id": str(l.tax_code_id) if l.tax_code_id else None,
                   "uom_id": str(l.uom_id) if l.uom_id else None, "description": l.description}
                  for l in so.lines],
        "outstanding": so_svc.so_outstanding(so),
    }


@router.post("/orders", status_code=status.HTTP_201_CREATED)
def create_order(payload: SOIn, db: Session = Depends(get_company_db),
                 user: User = Depends(require_permission("sales.manage"))):
    _authz_factory(db, user, payload.factory_id)
    try:
        so = so_svc.create_yarn_so(db, user.company_id, created_by=user.id,
                                   lines=[ln.model_dump() for ln in payload.lines],
                                   **payload.model_dump(exclude={"lines"}))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Yarn sales order number already exists")
    except so_svc.YarnSalesError as exc:
        db.rollback()
        code = status.HTTP_409_CONFLICT if "already exists" in str(exc).lower() else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(code, str(exc))
    return _so_out(so)


@router.get("/orders")
def list_orders(status_filter: str | None = None, customer_id: uuid.UUID | None = None,
                limit: int = 100, offset: int = 0, db: Session = Depends(get_company_db),
                user: User = Depends(require_permission("sales.manage"))):
    rows, total = so_svc.list_yarn_sos(db, user.company_id, status=status_filter,
                                       customer_id=customer_id, limit=limit, offset=offset)
    return {"total": total, "limit": limit, "offset": offset, "items": [_so_out(r) for r in rows]}


@router.get("/orders/{so_id}")
def get_order(so_id: uuid.UUID, db: Session = Depends(get_company_db),
              user: User = Depends(require_permission("sales.manage"))):
    try:
        return _so_out(so_svc.get_yarn_so(db, user.company_id, so_id))
    except so_svc.YarnSalesError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.post("/orders/{so_id}/approve")
def approve_order(so_id: uuid.UUID, db: Session = Depends(get_company_db),
                  user: User = Depends(require_permission("sales.manage"))):
    try:
        so = so_svc.approve_yarn_so(db, user.company_id, so_id, approved_by=user.id)
        db.commit()
    except so_svc.YarnSalesError as exc:
        db.rollback()
        code = status.HTTP_404_NOT_FOUND if "not found" in str(exc).lower() else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(code, str(exc))
    return _so_out(so)


@router.post("/orders/{so_id}/cancel")
def cancel_order(so_id: uuid.UUID, db: Session = Depends(get_company_db),
                 user: User = Depends(require_permission("sales.manage"))):
    try:
        so = so_svc.cancel_yarn_so(db, user.company_id, so_id, cancelled_by=user.id)
        db.commit()
    except so_svc.YarnSalesError as exc:
        db.rollback()
        code = status.HTTP_404_NOT_FOUND if "not found" in str(exc).lower() else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(code, str(exc))
    return _so_out(so)


@router.get("/lot-availability")
def lot_availability(yarn_id: uuid.UUID, warehouse_id: uuid.UUID | None = None,
                     db: Session = Depends(get_company_db),
                     user: User = Depends(require_permission("sales.manage"))):
    try:
        return so_svc.lot_availability(db, user.company_id, yarn_id, warehouse_id=warehouse_id)
    except so_svc.YarnSalesError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))


# ------------------------------ dispatches ----------------------------------

class DispatchLineIn(BaseModel):
    yarn_id: uuid.UUID
    sales_order_line_id: uuid.UUID
    warehouse_id: uuid.UUID | None = None
    lot_id: uuid.UUID | None = None
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


def _dsp_out(db, d) -> dict:
    return {
        "id": str(d.id), "dispatch_number": d.dispatch_number, "status": d.status,
        "customer_id": str(d.customer_id), "sales_order_id": str(d.sales_order_id),
        "factory_id": str(d.factory_id) if d.factory_id else None,
        "dispatch_date": d.dispatch_date.isoformat(), "cogs_value": str(d.cogs_value),
        "is_over_dispatched": d.is_over_dispatched,
        "lines": dsp_svc.dispatch_lines_view(db, d),
    }


@router.post("/dispatches", status_code=status.HTTP_201_CREATED)
def create_dispatch(payload: DispatchIn, db: Session = Depends(get_company_db),
                    user: User = Depends(require_permission("sales.manage"))):
    _authz_factory(db, user, payload.factory_id)
    try:
        d = dsp_svc.create_yarn_dispatch(db, user.company_id, created_by=user.id,
                                         lines=[ln.model_dump() for ln in payload.lines],
                                         **payload.model_dump(exclude={"lines"}))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Dispatch number already exists")
    except dsp_svc.YarnDispatchError as exc:
        db.rollback()
        code = status.HTTP_409_CONFLICT if "already exists" in str(exc).lower() else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(code, str(exc))
    return _dsp_out(db, d)


@router.get("/dispatches")
def list_dispatches(status_filter: str | None = None, sales_order_id: uuid.UUID | None = None,
                    limit: int = 100, offset: int = 0, db: Session = Depends(get_company_db),
                    user: User = Depends(require_permission("sales.manage"))):
    rows, total = dsp_svc.list_yarn_dispatches(db, user.company_id, status=status_filter,
                                               sales_order_id=sales_order_id, limit=limit, offset=offset)
    return {"total": total, "limit": limit, "offset": offset, "items": [_dsp_out(db, r) for r in rows]}


@router.get("/dispatches/{dispatch_id}")
def get_dispatch(dispatch_id: uuid.UUID, db: Session = Depends(get_company_db),
                 user: User = Depends(require_permission("sales.manage"))):
    try:
        return _dsp_out(db, dsp_svc.get_yarn_dispatch(db, user.company_id, dispatch_id))
    except dsp_svc.YarnDispatchError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.post("/dispatches/{dispatch_id}/submit")
def submit_dispatch(dispatch_id: uuid.UUID, db: Session = Depends(get_company_db),
                    user: User = Depends(require_permission("sales.manage"))):
    try:
        d = dsp_svc.submit_yarn_dispatch(db, user.company_id, dispatch_id, submitted_by=user.id)
        db.commit()
    except dsp_svc.YarnDispatchError as exc:
        db.rollback()
        code = status.HTTP_404_NOT_FOUND if "not found" in str(exc).lower() else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(code, str(exc))
    return _dsp_out(db, d)


class ApproveIn(BaseModel):
    over_dispatch_reason: str | None = None


@router.post("/dispatches/{dispatch_id}/approve")
def approve_dispatch(dispatch_id: uuid.UUID, payload: ApproveIn | None = None,
                     db: Session = Depends(get_company_db),
                     user: User = Depends(require_permission("voucher.post"))):
    """Approve a SUBMITTED dispatch: consume yarn FIFO stock and post a balanced
    YARN_DELIVERY COGS voucher atomically. Blocked (409) when mappings are missing —
    no stock movement. Over-dispatch needs 'yarn.over_dispatch' + a reason."""
    over_ok = user.has_permission(dsp_svc.OVER_DISPATCH_PERMISSION)
    reason = payload.over_dispatch_reason if payload else None
    try:
        res = dsp_svc.approve_yarn_dispatch(db, user.company_id, dispatch_id, approved_by=user.id,
                                            over_dispatch_authorized=over_ok, over_dispatch_reason=reason)
        db.commit()
    except dsp_svc.YarnDispatchPostingBlocked as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except dsp_svc.YarnDispatchInconsistent as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except (dsp_svc.YarnDispatchOverDispatchError, dsp_svc.YarnDispatchError, InsufficientStockError) as exc:
        db.rollback(); _422(exc)
    d = dsp_svc.get_yarn_dispatch(db, user.company_id, dispatch_id)
    out = _dsp_out(db, d)
    v = res.get("voucher")
    out["voucher_number"] = getattr(v, "number", None)
    out["cogs_value"] = str(res.get("cogs_value"))
    out["already_approved"] = res.get("already_approved", False)
    return out


@router.post("/dispatches/{dispatch_id}/cancel")
def cancel_dispatch(dispatch_id: uuid.UUID, db: Session = Depends(get_company_db),
                    user: User = Depends(require_permission("sales.manage"))):
    try:
        d = dsp_svc.cancel_yarn_dispatch(db, user.company_id, dispatch_id, cancelled_by=user.id)
        db.commit()
    except dsp_svc.YarnDispatchError as exc:
        db.rollback()
        code = status.HTTP_404_NOT_FOUND if "not found" in str(exc).lower() else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(code, str(exc))
    return _dsp_out(db, d)


@router.get("/dispatches/{dispatch_id}/allocations")
def dispatch_allocations(dispatch_id: uuid.UUID, db: Session = Depends(get_company_db),
                         user: User = Depends(require_permission("sales.manage"))):
    try:
        dsp_svc.get_yarn_dispatch(db, user.company_id, dispatch_id)
    except dsp_svc.YarnDispatchError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return {"items": dsp_svc.dispatch_allocations_view(db, user.company_id, dispatch_id)}


# ------------------------------ invoices ------------------------------------

class InvLineIn(BaseModel):
    dispatch_line_id: uuid.UUID
    quantity: Decimal
    rate: Decimal = Decimal("0")
    discount: Decimal = Decimal("0")
    tax_code_id: uuid.UUID | None = None
    uom_id: uuid.UUID | None = None
    description: str | None = None


class InvIn(BaseModel):
    customer_id: uuid.UUID
    number: str
    invoice_date: date
    factory_id: uuid.UUID | None = None
    lines: list[InvLineIn] = Field(min_length=1)


def _inv_out(db, inv) -> dict:
    status_val = inv.status.value if hasattr(inv.status, "value") else inv.status
    return {
        "id": str(inv.id), "number": inv.number, "source": inv.source,
        "customer_id": str(inv.customer_id),
        "factory_id": str(inv.factory_id) if inv.factory_id else None,
        "invoice_date": inv.invoice_date.isoformat(), "status": status_val,
        "taxable_value": str(inv.taxable_value), "tax_amount": str(inv.tax_amount),
        "total_value": str(inv.total_value), "posted": status_val == "POSTED",
        "lines": inv_svc.invoice_lines_view(db, inv),
    }


@router.post("/sales-invoices", status_code=status.HTTP_201_CREATED)
def create_invoice(payload: InvIn, db: Session = Depends(get_company_db),
                   user: User = Depends(require_permission("sales.manage"))):
    _authz_factory(db, user, payload.factory_id)
    try:
        inv = inv_svc.create_yarn_invoice(db, user.company_id, created_by=user.id,
                                          lines=[ln.model_dump() for ln in payload.lines],
                                          **payload.model_dump(exclude={"lines"}))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Yarn invoice number already exists")
    except inv_svc.YarnInvoiceInconsistent as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except inv_svc.YarnInvoiceError as exc:
        db.rollback()
        code = status.HTTP_409_CONFLICT if "already exists" in str(exc).lower() else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(code, str(exc))
    return _inv_out(db, inv)


@router.get("/sales-invoices")
def list_invoices(customer_id: uuid.UUID | None = None, status_filter: str | None = None,
                  limit: int = 100, offset: int = 0, db: Session = Depends(get_company_db),
                  user: User = Depends(require_permission("sales.manage"))):
    rows, total = inv_svc.list_yarn_invoices(db, user.company_id, customer_id=customer_id,
                                             status=status_filter, limit=limit, offset=offset)
    return {"total": total, "limit": limit, "offset": offset, "items": [_inv_out(db, r) for r in rows]}


@router.get("/sales-invoices/{invoice_id}")
def get_invoice(invoice_id: uuid.UUID, db: Session = Depends(get_company_db),
                user: User = Depends(require_permission("sales.manage"))):
    try:
        return _inv_out(db, inv_svc.get_yarn_invoice(db, user.company_id, invoice_id))
    except inv_svc.YarnInvoiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.post("/sales-invoices/{invoice_id}/cancel")
def cancel_invoice(invoice_id: uuid.UUID, db: Session = Depends(get_company_db),
                   user: User = Depends(require_permission("sales.manage"))):
    try:
        inv = inv_svc.cancel_yarn_invoice(db, user.company_id, invoice_id, cancelled_by=user.id)
        db.commit()
    except inv_svc.YarnInvoiceError as exc:
        db.rollback()
        code = status.HTTP_404_NOT_FOUND if "not found" in str(exc).lower() else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(code, str(exc))
    return _inv_out(db, inv)


@router.post("/sales-invoices/{invoice_id}/post")
def post_invoice(invoice_id: uuid.UUID, db: Session = Depends(get_company_db),
                 user: User = Depends(require_permission("voucher.post"))):
    """Post a DRAFT yarn invoice: revenue + output GST + customer receivable in one
    balanced voucher. Blocked (409) when YARN_SALES mappings or GST place-of-supply
    inputs are missing — no voucher, invoice stays DRAFT."""
    try:
        res = inv_svc.post_yarn_invoice(db, user.company_id, invoice_id, posted_by=user.id)
        db.commit()
    except inv_svc.YarnInvoicePostingBlocked as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except inv_svc.YarnInvoiceInconsistent as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except inv_svc.YarnInvoiceError as exc:
        db.rollback()
        code = status.HTTP_404_NOT_FOUND if "not found" in str(exc).lower() else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(code, str(exc))
    inv = inv_svc.get_yarn_invoice(db, user.company_id, invoice_id)
    out = _inv_out(db, inv)
    v = res.get("voucher")
    out["voucher_number"] = getattr(v, "number", None)
    out["already_posted"] = res.get("already_posted", False)
    return out
