"""Finishing & quality routes."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import apply_factory_scope
from app.core.database import get_db
from app.core.deps import get_company_db
from app.core.deps import require_permission
from app.models.core import User
from app.models.quality import CustomerComplaint, FinishingTransaction, QualityInspection
from app.services import quality

router = APIRouter(prefix="/api/quality", tags=["quality"])


def _s(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _s(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_s(v) for v in obj]
    return obj


def _fy_start(d: date) -> date:
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1)


# ------------------------------ finishing ----------------------------------

class FinishingIn(BaseModel):
    process_type: str
    process_date: date
    fabric_roll_id: uuid.UUID | None = None
    dye_batch_id: uuid.UUID | None = None
    machine_id: uuid.UUID | None = None
    input_weight_kg: Decimal | None = None
    output_weight_kg: Decimal | None = None
    width_cm: Decimal | None = None
    gsm: Decimal | None = None
    shrinkage_pct: Decimal | None = None
    remarks: str | None = None


@router.post("/finishing", status_code=status.HTTP_201_CREATED)
def post_finishing(payload: FinishingIn, db: Session = Depends(get_company_db),
                   user: User = Depends(require_permission("finishing.manage"))):
    try:
        ft = quality.record_finishing(db, user.company_id, created_by=user.id, **payload.model_dump())
        db.commit()
    except (ValueError, KeyError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return {"id": str(ft.id), "number": ft.number, "shrinkage_pct": _s(ft.shrinkage_pct)}


@router.get("/finishing")
def list_finishing(db: Session = Depends(get_company_db), user: User = Depends(require_permission("report.read"))):
    stmt = apply_factory_scope(select(FinishingTransaction).where(
        FinishingTransaction.company_id == user.company_id), FinishingTransaction, user)
    rows = db.execute(stmt.order_by(FinishingTransaction.process_date.desc())).scalars().all()
    return [{"id": str(r.id), "number": r.number, "process_type": r.process_type,
             "process_date": r.process_date.isoformat(), "shrinkage_pct": _s(r.shrinkage_pct)} for r in rows]


# ----------------------------- inspections ---------------------------------

class InspectionIn(BaseModel):
    inspection_date: date
    fabric_roll_id: uuid.UUID | None = None
    dye_batch_id: uuid.UUID | None = None
    delta_e: Decimal | None = None
    l_star: Decimal | None = None
    a_star: Decimal | None = None
    b_star: Decimal | None = None
    shade_match: bool | None = None
    colour_fastness: str | None = None
    result: str | None = None
    inspected_qty: Decimal | None = None
    rejection_qty: Decimal | None = None
    remarks: str | None = None


@router.post("/inspections", status_code=status.HTTP_201_CREATED)
def post_inspection(payload: InspectionIn, db: Session = Depends(get_company_db),
                    user: User = Depends(require_permission("quality.manage"))):
    try:
        qi = quality.record_inspection(db, user.company_id, created_by=user.id, **payload.model_dump())
        db.commit()
    except (ValueError, KeyError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return {"id": str(qi.id), "number": qi.number, "result": qi.result}


@router.get("/inspections")
def list_inspections(db: Session = Depends(get_company_db), user: User = Depends(require_permission("report.read"))):
    stmt = apply_factory_scope(select(QualityInspection).where(
        QualityInspection.company_id == user.company_id), QualityInspection, user)
    rows = db.execute(stmt.order_by(QualityInspection.inspection_date.desc())).scalars().all()
    return [{"id": str(r.id), "number": r.number, "result": r.result, "delta_e": _s(r.delta_e),
             "shade_match": r.shade_match, "colour_fastness": r.colour_fastness,
             "inspection_date": r.inspection_date.isoformat()} for r in rows]


# ----------------------------- complaints ----------------------------------

class ComplaintIn(BaseModel):
    customer_id: uuid.UUID
    complaint_date: date
    description: str
    severity: str = "MEDIUM"
    reference: str | None = None


class ResolveIn(BaseModel):
    resolution: str


@router.post("/complaints", status_code=status.HTTP_201_CREATED)
def post_complaint(payload: ComplaintIn, db: Session = Depends(get_company_db),
                   user: User = Depends(require_permission("quality.manage"))):
    c = quality.raise_complaint(db, user.company_id, created_by=user.id, **payload.model_dump())
    db.commit()
    return {"id": str(c.id), "number": c.number, "status": c.status}


@router.post("/complaints/{complaint_id}/resolve")
def resolve_complaint(complaint_id: uuid.UUID, payload: ResolveIn, db: Session = Depends(get_company_db),
                      user: User = Depends(require_permission("quality.manage"))):
    c = db.get(CustomerComplaint, complaint_id)
    if c is None or c.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Complaint not found")
    quality.resolve_complaint(db, complaint_id, resolution=payload.resolution, created_by=user.id)
    db.commit()
    return {"id": str(complaint_id), "status": "RESOLVED"}


@router.get("/complaints")
def list_complaints(db: Session = Depends(get_company_db), user: User = Depends(require_permission("report.read"))):
    rows = db.execute(select(CustomerComplaint).where(
        CustomerComplaint.company_id == user.company_id).order_by(
        CustomerComplaint.complaint_date.desc())).scalars().all()
    return [{"id": str(r.id), "number": r.number, "severity": r.severity, "status": r.status,
             "description": r.description, "complaint_date": r.complaint_date.isoformat()} for r in rows]


@router.get("/summary")
def summary(date_from: date | None = None, date_to: date | None = None, db: Session = Depends(get_company_db),
            user: User = Depends(require_permission("report.read"))):
    to = date_to or date.today()
    frm = date_from or _fy_start(to)
    return _s(quality.quality_summary(db, user.company_id, frm, to))
