"""Capture ERP purchase invoices (read-only) into the ITC tracking register.

The ITC register (``ItcInvoiceStatus``) is a SEPARATE, read-only snapshot of the
books purchase documents — capturing it never modifies the purchase invoice or its
tax values. New rows start at ``BOOKED_NOT_IN_2B``; an existing row is left as-is
(its lifecycle is owned by the reconciliation / ITC services).
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.gst_itc import ItcInvoiceStatus, ItcStatus
from app.models.masters import Supplier
from app.services.audit import record_audit
from app.services.gst_portal.common import (
    GstItcError,
    m,
    make_books_key,
    norm_gstin,
    period_from_date,
    period_valid,
    record_status,
)


def capture_purchase(db: Session, company_id: uuid.UUID, *, gstin: str, supplier_gstin: str,
                     supplier_invoice_no: str, invoice_date: date | None, booked_period: str,
                     taxable_value, igst=0, cgst=0, sgst=0, cess=0, doc_type: str = "INV",
                     supplier_id: uuid.UUID | None = None, source_module: str | None = None,
                     source_id: str | None = None, actor: uuid.UUID | None = None) -> ItcInvoiceStatus:
    """Create (idempotently) one ITC-register row for a purchase document. Returns the
    existing row unchanged if already captured (same company + books_key)."""
    if not period_valid(booked_period):
        raise GstItcError("booked_period must be MMYYYY")
    books_key = make_books_key(supplier_gstin, supplier_invoice_no, doc_type)
    existing = db.execute(select(ItcInvoiceStatus).where(
        ItcInvoiceStatus.company_id == company_id,
        ItcInvoiceStatus.books_key == books_key)).scalar_one_or_none()
    if existing is not None:
        return existing
    inv = ItcInvoiceStatus(
        company_id=company_id, gstin=norm_gstin(gstin), books_key=books_key,
        source_module=source_module, source_id=source_id, supplier_id=supplier_id,
        supplier_gstin=norm_gstin(supplier_gstin), supplier_invoice_no=supplier_invoice_no,
        invoice_date=invoice_date, doc_type=(doc_type or "INV").upper(), booked_period=booked_period,
        taxable_value=m(taxable_value), igst=m(igst), cgst=m(cgst), sgst=m(sgst), cess=m(cess),
        status=ItcStatus.BOOKED_NOT_IN_2B.value, reversed_total=m(0), reclaimed_total=m(0),
        created_by=actor, updated_by=actor)
    db.add(inv)
    db.flush()
    record_status(db, inv, ItcStatus.BOOKED_NOT_IN_2B.value, period=booked_period,
                  reason="captured from books", reference=source_id, actor=actor)
    record_audit(db, action="GST_ITC_CAPTURED", entity="gst_itc_invoice_status",
                 entity_id=str(inv.id), actor_id=actor,
                 detail={"company_id": str(company_id), "books_key": books_key,
                         "booked_period": booked_period})
    return inv


def capture_from_purchase_register(db: Session, company_id: uuid.UUID, *, gstin: str,
                                   period: str, actor: uuid.UUID | None = None) -> dict:
    """Best-effort adapter: read the posted ERP purchase register for ``period`` and
    capture each supplier-GSTIN-bearing row into the ITC register (read-only).

    The ERP purchase register exposes the receipt document number (used as the books
    invoice reference) and the supplier GSTIN. Rows without a supplier GSTIN cannot be
    reconciled against portal data and are skipped. This is the integration point; a
    deployment that stores the supplier's own invoice number on the GRN would map that
    here instead of the receipt number."""
    from calendar import monthrange

    from app.services.accounting import gst as gst_reports

    if not period_valid(period):
        raise GstItcError("period must be MMYYYY")
    mm, yyyy = int(period[:2]), int(period[2:])
    d_from = date(yyyy, mm, 1)
    d_to = date(yyyy, mm, monthrange(yyyy, mm)[1])
    reg = gst_reports.purchase_register(db, company_id, d_from, d_to)
    captured, skipped = 0, 0
    for row in reg.get("rows", []):
        sup_gstin = row.get("gstin")
        if not sup_gstin:
            skipped += 1
            continue
        doc_type = "DN" if row.get("doc_type") == "DEBIT_NOTE" else "INV"
        capture_purchase(
            db, company_id, gstin=gstin, supplier_gstin=sup_gstin,
            supplier_invoice_no=row.get("document"), invoice_date=row.get("date"),
            booked_period=period, taxable_value=row.get("taxable"), igst=row.get("igst"),
            cgst=row.get("cgst"), sgst=row.get("sgst"), doc_type=doc_type,
            source_module="purchase_register", source_id=row.get("document"), actor=actor)
        captured += 1
    return {"period": period, "captured": captured, "skipped_no_gstin": skipped}
