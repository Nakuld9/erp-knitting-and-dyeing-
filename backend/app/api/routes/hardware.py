"""Weighing-scale & knitting-machine integration routes (+ device masters).

The ``simulate/*`` endpoints drive the bundled simulator for development; a
verified vendor gateway posts to the ingest endpoints in production.
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
from app.models.core import User
from app.models.hardware import KnittingMachineLog, ScaleReading, WeighingScale
from app.services import hardware

router = APIRouter(prefix="/api/hardware", tags=["hardware"])


def _s(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _s(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_s(v) for v in obj]
    return obj


# --- device master: weighing scales ----------------------------------------

class ScaleIn(BaseModel):
    code: str
    name: str
    location: str | None = None
    capacity_kg: Decimal | None = None


@router.get("/scales")
def list_scales(db: Session = Depends(get_company_db), user: User = Depends(require_permission("master.read"))):
    rows = db.execute(select(WeighingScale).where(WeighingScale.company_id == user.company_id)
                      .order_by(WeighingScale.code)).scalars().all()
    return [{"id": str(s.id), "code": s.code, "name": s.name, "location": s.location} for s in rows]


@router.post("/scales", status_code=status.HTTP_201_CREATED)
def create_scale(payload: ScaleIn, db: Session = Depends(get_company_db),
                 user: User = Depends(require_permission("master.write"))):
    s = WeighingScale(company_id=user.company_id, created_by=user.id, **payload.model_dump())
    db.add(s); db.commit()
    return {"id": str(s.id), "code": s.code}


# --- scale readings ---------------------------------------------------------

class ScaleReadingIn(BaseModel):
    external_reading_id: str
    scale_code: str
    weight: Decimal
    unit: str = "KG"
    stable: bool = True
    reference_type: str | None = None
    reference_id: str | None = None
    operator: str | None = None


@router.post("/scale-readings")
def ingest_scale(readings: list[ScaleReadingIn], db: Session = Depends(get_company_db),
                 user: User = Depends(require_permission("dispensing.operate"))):
    msgs = [hardware.ScaleReadingMsg(**r.model_dump()) for r in readings]
    result = hardware.ingest_scale_readings(db, user.company_id, msgs)
    db.commit()
    return result


@router.get("/scale-readings")
def list_scale_readings(db: Session = Depends(get_company_db), user: User = Depends(require_permission("report.read"))):
    rows = db.execute(select(ScaleReading).where(ScaleReading.company_id == user.company_id)
                      .order_by(ScaleReading.captured_at.desc())).scalars().all()
    return [{"id": str(r.id), "external_reading_id": r.external_reading_id, "weight": str(r.weight),
             "unit": r.unit, "reference_type": r.reference_type, "stable": r.stable,
             "captured_at": r.captured_at.isoformat() if r.captured_at else None} for r in rows[:100]]


class SimScaleIn(BaseModel):
    scale_code: str = "SCALE-01"
    weight: Decimal
    reference_type: str | None = None
    reference_id: str | None = None
    seq: int = 1


@router.post("/simulate/scale")
def simulate_scale(payload: SimScaleIn, db: Session = Depends(get_company_db),
                   user: User = Depends(require_permission("dispensing.operate"))):
    sim = hardware.WeighingScaleSimulator(payload.scale_code)
    msg = sim.read(payload.weight, reference_type=payload.reference_type,
                   reference_id=payload.reference_id, seq=payload.seq)
    result = hardware.ingest_scale_readings(db, user.company_id, [msg])
    db.commit()
    return {**result, "external_reading_id": msg.external_reading_id}


# --- knitting machine logs --------------------------------------------------

class KnittingLogIn(BaseModel):
    external_log_id: str
    machine_code: str
    status: str = "RUNNING"
    produced_kg: Decimal = Decimal("0")
    rpm: Decimal | None = None
    run_minutes: int = 0
    downtime_minutes: int = 0
    stop_reason: str | None = None
    operator: str | None = None


@router.post("/knitting-logs")
def ingest_logs(logs: list[KnittingLogIn], db: Session = Depends(get_company_db),
                user: User = Depends(require_permission("knitting.manage"))):
    msgs = [hardware.KnittingLogMsg(**g.model_dump()) for g in logs]
    result = hardware.ingest_knitting_logs(db, user.company_id, msgs)
    db.commit()
    return result


class SimKnitIn(BaseModel):
    machine_code: str
    samples: int = Field(default=4, ge=1, le=50)


@router.post("/simulate/knitting")
def simulate_knitting(payload: SimKnitIn, db: Session = Depends(get_company_db),
                      user: User = Depends(require_permission("knitting.manage"))):
    sim = hardware.KnittingMachineSimulator(payload.machine_code)
    result = hardware.ingest_knitting_logs(db, user.company_id, sim.generate(payload.samples))
    db.commit()
    return result


@router.get("/knitting-summary")
def knitting_summary(db: Session = Depends(get_company_db), user: User = Depends(require_permission("report.read"))):
    return _s(hardware.knitting_machine_summary(db, user.company_id))
