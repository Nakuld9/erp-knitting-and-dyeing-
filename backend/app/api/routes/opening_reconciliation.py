"""Opening reconciliation API (Stage 6F-P6).

Company- and factory-scoped, read/compute-only reconciliation: run a reconciliation,
review runs and discrepancies (with history), resolve/reopen discrepancies, drive the
status lifecycle, view the consolidated dashboard + cutover readiness, and render the
17 reports as JSON / Excel / PDF.

Permissions: ``opening.read`` for views, ``opening.reconcile`` to run, ``opening.manage``
to transition run status, ``opening.discrepancy_resolve`` to resolve/reopen. Backend
RBAC + company scoping are authoritative. Reconciliation posts nothing.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.deps import get_company_db, require_permission
from app.models.core import User
from app.services import opening_reconciliation as recon
from app.services import opening_recon_reports as reports

router = APIRouter(prefix="/api/opening/reconciliation", tags=["opening-reconciliation"])
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _raise(exc: Exception):
    msg = str(exc)
    if isinstance(exc, recon.OpeningReconStateError):
        raise HTTPException(status.HTTP_409_CONFLICT, msg)
    if msg.strip().lower().endswith("not found"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, msg)
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, msg)


def _row(obj) -> dict:
    out = {}
    for col in obj.__table__.columns:
        v = getattr(obj, col.name)
        if v is None:
            out[col.name] = None
        elif isinstance(v, uuid.UUID):
            out[col.name] = str(v)
        elif isinstance(v, (date, datetime)):
            out[col.name] = v.isoformat()
        else:
            out[col.name] = str(v) if type(v).__name__ == "Decimal" else v
    return out


class RunIn(_Strict):
    recon_type: str
    effective_date: date | None = None
    factory_id: uuid.UUID | None = None


@router.post("/runs", status_code=status.HTTP_201_CREATED)
def run(payload: RunIn, db: Session = Depends(get_company_db),
        user: User = Depends(require_permission("opening.reconcile"))):
    try:
        r = recon.run_reconciliation(db, user.company_id, recon_type=payload.recon_type,
                                     effective_date=payload.effective_date,
                                     factory_id=payload.factory_id, actor=user.id)
        db.commit()
    except recon.OpeningReconError as exc:
        db.rollback()
        _raise(exc)
    return _row(r)


@router.get("/runs")
def list_runs(recon_type: str | None = None, limit: int = 50, offset: int = 0,
              db: Session = Depends(get_company_db),
              user: User = Depends(require_permission("opening.read"))):
    rows = recon.list_runs(db, user.company_id, recon_type=recon_type, limit=limit, offset=offset)
    return {"items": [_row(r) for r in rows]}


@router.get("/runs/{run_id}")
def get_run(run_id: uuid.UUID, db: Session = Depends(get_company_db),
            user: User = Depends(require_permission("opening.read"))):
    try:
        return _row(recon.get_run(db, user.company_id, run_id))
    except recon.OpeningReconError as exc:
        _raise(exc)


class StatusIn(_Strict):
    to_status: str


@router.post("/runs/{run_id}/status")
def set_status(run_id: uuid.UUID, payload: StatusIn, db: Session = Depends(get_company_db),
               user: User = Depends(require_permission("opening.manage"))):
    try:
        r = recon.set_run_status(db, user.company_id, run_id, to_status=payload.to_status,
                                 actor=user.id)
        db.commit()
    except recon.OpeningReconError as exc:
        db.rollback()
        _raise(exc)
    return _row(r)


@router.get("/discrepancies")
def list_discrepancies(run_id: uuid.UUID | None = None, resolution_status: str | None = None,
                       category: str | None = None, limit: int = 500, offset: int = 0,
                       db: Session = Depends(get_company_db),
                       user: User = Depends(require_permission("opening.read"))):
    rows = recon.list_discrepancies(db, user.company_id, run_id=run_id,
                                    resolution_status=resolution_status, category=category,
                                    limit=limit, offset=offset)
    return {"items": [_row(r) for r in rows]}


@router.get("/discrepancies/history")
def discrepancy_history(discrepancy_key: str, db: Session = Depends(get_company_db),
                        user: User = Depends(require_permission("opening.read"))):
    rows = recon.discrepancy_history(db, user.company_id, discrepancy_key)
    return {"discrepancy_key": discrepancy_key, "history": [_row(r) for r in rows]}


class ResolveIn(_Strict):
    resolution_notes: str
    supporting_reference: str | None = None
    responsible_user: uuid.UUID | None = None


@router.post("/discrepancies/{discrepancy_id}/resolve")
def resolve(discrepancy_id: uuid.UUID, payload: ResolveIn, db: Session = Depends(get_company_db),
            user: User = Depends(require_permission("opening.discrepancy_resolve"))):
    try:
        d = recon.resolve_discrepancy(
            db, user.company_id, discrepancy_id, resolution_notes=payload.resolution_notes,
            supporting_reference=payload.supporting_reference,
            responsible_user=payload.responsible_user, actor=user.id)
        db.commit()
    except recon.OpeningReconError as exc:
        db.rollback()
        _raise(exc)
    return _row(d)


class ReopenIn(_Strict):
    reason: str


@router.post("/discrepancies/{discrepancy_id}/reopen")
def reopen(discrepancy_id: uuid.UUID, payload: ReopenIn, db: Session = Depends(get_company_db),
           user: User = Depends(require_permission("opening.discrepancy_resolve"))):
    try:
        d = recon.reopen_discrepancy(db, user.company_id, discrepancy_id, reason=payload.reason,
                                     actor=user.id)
        db.commit()
    except recon.OpeningReconError as exc:
        db.rollback()
        _raise(exc)
    return _row(d)


@router.get("/dashboard")
def dashboard(effective_date: date | None = None, factory_id: uuid.UUID | None = None,
              db: Session = Depends(get_company_db),
              user: User = Depends(require_permission("opening.read"))):
    return recon.dashboard(db, user.company_id, effective_date=effective_date, factory_id=factory_id)


@router.get("/cutover-readiness")
def cutover_readiness(effective_date: date | None = None, factory_id: uuid.UUID | None = None,
                      db: Session = Depends(get_company_db),
                      user: User = Depends(require_permission("opening.read"))):
    return recon.cutover_readiness(db, user.company_id, effective_date=effective_date,
                                   factory_id=factory_id)


@router.get("/reports/{report_key}")
def report(report_key: str, effective_date: date | None = None,
           factory_id: uuid.UUID | None = None, format: str = "json",
           db: Session = Depends(get_company_db),
           user: User = Depends(require_permission("opening.read"))):
    try:
        rep = reports.build(db, user.company_id, report_key, effective_date=effective_date,
                            factory_id=factory_id)
    except recon.OpeningReconError as exc:
        _raise(exc)
    if format == "json":
        return rep
    if format == "excel":
        return Response(content=reports.to_excel(rep), media_type=_XLSX, headers={
            "Content-Disposition": f'attachment; filename="opening_{report_key}.xlsx"'})
    if format == "pdf":
        return Response(content=reports.to_pdf(rep), media_type="application/pdf", headers={
            "Content-Disposition": f'attachment; filename="opening_{report_key}.pdf"'})
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "format must be json, excel or pdf")
