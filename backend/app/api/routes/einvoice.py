"""E-invoice (IRN) endpoints: generate, cancel, and read status for an invoice."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import require_permission
from app.models.core import User
from app.models.einvoice import EInvoice
from app.services.einvoice import service as svc

router = APIRouter(prefix="/api/einvoice", tags=["einvoice"])


def _out(rec: EInvoice) -> dict:
    return {"invoice_id": str(rec.invoice_id), "status": rec.status.value if hasattr(rec.status, "value") else rec.status,
            "irn": rec.irn, "ack_no": rec.ack_no, "ack_date": rec.ack_date,
            "signed_qr": rec.signed_qr, "ewb_no": rec.ewb_no, "gsp": rec.gsp,
            "error": rec.error}


@router.get("/config")
def einvoice_config(user: User = Depends(require_permission("sales.manage"))):
    """What the server is wired to do — so the UI can show live-vs-simulator
    without ever exposing the credentials themselves."""
    return {"mode": settings.einvoice_gsp,
            "live": settings.einvoice_gsp.lower() == "gsp",
            "configured": bool(settings.einvoice_base_url and settings.einvoice_username)}


@router.post("/{invoice_id}/generate", status_code=status.HTTP_201_CREATED)
def generate(invoice_id: uuid.UUID, db: Session = Depends(get_db),
             user: User = Depends(require_permission("sales.manage"))):
    try:
        rec = svc.generate_einvoice(db, invoice_id)
        db.commit()
    except svc.EInvoiceServiceError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return _out(rec)


class CancelIn(BaseModel):
    reason_code: str = "1"           # 1=Duplicate, 2=Data entry error, 3=Order cancelled, 4=Other
    remarks: str = "Cancelled"


@router.post("/{invoice_id}/cancel")
def cancel(invoice_id: uuid.UUID, payload: CancelIn, db: Session = Depends(get_db),
           user: User = Depends(require_permission("sales.manage"))):
    try:
        rec = svc.cancel_einvoice(db, invoice_id, reason_code=payload.reason_code,
                                  remarks=payload.remarks)
        db.commit()
    except svc.EInvoiceServiceError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return _out(rec)


@router.get("/{invoice_id}")
def get_status(invoice_id: uuid.UUID, db: Session = Depends(get_db),
               user: User = Depends(require_permission("report.read"))):
    rec = svc.get_einvoice(db, invoice_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No e-invoice for this invoice")
    return _out(rec)
