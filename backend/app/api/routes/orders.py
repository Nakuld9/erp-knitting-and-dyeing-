"""Purchase- and sales-order lifecycle: placement, confirmation, dispatch.

The GRN endpoint lives in ``operations`` (it posts stock + GST); here we place
and confirm the orders those documents fulfil, and dispatch finished rolls.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_company_db
from app.core.deps import require_permission
from app.models.commercial import DeliveryNote, DocStatus, PurchaseOrder, SalesOrder
from app.models.core import User
from app.services import order_lifecycle as ol

router = APIRouter(prefix="/api", tags=["orders"])


# ------------------------------ purchase orders -----------------------------

class POLineIn(BaseModel):
    chemical_id: uuid.UUID | None = None
    description: str | None = None
    qty: Decimal
    rate: Decimal = Decimal("0")
    tax_code_id: uuid.UUID | None = None


class POIn(BaseModel):
    supplier_id: uuid.UUID
    number: str
    order_date: date
    lines: list[POLineIn] = Field(min_length=1)


def _po_out(db: Session, po: PurchaseOrder) -> dict:
    received = ol.po_received_map(db, po)
    return {
        "id": str(po.id), "number": po.number, "supplier_id": str(po.supplier_id),
        "order_date": po.order_date.isoformat(), "status": po.status,
        "lines": [
            {"id": str(ln.id), "line_no": ln.line_no, "chemical_id": str(ln.chemical_id) if ln.chemical_id else None,
             "description": ln.description, "qty": str(ln.qty), "rate": str(ln.rate),
             "received_qty": str(received.get(ln.id, Decimal("0")))}
            for ln in po.lines
        ],
    }


@router.post("/procurement/purchase-orders", status_code=status.HTTP_201_CREATED)
def create_po(payload: POIn, db: Session = Depends(get_company_db),
              user: User = Depends(require_permission("purchase.manage"))):
    po = ol.create_purchase_order(
        db, company_id=user.company_id, supplier_id=payload.supplier_id,
        number=payload.number, order_date=payload.order_date,
        lines=[ln.model_dump() for ln in payload.lines], created_by=user.id,
    )
    db.commit()
    return _po_out(db, po)


@router.post("/procurement/purchase-orders/{po_id}/confirm")
def confirm_po(po_id: uuid.UUID, db: Session = Depends(get_company_db),
               user: User = Depends(require_permission("purchase.manage"))):
    po = db.get(PurchaseOrder, po_id)
    if po is None or po.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Purchase order not found")
    try:
        ol.confirm_purchase_order(db, po)
    except ol.LifecycleError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    db.commit()
    return _po_out(db, po)


@router.post("/procurement/purchase-orders/{po_id}/submit-approval")
def submit_po_approval(po_id: uuid.UUID, db: Session = Depends(get_company_db),
                       user: User = Depends(require_permission("purchase.manage"))):
    """Route a PO through maker-checker: at/above the configured threshold it is
    held for approval (confirmed on approval); otherwise it confirms at once."""
    from app.services import approvals
    po = db.get(PurchaseOrder, po_id)
    if po is None or po.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Purchase order not found")
    if po.status != DocStatus.DRAFT:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"PO is {po.status}, not DRAFT")
    gross = sum((Decimal(str(ln.qty)) * Decimal(str(ln.rate)) for ln in po.lines), Decimal("0"))
    rule = approvals.rule_for(db, user.company_id, "PURCHASE_ORDER", gross)
    if rule is None:
        ol.confirm_purchase_order(db, po)
        db.commit()
        return {"status": po.status, "approval_required": False}
    title = f"Purchase order {po.number}: ₹{gross.quantize(Decimal('0.01')):,}"
    req = approvals.create_request(db, company_id=user.company_id, doc_type="PURCHASE_ORDER",
                                   doc_id=po.id, title=title,
                                   amount=gross, requested_by=user.id, rule=rule)
    db.commit()
    return {"status": po.status, "approval_required": True,
            "approval_request_id": str(req.id), "approvals_required": req.approvals_required}


@router.get("/procurement/purchase-orders")
def list_pos(db: Session = Depends(get_company_db),
             user: User = Depends(require_permission("purchase.manage"))):
    rows = db.execute(select(PurchaseOrder).where(PurchaseOrder.company_id == user.company_id)
                      .order_by(PurchaseOrder.order_date.desc())).scalars().all()
    return [{"id": str(p.id), "number": p.number, "status": p.status,
             "order_date": p.order_date.isoformat(), "lines": len(p.lines)} for p in rows[:200]]


@router.get("/procurement/purchase-orders/{po_id}")
def get_po(po_id: uuid.UUID, db: Session = Depends(get_company_db),
           user: User = Depends(require_permission("purchase.manage"))):
    po = db.get(PurchaseOrder, po_id)
    if po is None or po.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Purchase order not found")
    return _po_out(db, po)


# ------------------------------- sales orders -------------------------------

class SOLineIn(BaseModel):
    fabric_type_id: uuid.UUID | None = None
    shade_id: uuid.UUID | None = None
    description: str | None = None
    gsm: Decimal | None = None
    width_cm: Decimal | None = None
    qty_kg: Decimal
    qty_m: Decimal | None = None
    rate: Decimal = Decimal("0")
    tax_code_id: uuid.UUID | None = None


class SOIn(BaseModel):
    customer_id: uuid.UUID
    number: str
    order_date: date
    delivery_date: date | None = None
    lines: list[SOLineIn] = Field(min_length=1)


def _so_out(db: Session, so: SalesOrder) -> dict:
    return {
        "id": str(so.id), "number": so.number, "customer_id": str(so.customer_id),
        "order_date": so.order_date.isoformat(), "status": so.status,
        "ordered_kg": str(ol.so_ordered_kg(so)), "delivered_kg": str(ol.so_delivered_kg(db, so)),
        "lines": [
            {"id": str(ln.id), "line_no": ln.line_no,
             "fabric_type_id": str(ln.fabric_type_id) if ln.fabric_type_id else None,
             "description": ln.description, "qty_kg": str(ln.qty_kg), "rate": str(ln.rate)}
            for ln in so.lines
        ],
    }


@router.post("/sales/orders", status_code=status.HTTP_201_CREATED)
def create_so(payload: SOIn, db: Session = Depends(get_company_db),
              user: User = Depends(require_permission("sales.manage"))):
    so = ol.create_sales_order(
        db, company_id=user.company_id, customer_id=payload.customer_id,
        number=payload.number, order_date=payload.order_date, delivery_date=payload.delivery_date,
        lines=[ln.model_dump() for ln in payload.lines], created_by=user.id,
    )
    db.commit()
    return _so_out(db, so)


@router.post("/sales/orders/{so_id}/confirm")
def confirm_so(so_id: uuid.UUID, db: Session = Depends(get_company_db),
               user: User = Depends(require_permission("sales.manage"))):
    so = db.get(SalesOrder, so_id)
    if so is None or so.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sales order not found")
    try:
        ol.confirm_sales_order(db, so)
    except ol.LifecycleError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    db.commit()
    return _so_out(db, so)


@router.get("/sales/orders")
def list_sos(db: Session = Depends(get_company_db),
             user: User = Depends(require_permission("sales.manage"))):
    rows = db.execute(select(SalesOrder).where(SalesOrder.company_id == user.company_id)
                      .order_by(SalesOrder.order_date.desc())).scalars().all()
    return [{"id": str(s.id), "number": s.number, "status": s.status,
             "order_date": s.order_date.isoformat(), "lines": len(s.lines)} for s in rows[:200]]


@router.get("/sales/orders/{so_id}")
def get_so(so_id: uuid.UUID, db: Session = Depends(get_company_db),
           user: User = Depends(require_permission("sales.manage"))):
    so = db.get(SalesOrder, so_id)
    if so is None or so.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sales order not found")
    return _so_out(db, so)


# -------------------------------- deliveries --------------------------------

class DeliveryIn(BaseModel):
    customer_id: uuid.UUID
    sales_order_id: uuid.UUID | None = None
    number: str | None = None            # auto DC-<fy>-<seq> when omitted
    delivery_date: date
    roll_ids: list[uuid.UUID] = Field(min_length=1)


@router.post("/sales/deliveries", status_code=status.HTTP_201_CREATED)
def create_delivery(payload: DeliveryIn, db: Session = Depends(get_company_db),
                    user: User = Depends(require_permission("sales.manage"))):
    number = (payload.number or "").strip() or ol.auto_delivery_number(
        db, user.company_id, payload.delivery_date)
    try:
        res = ol.create_delivery(
            db, company_id=user.company_id, customer_id=payload.customer_id,
            sales_order_id=payload.sales_order_id, number=number,
            delivery_date=payload.delivery_date, roll_ids=payload.roll_ids, created_by=user.id,
        )
        db.commit()
    except ol.LifecycleError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    voucher = res.get("voucher")
    return {"delivery_id": res.get("delivery_id"), "number": res.get("number", number),
            "already_posted": res.get("already_posted", False),
            "rolls": res.get("rolls"), "total_qty_kg": str(res.get("total_qty_kg", "")),
            "cogs_value": str(res.get("cogs_value", "")),
            "voucher_number": voucher.number if voucher else None}


@router.get("/sales/deliveries")
def list_deliveries(db: Session = Depends(get_company_db),
                    user: User = Depends(require_permission("sales.manage"))):
    rows = db.execute(select(DeliveryNote).where(DeliveryNote.company_id == user.company_id)
                      .order_by(DeliveryNote.delivery_date.desc())).scalars().all()
    return [{"id": str(d.id), "number": d.number, "status": d.status,
             "delivery_date": d.delivery_date.isoformat(),
             "total_qty_kg": str(d.total_qty_kg), "cogs_value": str(d.cogs_value),
             "sales_order_id": str(d.sales_order_id) if d.sales_order_id else None,
             "rolls": len(d.lines)} for d in rows[:200]]
