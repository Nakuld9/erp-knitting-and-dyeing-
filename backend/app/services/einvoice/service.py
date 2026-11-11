"""Generate and cancel e-invoices (IRNs) for sales invoices.

One IRN per invoice, idempotent: a second generate returns the existing IRN
instead of calling the IRP again. The canonical payload, the IRP response and
every attempt are persisted (``einvoices`` row + an ``integration_events`` audit
record), so the exchange is reproducible and offline-tolerant — exactly as the
Color Service dispensing pipeline does.
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
from app.services.einvoice.adapter import EInvoiceError, get_adapter
from app.services.einvoice.payload import EInvoicePayloadError, build_irn_payload


class EInvoiceServiceError(Exception):
    """A business-rule failure (invoice not eligible, already cancelled, ...)."""


def _event(db: Session, company_id: uuid.UUID, direction: str, ext_id: str,
           payload: dict | str, status: str, error: str | None = None) -> None:
    body = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    db.add(IntegrationEvent(
        company_id=company_id, direction=direction, external_txn_id=ext_id,
        payload=body, status=status, error=error,
        processed_at=datetime.now(timezone.utc) if status != "PENDING" else None))


def get_einvoice(db: Session, invoice_id: uuid.UUID) -> EInvoice | None:
    return db.execute(select(EInvoice).where(EInvoice.invoice_id == invoice_id)).scalar_one_or_none()


def generate_einvoice(db: Session, invoice_id: uuid.UUID, *, settings=None) -> EInvoice:
    settings = settings or app_settings
    invoice = db.get(SalesInvoice, invoice_id)
    if invoice is None:
        raise EInvoiceServiceError("Sales invoice not found")
    if invoice.status in (DocStatus.DRAFT, DocStatus.CANCELLED):
        raise EInvoiceServiceError("Only a finalised (posted) invoice can be e-invoiced")

    existing = get_einvoice(db, invoice_id)
    if existing and existing.status == EInvoiceStatus.GENERATED:
        return existing                                  # idempotent: IRN already issued
    if existing and existing.status == EInvoiceStatus.CANCELLED:
        raise EInvoiceServiceError("This invoice's IRN was cancelled; issue a fresh invoice")

    rec = existing or EInvoice(company_id=invoice.company_id, invoice_id=invoice_id,
                               status=EInvoiceStatus.PENDING)
    if existing is None:
        db.add(rec)

    adapter = get_adapter(settings)
    rec.gsp = adapter.name
    try:
        payload = build_irn_payload(db, invoice)
        rec.request_payload = json.dumps(payload, default=str)
        _event(db, invoice.company_id, "OUTBOUND", f"IRN:{invoice.number}", payload, "PENDING")
        result = adapter.generate_irn(payload)
    except Exception as exc:  # noqa: BLE001 — record the failure, surface it
        rec.status = EInvoiceStatus.FAILED
        rec.error = str(exc)
        _event(db, invoice.company_id, "OUTBOUND", f"IRN:{invoice.number}:err",
               {"error": str(exc)}, "FAILED", error=str(exc))
        db.flush()
        if isinstance(exc, (EInvoiceError, EInvoicePayloadError)):
            raise EInvoiceServiceError(str(exc)) from exc
        raise

    rec.status = EInvoiceStatus.GENERATED
    rec.irn = result.irn
    rec.ack_no = result.ack_no
    rec.ack_date = result.ack_date
    rec.signed_qr = result.signed_qr
    rec.signed_invoice = result.signed_invoice or None
    rec.ewb_no = result.ewb_no
    rec.response_payload = json.dumps(result.raw, default=str)
    rec.error = None
    _event(db, invoice.company_id, "INBOUND", f"IRN:{result.irn}", result.raw, "PROCESSED")
    db.flush()
    return rec


def cancel_einvoice(db: Session, invoice_id: uuid.UUID, *, reason_code: str = "1",
                    remarks: str = "Cancelled", settings=None) -> EInvoice:
    settings = settings or app_settings
    rec = get_einvoice(db, invoice_id)
    if rec is None or rec.status != EInvoiceStatus.GENERATED:
        raise EInvoiceServiceError("No active IRN to cancel for this invoice")

    adapter = get_adapter(settings)
    try:
        resp = adapter.cancel_irn(rec.irn, reason_code=reason_code, remarks=remarks)
    except EInvoiceError as exc:
        raise EInvoiceServiceError(str(exc)) from exc

    rec.status = EInvoiceStatus.CANCELLED
    rec.cancelled_at = datetime.now(timezone.utc)
    rec.cancel_reason = remarks[:200]
    _event(db, rec.company_id, "OUTBOUND", f"CANCEL:{rec.irn}", resp, "PROCESSED")
    db.flush()
    return rec
