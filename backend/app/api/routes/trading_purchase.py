"""Trading-fabric purchase order + goods receipt API (Stage 4B-2).

Company scope comes from the authenticated session; a client cannot supply a
company id. Factory ids are authorised against the caller. Over-receipt beyond a
PO's outstanding quantity is honoured only when the caller holds the dedicated
'trading.over_receive' permission AND supplies a reason — the request cannot
self-authorise with a boolean.
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
from app.services import trading_purchase as tp

router = APIRouter(prefix="/api/trading", tags=["trading-purchase"])


def _authz_factory(db, user, factory_id):
    if factory_id is not None:
        try:
            authorize_factory(db, user, factory_id, user.company_id)
        except FactoryAuthError:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not authorized for this factory")


def _422(exc):
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))


# ------------------------------ purchase orders -----------------------------

class POLineIn(BaseModel):
    trading_item_id: uuid.UUID
    qty_ordered: Decimal
    rate: Decimal = Decimal("0")
    uom_id: uuid.UUID | None = None
    tax_code_id: uuid.UUID | None = None


class POIn(BaseModel):
    supplier_id: uuid.UUID
    number: str
    order_date: date
    expected_date: date | None = None
    factory_id: uuid.UUID | None = None
    remarks: str | None = None
    lines: list[POLineIn] = Field(min_length=1)


def _po_out(po) -> dict:
    return {
        "id": str(po.id), "number": po.number, "supplier_id": str(po.supplier_id),
        "factory_id": str(po.factory_id) if po.factory_id else None,
        "order_date": po.order_date.isoformat(), "status": po.status,
        "lines": [{"id": str(l.id), "line_no": l.line_no, "trading_item_id": str(l.trading_item_id),
                   "qty_ordered": str(l.qty_ordered), "qty_received": str(l.qty_received),
                   "qty_cancelled": str(l.qty_cancelled), "rate": str(l.rate)} for l in po.lines],
    }


@router.post("/purchase-orders", status_code=status.HTTP_201_CREATED)
def create_po(payload: POIn, db: Session = Depends(get_company_db),
              user: User = Depends(require_permission("purchase.manage"))):
    _authz_factory(db, user, payload.factory_id)
    try:
        po = tp.create_trading_po(db, user.company_id, created_by=user.id,
                                  lines=[ln.model_dump() for ln in payload.lines],
                                  **payload.model_dump(exclude={"lines"}))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Purchase order number already exists")
    except tp.TradingPurchaseError as exc:
        db.rollback(); _422(exc)
    return _po_out(po)


@router.get("/purchase-orders")
def list_pos(status_filter: str | None = None, db: Session = Depends(get_company_db),
             user: User = Depends(require_permission("purchase.manage"))):
    return [_po_out(p) for p in tp.list_trading_pos(db, user.company_id, status=status_filter)]


@router.get("/purchase-orders/{po_id}")
def get_po(po_id: uuid.UUID, db: Session = Depends(get_company_db),
           user: User = Depends(require_permission("purchase.manage"))):
    try:
        return _po_out(tp.get_trading_po(db, user.company_id, po_id))
    except tp.TradingPurchaseError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.get("/purchase-orders/{po_id}/outstanding")
def po_outstanding(po_id: uuid.UUID, db: Session = Depends(get_company_db),
                   user: User = Depends(require_permission("purchase.manage"))):
    try:
        po = tp.get_trading_po(db, user.company_id, po_id)
    except tp.TradingPurchaseError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return {"po_id": str(po.id), "status": po.status,
            "lines": [{**r, "ordered": str(r["ordered"]), "received": str(r["received"]),
                       "cancelled": str(r["cancelled"]), "outstanding": str(r["outstanding"])}
                      for r in tp.po_outstanding(po)]}


@router.post("/purchase-orders/{po_id}/approve")
def approve_po(po_id: uuid.UUID, db: Session = Depends(get_company_db),
               user: User = Depends(require_permission("purchase.manage"))):
    try:
        po = tp.approve_trading_po(db, user.company_id, po_id, approved_by=user.id)
        db.commit()
    except tp.TradingPurchaseError as exc:
        db.rollback(); _422(exc)
    return _po_out(po)


@router.post("/purchase-orders/{po_id}/cancel")
def cancel_po(po_id: uuid.UUID, db: Session = Depends(get_company_db),
              user: User = Depends(require_permission("purchase.manage"))):
    try:
        po = tp.cancel_trading_po(db, user.company_id, po_id, cancelled_by=user.id)
        db.commit()
    except tp.TradingPurchaseError as exc:
        db.rollback(); _422(exc)
    return _po_out(po)


# --------------------------------- GRNs -------------------------------------

class GRNLineIn(BaseModel):
    trading_item_id: uuid.UUID
    warehouse_id: uuid.UUID
    qty_received: Decimal
    qty_rejected: Decimal = Decimal("0")
    rate: Decimal = Decimal("0")
    uom_id: uuid.UUID | None = None
    tax_code_id: uuid.UUID | None = None
    purchase_order_line_id: uuid.UUID | None = None
    remarks: str | None = None


class GRNIn(BaseModel):
    supplier_id: uuid.UUID
    number: str
    grn_date: date
    supplier_invoice_no: str
    supplier_invoice_date: date | None = None
    purchase_order_id: uuid.UUID | None = None   # null => direct purchase
    factory_id: uuid.UUID | None = None
    remarks: str | None = None
    over_receive_reason: str | None = None
    lines: list[GRNLineIn] = Field(min_length=1)


class RejectIn(BaseModel):
    reason: str


def _grn_out(grn) -> dict:
    return {
        "id": str(grn.id), "number": grn.number, "supplier_id": str(grn.supplier_id),
        "source_type": grn.source_type, "status": grn.status,
        "purchase_order_id": str(grn.purchase_order_id) if grn.purchase_order_id else None,
        "supplier_invoice_no": grn.supplier_invoice_no,
        "taxable_value": str(grn.taxable_value), "tax_amount": str(grn.tax_amount),
        "total_value": str(grn.total_value), "is_over_received": grn.is_over_received,
        "posting_reference": grn.posting_reference,
        "lines": [{"id": str(l.id), "line_no": l.line_no, "trading_item_id": str(l.trading_item_id),
                   "warehouse_id": str(l.warehouse_id), "qty_received": str(l.qty_received),
                   "qty_rejected": str(l.qty_rejected), "rate": str(l.rate),
                   "taxable_value": str(l.taxable_value), "cgst": str(l.cgst), "sgst": str(l.sgst),
                   "igst": str(l.igst), "total_value": str(l.total_value)} for l in grn.lines],
    }


@router.post("/grns", status_code=status.HTTP_201_CREATED)
def create_grn(payload: GRNIn, db: Session = Depends(get_company_db),
               user: User = Depends(require_permission("purchase.manage"))):
    _authz_factory(db, user, payload.factory_id)
    over_ok = user.has_permission(tp.OVER_RECEIVE_PERMISSION)
    try:
        grn = tp.create_trading_grn(
            db, user.company_id, created_by=user.id,
            lines=[ln.model_dump() for ln in payload.lines],
            over_receive_authorized=over_ok, authorized_by=user.id,
            **payload.model_dump(exclude={"lines"}))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Duplicate GRN number or supplier invoice")
    except tp.TradingDuplicateInvoice as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except (tp.TradingOverReceiptError, tp.TradingPurchaseError) as exc:
        db.rollback(); _422(exc)
    return _grn_out(grn)


@router.get("/grns")
def list_grns(status_filter: str | None = None, supplier_id: uuid.UUID | None = None,
              db: Session = Depends(get_company_db),
              user: User = Depends(require_permission("purchase.manage"))):
    return [_grn_out(g) for g in tp.list_trading_grns(db, user.company_id, status=status_filter,
                                                      supplier_id=supplier_id)]


@router.get("/grns/{grn_id}")
def get_grn(grn_id: uuid.UUID, db: Session = Depends(get_company_db),
            user: User = Depends(require_permission("purchase.manage"))):
    try:
        return _grn_out(tp.get_trading_grn(db, user.company_id, grn_id))
    except tp.TradingPurchaseError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.post("/grns/{grn_id}/submit")
def submit_grn(grn_id: uuid.UUID, db: Session = Depends(get_company_db),
               user: User = Depends(require_permission("purchase.manage"))):
    try:
        grn = tp.submit_trading_grn(db, user.company_id, grn_id, submitted_by=user.id)
        db.commit()
    except tp.TradingPurchaseError as exc:
        db.rollback(); _422(exc)
    return _grn_out(grn)


@router.post("/grns/{grn_id}/approve")
def approve_grn(grn_id: uuid.UUID, db: Session = Depends(get_company_db),
                user: User = Depends(require_permission("voucher.post"))):
    try:
        res = tp.approve_trading_grn(db, user.company_id, grn_id, approved_by=user.id)
        db.commit()
    except tp.TradingPostingBlocked as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except (tp.TradingOverReceiptError, tp.TradingPurchaseError) as exc:
        db.rollback(); _422(exc)
    grn = tp.get_trading_grn(db, user.company_id, grn_id)
    out = _grn_out(grn)
    v = res.get("voucher")
    out["voucher_number"] = getattr(v, "number", None)
    out["already_approved"] = res.get("already_approved", False)
    return out


@router.post("/grns/{grn_id}/reject")
def reject_grn(grn_id: uuid.UUID, payload: RejectIn, db: Session = Depends(get_company_db),
               user: User = Depends(require_permission("purchase.manage"))):
    try:
        grn = tp.reject_trading_grn(db, user.company_id, grn_id, reason=payload.reason, rejected_by=user.id)
        db.commit()
    except tp.TradingPurchaseError as exc:
        db.rollback(); _422(exc)
    return _grn_out(grn)


@router.post("/grns/{grn_id}/cancel")
def cancel_grn(grn_id: uuid.UUID, db: Session = Depends(get_company_db),
               user: User = Depends(require_permission("purchase.manage"))):
    try:
        grn = tp.cancel_trading_grn(db, user.company_id, grn_id, cancelled_by=user.id)
        db.commit()
    except tp.TradingPurchaseError as exc:
        db.rollback(); _422(exc)
    return _grn_out(grn)
