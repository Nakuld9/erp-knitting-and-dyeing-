"""E-way bill endpoints: generate (with transport), cancel, read status."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import require_permission
from app.models.core import User
from app.models.ewaybill import EWayBill
from app.services.ewaybill import service as svc
from app.services.ewaybill.payload import Transport

router = APIRouter(prefix="/api/ewaybill", tags=["ewaybill"])


def _out(rec: EWayBill) -> dict:
    return {"invoice_id": str(rec.invoice_id),
            "status": rec.status.value if hasattr(rec.status, "value") else rec.status,
            "ewb_no": rec.ewb_no, "ewb_date": rec.ewb_date, "valid_until": rec.valid_until,
            "irn": rec.irn, "trans_mode": rec.trans_mode, "vehicle_no": rec.vehicle_no,
            "distance_km": rec.distance_km, "gsp": rec.gsp, "error": rec.error}


class TransportIn(BaseModel):
    trans_mode: str = "1"                 # 1 Road, 2 Rail, 3 Air, 4 Ship
    vehicle_no: str | None = None
    distance_km: int = 0
    transporter_id: str | None = None
    transporter_name: str | None = None
    trans_doc_no: str | None = None
    trans_doc_date: str | None = None     # dd/mm/yyyy
    vehicle_type: str = "R"


@router.get("/config")
def ewb_config(user: User = Depends(require_permission("sales.manage"))):
    return {"mode": settings.einvoice_gsp, "live": settings.einvoice_gsp.lower() == "gsp",
            "configured": bool(settings.einvoice_base_url and settings.einvoice_username),
            "threshold": settings.ewb_threshold}


@router.post("/{invoice_id}/generate", status_code=status.HTTP_201_CREATED)
def generate(invoice_id: uuid.UUID, payload: TransportIn, db: Session = Depends(get_db),
             user: User = Depends(require_permission("sales.manage"))):
    transport = Transport(**payload.model_dump())
    try:
        rec = svc.generate_eway_bill(db, invoice_id, transport)
        db.commit()
    except svc.EWayBillServiceError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return _out(rec)


class CancelIn(BaseModel):
    reason_code: str = "2"     # 1 Duplicate, 2 Order cancelled, 3 Data entry mistake, 4 Others
    remarks: str = "Cancelled"


@router.post("/{invoice_id}/cancel")
def cancel(invoice_id: uuid.UUID, payload: CancelIn, db: Session = Depends(get_db),
           user: User = Depends(require_permission("sales.manage"))):
    try:
        rec = svc.cancel_eway_bill(db, invoice_id, reason_code=payload.reason_code,
                                   remarks=payload.remarks)
        db.commit()
    except svc.EWayBillServiceError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return _out(rec)


@router.get("/{invoice_id}")
def get_status(invoice_id: uuid.UUID, db: Session = Depends(get_db),
               user: User = Depends(require_permission("report.read"))):
    rec = svc.get_eway_bill(db, invoice_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No e-way bill for this invoice")
    return _out(rec)
