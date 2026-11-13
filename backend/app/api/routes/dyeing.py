"""Dyeing jobs API — operational + actual cost tracking (chemical-BOM-free).

Company-scoped (`get_company_db`) and factory-authorized: a job owned by a
factory is only reachable by a user with access to that factory (or
`factory.access_all` / superuser). `factory_id` / `warehouse_id` from the client
are validated, never trusted.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import (
    FactoryAuthError,
    accessible_factory_ids,
    authorize_factory,
    authorize_warehouse,
    user_can_access_factory,
)
from app.core.deps import get_company_db, require_permission
from app.models.core import User
from app.models.dyeing_cost import DyeingJob
from app.services.dyeing import service as dyeing
from app.services.inventory import InsufficientStockError

router = APIRouter(prefix="/api/dyeing", tags=["dyeing"])


def _authz(db, user, *, factory_id=None, warehouse_id=None) -> None:
    try:
        authorize_factory(db, user, factory_id, user.company_id)
        authorize_warehouse(db, user, warehouse_id, user.company_id, factory_id)
    except FactoryAuthError as e:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND if e.not_found else status.HTTP_403_FORBIDDEN, str(e))


# --- schemas ----------------------------------------------------------------

class JobIn(BaseModel):
    number: str
    mode: str = "INTERNAL"
    grey_production_order_id: uuid.UUID | None = None
    sales_order_id: uuid.UUID | None = None
    customer_id: uuid.UUID | None = None
    shade_id: uuid.UUID | None = None
    dye_lot: str | None = None
    jobwork_supplier_id: uuid.UUID | None = None
    factory_id: uuid.UUID | None = None
    dye_date: date | None = None


class IssueGreyIn(BaseModel):
    source_production_order_id: uuid.UUID
    qty_kg: Decimal
    warehouse_id: uuid.UUID | None = None
    method: str = "FIFO"


class CostsIn(BaseModel):
    dyeing_charge: Decimal | None = None
    jobwork_charge: Decimal | None = None
    chemical_cost: Decimal | None = None
    finishing_cost: Decimal | None = None
    testing_cost: Decimal | None = None
    other_cost: Decimal | None = None
    reprocess_cost: Decimal | None = None


class OutputLot(BaseModel):
    lot_code: str
    qty_kg: Decimal
    shade_id: uuid.UUID | None = None
    warehouse_id: uuid.UUID | None = None


class OutputIn(BaseModel):
    lots: list[OutputLot]


class CompleteIn(BaseModel):
    rejection_kg: Decimal = Decimal("0")
    reprocess_qty_kg: Decimal = Decimal("0")
    process_loss_kg: Decimal | None = None
    warehouse_id: uuid.UUID | None = None


# --- serialization / access -------------------------------------------------

def _job_dict(j: DyeingJob) -> dict:
    return {
        "id": str(j.id), "number": j.number,
        "mode": j.mode.value if hasattr(j.mode, "value") else j.mode,
        "status": j.status.value if hasattr(j.status, "value") else j.status,
        "factory_id": str(j.factory_id) if j.factory_id else None,
        "grey_production_order_id": str(j.grey_production_order_id) if j.grey_production_order_id else None,
        "shade_id": str(j.shade_id) if j.shade_id else None, "dye_lot": j.dye_lot,
        "input_qty_kg": str(j.input_qty_kg), "output_qty_kg": str(j.output_qty_kg),
        "grey_cost_consumed": str(j.grey_cost_consumed), "dyed_inventory_value": str(j.dyed_inventory_value),
    }


def _get_job(db: Session, user: User, job_id: uuid.UUID) -> DyeingJob:
    j = db.get(DyeingJob, job_id)
    if j is None or j.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dyeing job not found")
    if not user_can_access_factory(db, user, j.factory_id, user.company_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not authorized for this job's factory")
    return j


def _d(v):
    return str(v) if isinstance(v, Decimal) else v


def _clean(d: dict) -> dict:
    return {k: (_d(v) if not isinstance(v, (dict, list)) else v) for k, v in d.items()}


# --- endpoints --------------------------------------------------------------

@router.get("/jobs")
def list_jobs(db: Session = Depends(get_company_db),
              user: User = Depends(require_permission("dyeing.manage"))):
    rows = db.execute(
        select(DyeingJob).where(DyeingJob.company_id == user.company_id).order_by(DyeingJob.number)
    ).scalars().all()
    allowed = accessible_factory_ids(db, user, user.company_id)
    if allowed is not None:
        rows = [j for j in rows if j.factory_id is None or j.factory_id in allowed]
    return [_job_dict(j) for j in rows]


@router.post("/jobs", status_code=status.HTTP_201_CREATED)
def create_job(payload: JobIn, db: Session = Depends(get_company_db),
               user: User = Depends(require_permission("dyeing.manage"))):
    _authz(db, user, factory_id=payload.factory_id)
    try:
        j = dyeing.create_job(db, company_id=user.company_id, created_by=user.id, **payload.model_dump())
        db.commit()
    except dyeing.DyeingError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    db.refresh(j)
    return _job_dict(j)


@router.get("/jobs/{job_id}")
def get_job(job_id: uuid.UUID, db: Session = Depends(get_company_db),
            user: User = Depends(require_permission("dyeing.manage"))):
    return _job_dict(_get_job(db, user, job_id))


@router.post("/jobs/{job_id}/issue-grey")
def issue_grey(job_id: uuid.UUID, payload: IssueGreyIn, db: Session = Depends(get_company_db),
               user: User = Depends(require_permission("dyeing.manage"))):
    j = _get_job(db, user, job_id)
    _authz(db, user, factory_id=j.factory_id, warehouse_id=payload.warehouse_id)
    try:
        inp = dyeing.issue_grey(db, job_id=job_id, **payload.model_dump())
        db.commit()
    except InsufficientStockError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    except dyeing.DyeingError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    return {"id": str(inp.id), "qty_kg": str(inp.qty_kg), "cost": str(inp.cost)}


@router.post("/jobs/{job_id}/costs")
def record_costs(job_id: uuid.UUID, payload: CostsIn, db: Session = Depends(get_company_db),
                 user: User = Depends(require_permission("dyeing.manage"))):
    _get_job(db, user, job_id)
    try:
        dyeing.record_costs(db, job_id=job_id, **payload.model_dump(exclude_none=True))
        db.commit()
    except dyeing.DyeingError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    return _job_dict(_get_job(db, user, job_id))


@router.post("/jobs/{job_id}/output")
def record_output(job_id: uuid.UUID, payload: OutputIn, db: Session = Depends(get_company_db),
                  user: User = Depends(require_permission("dyeing.manage"))):
    _get_job(db, user, job_id)
    try:
        dyeing.record_output(db, job_id=job_id, lots=[l.model_dump() for l in payload.lots])
        db.commit()
    except dyeing.DyeingError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    return _job_dict(_get_job(db, user, job_id))


@router.post("/jobs/{job_id}/complete")
def complete_job(job_id: uuid.UUID, payload: CompleteIn, db: Session = Depends(get_company_db),
                 user: User = Depends(require_permission("dyeing.manage"))):
    j = _get_job(db, user, job_id)
    _authz(db, user, factory_id=j.factory_id, warehouse_id=payload.warehouse_id)
    try:
        dyeing.complete_job(db, job_id=job_id, **payload.model_dump())
        db.commit()
    except dyeing.DyeingError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    return _job_dict(_get_job(db, user, job_id))


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: uuid.UUID, db: Session = Depends(get_company_db),
               user: User = Depends(require_permission("dyeing.manage"))):
    _get_job(db, user, job_id)
    try:
        dyeing.cancel_job(db, job_id=job_id)
        db.commit()
    except dyeing.DyeingError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    return _job_dict(_get_job(db, user, job_id))


@router.get("/jobs/{job_id}/cost")
def dyed_cost(job_id: uuid.UUID, db: Session = Depends(get_company_db),
              user: User = Depends(require_permission("report.read"))):
    _get_job(db, user, job_id)
    res = dyeing.dyed_actual_cost(db, job_id)
    return res


@router.get("/jobs/{job_id}/trace")
def trace(job_id: uuid.UUID, db: Session = Depends(get_company_db),
          user: User = Depends(require_permission("report.read"))):
    _get_job(db, user, job_id)
    return dyeing.trace(db, job_id)


@router.get("/jobs/{job_id}/reconciliation")
def job_reconciliation(job_id: uuid.UUID, db: Session = Depends(get_company_db),
                       user: User = Depends(require_permission("report.read"))):
    _get_job(db, user, job_id)
    from app.services.accounting import production_reconciliation as prec
    return prec.dyeing_job_reconciliation(db, user.company_id, job_id)


@router.get("/reconciliation")
def production_reconciliation(db: Session = Depends(get_company_db),
                             user: User = Depends(require_permission("report.financial"))):
    """Inventory-to-GL reconciliation for the whole grey→dyed workflow (company)."""
    from app.services.accounting import production_reconciliation as prec
    return prec.production_ledger_reconciliation(db, user.company_id)
