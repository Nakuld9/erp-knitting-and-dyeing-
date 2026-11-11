"""Generate and cancel e-way bills for sales invoices.

One EWB per invoice, idempotent. If the invoice already has an IRN, it is linked
on the payload. The request/response and every attempt are persisted (an
``eway_bills`` row + an ``integration_events`` audit record), mirroring the
e-invoice pipeline.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings as app_settings
from app.models.commercial import DocStatus, SalesInvoice
from app.models.dispensing import IntegrationEvent
from app.models.einvoice import EInvoice, EInvoiceStatus
from app.models.ewaybill import EWayBill, EWayBillStatus
from app.services.ewaybill.adapter import EWBError, get_adapter
from app.services.ewaybill.payload import EWBPayloadError, Transport, build_ewb_payload


class EWayBillServiceError(Exception):
    """A business-rule failure (invoice not eligible, already cancelled, ...)."""


def _event(db: Session, company_id: uuid.UUID, direction: str, ext_id: str,
           payload: dict | str, status: str, error: str | None = None) -> None:
    body = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    db.add(IntegrationEvent(
        company_id=company_id, direction=direction, external_txn_id=ext_id,
        payload=body, status=status, error=error,
        processed_at=datetime.now(timezone.utc) if status != "PENDING" else None))


def get_eway_bill(db: Session, invoice_id: uuid.UUID) -> EWayBill | None:
    return db.execute(select(EWayBill).where(EWayBill.invoice_id == invoice_id)).scalar_one_or_none()


def generate_eway_bill(db: Session, invoice_id: uuid.UUID, transport: Transport,
                       *, settings=None) -> EWayBill:
    settings = settings or app_settings
    invoice = db.get(SalesInvoice, invoice_id)
    if invoice is None:
        raise EWayBillServiceError("Sales invoice not found")
    if invoice.status in (DocStatus.DRAFT, DocStatus.CANCELLED):
        raise EWayBillServiceError("Only a finalised (posted) invoice can have an e-way bill")

    existing = get_eway_bill(db, invoice_id)
    if existing and existing.status == EWayBillStatus.GENERATED:
        return existing                                  # idempotent
    if existing and existing.status == EWayBillStatus.CANCELLED:
        raise EWayBillServiceError("This invoice's e-way bill was cancelled; issue a fresh one")

    rec = existing or EWayBill(company_id=invoice.company_id, invoice_id=invoice_id,
                               status=EWayBillStatus.PENDING)
    if existing is None:
        db.add(rec)

    ei = db.execute(select(EInvoice).where(EInvoice.invoice_id == invoice_id)).scalar_one_or_none()
    irn = ei.irn if ei and ei.status == EInvoiceStatus.GENERATED else None

    adapter = get_adapter(settings)
    rec.gsp = adapter.name
    rec.trans_mode = transport.trans_mode
    rec.vehicle_no = (transport.vehicle_no or "").replace(" ", "").upper() or None
    rec.transporter_id = transport.transporter_id
    rec.distance_km = int(transport.distance_km or 0)
    rec.irn = irn
    try:
        payload = build_ewb_payload(db, invoice, transport, irn=irn)
        rec.request_payload = json.dumps(payload, default=str)
        _event(db, invoice.company_id, "OUTBOUND", f"EWB:{invoice.number}", payload, "PENDING")
        result = adapter.generate_ewb(payload)
    except Exception as exc:  # noqa: BLE001
        rec.status = EWayBillStatus.FAILED
        rec.error = str(exc)
        _event(db, invoice.company_id, "OUTBOUND", f"EWB:{invoice.number}:err",
               {"error": str(exc)}, "FAILED", error=str(exc))
        db.flush()
        if isinstance(exc, (EWBError, EWBPayloadError)):
            raise EWayBillServiceError(str(exc)) from exc
        raise

    rec.status = EWayBillStatus.GENERATED
    rec.ewb_no = result.ewb_no
    rec.ewb_date = result.ewb_date
    rec.valid_until = result.valid_until
    rec.response_payload = json.dumps(result.raw, default=str)
    rec.error = None
    _event(db, invoice.company_id, "INBOUND", f"EWB:{result.ewb_no}", result.raw, "PROCESSED")
    db.flush()
    return rec


def cancel_eway_bill(db: Session, invoice_id: uuid.UUID, *, reason_code: str = "2",
                     remarks: str = "Cancelled", settings=None) -> EWayBill:
    settings = settings or app_settings
    rec = get_eway_bill(db, invoice_id)
    if rec is None or rec.status != EWayBillStatus.GENERATED:
        raise EWayBillServiceError("No active e-way bill to cancel for this invoice")

    adapter = get_adapter(settings)
    try:
        resp = adapter.cancel_ewb(rec.ewb_no, reason_code=reason_code, remarks=remarks)
    except EWBError as exc:
        raise EWayBillServiceError(str(exc)) from exc

    rec.status = EWayBillStatus.CANCELLED
    rec.cancelled_at = datetime.now(timezone.utc)
    rec.cancel_reason = remarks[:200]
    _event(db, rec.company_id, "OUTBOUND", f"EWBCANCEL:{rec.ewb_no}", resp, "PROCESSED")
    db.flush()
    return rec
