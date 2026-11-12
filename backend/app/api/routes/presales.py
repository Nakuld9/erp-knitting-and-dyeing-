"""Pre-sales: enquiries, quotations, convert-to-SO."""
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
from app.models.commercial import DocStatus, Enquiry, Quotation
from app.models.core import User
from app.services import order_lifecycle as ol
from app.services import presales

router = APIRouter(prefix="/api/sales", tags=["presales"])


# -------------------------------- enquiries ---------------------------------

class EnquiryIn(BaseModel):
    customer_id: uuid.UUID
    number: str
    enquiry_date: date
    description: str | None = None


@router.post("/enquiries", status_code=status.HTTP_201_CREATED)
def create_enquiry(payload: EnquiryIn, db: Session = Depends(get_company_db),
                   user: User = Depends(require_permission("sales.manage"))):
    e = presales.create_enquiry(db, company_id=user.company_id, created_by=user.id, **payload.model_dump())
    db.commit()
    return {"id": str(e.id), "number": e.number, "status": e.status}


@router.get("/enquiries")
def list_enquiries(db: Session = Depends(get_company_db),
                   user: User = Depends(require_permission("sales.manage"))):
    rows = db.execute(select(Enquiry).where(Enquiry.company_id == user.company_id)
                      .order_by(Enquiry.enquiry_date.desc())).scalars().all()
    return [{"id": str(e.id), "number": e.number, "date": e.enquiry_date.isoformat(),
             "description": e.description, "status": e.status} for e in rows[:200]]


# -------------------------------- quotations --------------------------------

class QuoteLineIn(BaseModel):
    fabric_type_id: uuid.UUID | None = None
    description: str | None = None
    qty_kg: Decimal
    rate: Decimal
    tax_code_id: uuid.UUID | None = None


class QuoteIn(BaseModel):
    customer_id: uuid.UUID
    number: str
    quote_date: date
    enquiry_id: uuid.UUID | None = None
    valid_until: date | None = None
    lines: list[QuoteLineIn] = Field(min_length=1)


def _quote_out(q: Quotation) -> dict:
    total = sum((Decimal(str(ln.qty_kg)) * Decimal(str(ln.rate)) for ln in q.lines), Decimal("0"))
    return {"id": str(q.id), "number": q.number, "customer_id": str(q.customer_id),
            "date": q.quote_date.isoformat(), "status": q.status,
            "sales_order_id": str(q.sales_order_id) if q.sales_order_id else None,
            "value": str(total), "lines": len(q.lines)}


@router.post("/quotations", status_code=status.HTTP_201_CREATED)
def create_quotation(payload: QuoteIn, db: Session = Depends(get_company_db),
                     user: User = Depends(require_permission("sales.manage"))):
    q = presales.create_quotation(
        db, company_id=user.company_id, customer_id=payload.customer_id, number=payload.number,
        quote_date=payload.quote_date, enquiry_id=payload.enquiry_id, valid_until=payload.valid_until,
        lines=[ln.model_dump() for ln in payload.lines], created_by=user.id)
    db.commit()
    return _quote_out(q)


@router.get("/quotations")
def list_quotations(db: Session = Depends(get_company_db),
                    user: User = Depends(require_permission("sales.manage"))):
    rows = db.execute(select(Quotation).where(Quotation.company_id == user.company_id)
                      .order_by(Quotation.quote_date.desc())).scalars().all()
    return [_quote_out(q) for q in rows[:200]]


class ConvertIn(BaseModel):
    number: str
    order_date: date


@router.post("/quotations/{quote_id}/convert", status_code=status.HTTP_201_CREATED)
def convert_quotation(quote_id: uuid.UUID, payload: ConvertIn, db: Session = Depends(get_company_db),
                      user: User = Depends(require_permission("sales.manage"))):
    q = db.get(Quotation, quote_id)
    if q is None or q.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quotation not found")
    try:
        res = presales.convert_quotation_to_so(db, company_id=user.company_id, quotation=q,
                                                number=payload.number, order_date=payload.order_date,
                                                created_by=user.id)
        db.commit()
    except presales.PresalesError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return res
