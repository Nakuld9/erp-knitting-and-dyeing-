"""Job-work endpoints: create inward/outward orders, dispatch, and bill."""
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
from app.models.jobwork import JobWork
from app.models.masters import Customer, Supplier
from app.services import jobwork as svc

router = APIRouter(prefix="/api/jobwork", tags=["jobwork"])


def _out(db: Session, jw: JobWork) -> dict:
    model = Customer if jw.party_type == "customer" else Supplier
    party = db.get(model, jw.party_id)
    return {"id": str(jw.id), "number": jw.number,
            "direction": jw.direction.value if hasattr(jw.direction, "value") else jw.direction,
            "process": jw.process.value if hasattr(jw.process, "value") else jw.process,
            "order_date": jw.order_date.isoformat() if jw.order_date else None,
            "party": party.name if party else None, "party_type": jw.party_type,
            "description": jw.description, "qty_kg": str(jw.qty_kg), "rate": str(jw.rate),
            "taxable_value": str(jw.taxable_value), "tax_amount": str(jw.tax_amount),
            "total_value": str(jw.total_value),
            "status": jw.status.value if hasattr(jw.status, "value") else jw.status}


class JobWorkIn(BaseModel):
    number: str
    direction: str                       # INWARD | OUTWARD
    process: str = "DYEING"              # DYEING | FINISHING | KNITTING | OTHER
    party_id: uuid.UUID                  # customer (inward) or subcontractor supplier (outward)
    order_date: date
    qty_kg: Decimal
    rate: Decimal = Decimal("0")
    fabric_type_id: uuid.UUID | None = None
    description: str | None = None
    tax_code_id: uuid.UUID | None = None


@router.post("", status_code=status.HTTP_201_CREATED)
def create(payload: JobWorkIn, db: Session = Depends(get_db),
           user: User = Depends(require_permission("voucher.create"))):
    try:
        jw = svc.create(db, company_id=user.company_id, created_by=user.id, **payload.model_dump())
        db.commit()
    except (svc.JobWorkError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return _out(db, jw)


@router.post("/{jw_id}/dispatch")
def dispatch(jw_id: uuid.UUID, db: Session = Depends(get_db),
             user: User = Depends(require_permission("voucher.create"))):
    jw = db.get(JobWork, jw_id)
    if jw is None or jw.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job work not found")
    try:
        svc.dispatch(db, jw)
        db.commit()
    except svc.JobWorkError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return _out(db, jw)


@router.post("/{jw_id}/bill")
def bill(jw_id: uuid.UUID, db: Session = Depends(get_db),
         user: User = Depends(require_permission("voucher.create"))):
    jw = db.get(JobWork, jw_id)
    if jw is None or jw.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job work not found")
    try:
        voucher = svc.bill(db, jw, created_by=user.id)
        db.commit()
    except svc.JobWorkError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return {**_out(db, jw), "voucher_number": voucher.number if voucher else None}


@router.get("")
def list_jobwork(direction: str | None = None, db: Session = Depends(get_db),
                 user: User = Depends(require_permission("report.read"))):
    stmt = select(JobWork).where(JobWork.company_id == user.company_id)
    if direction:
        stmt = stmt.where(JobWork.direction == direction.upper())
    rows = db.execute(stmt.order_by(JobWork.created_at.desc())).scalars().all()
    return [_out(db, jw) for jw in rows[:200]]


@router.get("/{jw_id}")
def get_one(jw_id: uuid.UUID, db: Session = Depends(get_db),
            user: User = Depends(require_permission("report.read"))):
    jw = db.get(JobWork, jw_id)
    if jw is None or jw.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job work not found")
    return _out(db, jw)
