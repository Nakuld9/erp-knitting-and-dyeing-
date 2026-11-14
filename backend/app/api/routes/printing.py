"""Printing framework API (Stage 2) — generic engine, demonstrated with a sample.

These endpoints exercise the reusable engine end to end WITHOUT implementing any
business document:

- preview            — render a PDF with no number allocated (watermarked PREVIEW);
- issue/print        — allocate a stable number on first print, reuse on reprint;
- download vs inline — attachment (download) vs inline (browser preview + print);
- cancel             — flip the print-layer flag so the PDF shows a CANCELLED mark.

All are scoped to the authenticated active company (``get_company_db``), so one
company never sees another's documents, branding or numbers. Rendering/printing
never writes inventory, accounting or production data.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_company_db, get_current_user
from app.models.core import User
from app.models.printing import PrintedDocument
from app.services.printing import numbering as num
from app.services.printing.pdf import render_pdf
from app.services.printing.samples import sample_print_document

router = APIRouter(prefix="/api/print", tags=["printing"])

SAMPLE_DOC_TYPE = "SAMPLE"
SAMPLE_SOURCE_TYPE = "sample"


def _pdf_response(pdf: bytes, *, filename: str, download: bool) -> Response:
    disp = "attachment" if download else "inline"
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'{disp}; filename="{filename}"',
                             "Cache-Control": "no-store"})


def _fmt_date(d: date) -> str:
    return d.strftime("%d-%b-%Y")


@router.get("/sample/preview")
def sample_preview(db: Session = Depends(get_company_db),
                   user: User = Depends(get_current_user)) -> Response:
    """Render a PREVIEW of the sample document. Allocates NO number."""
    doc = sample_print_document(db, user.company_id, number="(preview — not issued)",
                                date=_fmt_date(date.today()), is_preview=True)
    return _pdf_response(render_pdf(doc), filename="sample-preview.pdf", download=False)


class IssueIn(BaseModel):
    source_id: str


@router.post("/sample/issue", status_code=status.HTTP_201_CREATED)
def sample_issue(payload: IssueIn, db: Session = Depends(get_company_db),
                 user: User = Depends(get_current_user)) -> dict:
    """Allocate (or return) the stable number for a sample source. Idempotent."""
    fy = num.fiscal_year_for(date.today())
    row, created = num.issue_document(
        db, user.company_id, doc_type=SAMPLE_DOC_TYPE, source_type=SAMPLE_SOURCE_TYPE,
        source_id=payload.source_id, fiscal_year=fy, prefix="SMP-", issued_by=user.id)
    db.commit()
    return {"number": row.number, "created": created, "reprint_count": row.reprint_count,
            "cancelled": row.cancelled, "fiscal_year": row.fiscal_year}


@router.get("/sample/document")
def sample_document(source_id: str = Query(...), download: bool = Query(False),
                    db: Session = Depends(get_company_db),
                    user: User = Depends(get_current_user)) -> Response:
    """Print the sample document for a source: allocate a number on the first call,
    reuse it (and record a reprint) on every later call. Inline = browser preview /
    print; ``download=true`` = file download."""
    fy = num.fiscal_year_for(date.today())
    existing = num.get_issued(db, user.company_id, doc_type=SAMPLE_DOC_TYPE,
                              source_type=SAMPLE_SOURCE_TYPE, source_id=source_id)
    row, created = num.issue_document(
        db, user.company_id, doc_type=SAMPLE_DOC_TYPE, source_type=SAMPLE_SOURCE_TYPE,
        source_id=source_id, fiscal_year=fy, prefix="SMP-", issued_by=user.id)
    if not created:
        num.record_reprint(db, row, by=user.id)
    db.commit()
    doc = sample_print_document(
        db, user.company_id, number=row.number,
        date=_fmt_date(row.first_issued_at.date() if row.first_issued_at else date.today()),
        is_cancelled=row.cancelled, reprint_count=row.reprint_count)
    return _pdf_response(render_pdf(doc), filename=f"sample-{row.number}.pdf", download=download)


@router.post("/sample/cancel")
def sample_cancel(payload: IssueIn, db: Session = Depends(get_company_db),
                  user: User = Depends(get_current_user)) -> dict:
    """Mark a sample document cancelled (prints a CANCELLED watermark). Does not
    touch any business transaction."""
    row = num.get_issued(db, user.company_id, doc_type=SAMPLE_DOC_TYPE,
                         source_type=SAMPLE_SOURCE_TYPE, source_id=payload.source_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not issued yet")
    num.cancel_document(db, row, by=user.id)
    db.commit()
    return {"number": row.number, "cancelled": True}


@router.get("/registry")
def list_registry(db: Session = Depends(get_company_db),
                  user: User = Depends(get_current_user)) -> list[dict]:
    """List issued printed documents for the active company (print history)."""
    rows = db.execute(select(PrintedDocument).where(
        PrintedDocument.company_id == user.company_id).order_by(
        PrintedDocument.first_issued_at)).scalars().all()
    return [{"doc_type": r.doc_type, "number": r.number, "source_id": r.source_id,
             "fiscal_year": r.fiscal_year, "reprint_count": r.reprint_count,
             "cancelled": r.cancelled} for r in rows]


# --------------------------------------------------------------------------- #
# Stage 3 — Sales GST Invoice and Delivery Challan (render existing documents)
# --------------------------------------------------------------------------- #
from datetime import date as _date  # noqa: E402

from app.core.context import authorize_factory, FactoryAuthError  # noqa: E402
from app.models.commercial import DeliveryNote, SalesInvoice  # noqa: E402
from app.models.manufacturing import FabricRoll  # noqa: E402
from app.services.printing import sales_documents as sdoc  # noqa: E402

INVOICE_DOC_TYPE, INVOICE_SRC = "SALES_INVOICE", "sales_invoice"
CHALLAN_DOC_TYPE, CHALLAN_SRC = "DELIVERY_CHALLAN", "delivery_note"


def _track_and_count(db, user, *, doc_type, source_type, source_id, number, when) -> int:
    """Register the document's own number for print history (first print) or record
    a reprint; return the reprint count to stamp. Writes only the print registry +
    audit log — never a business transaction."""
    fy = num.fiscal_year_for(when or _date.today())
    row, created = num.register_existing(
        db, user.company_id, doc_type=doc_type, source_type=source_type,
        source_id=str(source_id), number=number, fiscal_year=fy, by=user.id)
    if not created:
        num.record_reprint(db, row, by=user.id)
    db.commit()
    return row.reprint_count


@router.get("/sales-invoice/{invoice_id}/preview")
def invoice_preview(invoice_id: uuid.UUID, db: Session = Depends(get_company_db),
                    user: User = Depends(get_current_user)) -> Response:
    """Preview the GST invoice (watermarked; allocates/records nothing)."""
    try:
        doc = sdoc.build_sales_invoice_document(db, user.company_id, invoice_id, is_preview=True)
    except sdoc.DocumentError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    return _pdf_response(render_pdf(doc), filename="invoice-preview.pdf", download=False)


@router.get("/sales-invoice/{invoice_id}")
def invoice_print(invoice_id: uuid.UUID, download: bool = Query(False),
                  db: Session = Depends(get_company_db),
                  user: User = Depends(get_current_user)) -> Response:
    """Print the GST invoice (inline = browser preview/print; download=true = file).
    Records print/reprint history; posts no accounting entry, creates no invoice."""
    inv = db.get(SalesInvoice, invoice_id)
    if inv is None or inv.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found in the active company")
    reprints = _track_and_count(db, user, doc_type=INVOICE_DOC_TYPE, source_type=INVOICE_SRC,
                                source_id=inv.id, number=inv.number, when=inv.invoice_date)
    doc = sdoc.build_sales_invoice_document(db, user.company_id, invoice_id, reprint_count=reprints)
    return _pdf_response(render_pdf(doc), filename=f"invoice-{inv.number}.pdf", download=download)


def _authorize_challan_factories(db, user, dn: DeliveryNote) -> None:
    """Respect factory permissions: block printing a challan whose rolls belong to
    a factory the user may not access."""
    for line in dn.lines:
        roll = db.get(FabricRoll, line.fabric_roll_id)
        if roll is not None and roll.factory_id is not None:
            try:
                authorize_factory(db, user, roll.factory_id, user.company_id)
            except FactoryAuthError as e:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND if e.not_found else status.HTTP_403_FORBIDDEN, e.detail)


@router.get("/delivery-challan/{delivery_id}/preview")
def challan_preview(delivery_id: uuid.UUID, db: Session = Depends(get_company_db),
                    user: User = Depends(get_current_user)) -> Response:
    dn = db.get(DeliveryNote, delivery_id)
    if dn is None or dn.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Delivery challan not found in the active company")
    _authorize_challan_factories(db, user, dn)
    doc = sdoc.build_delivery_challan_document(db, user.company_id, delivery_id, is_preview=True)
    return _pdf_response(render_pdf(doc), filename="challan-preview.pdf", download=False)


@router.get("/delivery-challan/{delivery_id}")
def challan_print(delivery_id: uuid.UUID, download: bool = Query(False),
                  db: Session = Depends(get_company_db),
                  user: User = Depends(get_current_user)) -> Response:
    """Print the delivery challan (actual dispatched rolls/qty for THIS challan).
    Records print/reprint history; changes no stock or ledger."""
    dn = db.get(DeliveryNote, delivery_id)
    if dn is None or dn.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Delivery challan not found in the active company")
    _authorize_challan_factories(db, user, dn)
    reprints = _track_and_count(db, user, doc_type=CHALLAN_DOC_TYPE, source_type=CHALLAN_SRC,
                                source_id=dn.id, number=dn.number, when=dn.delivery_date)
    doc = sdoc.build_delivery_challan_document(db, user.company_id, delivery_id, reprint_count=reprints)
    return _pdf_response(render_pdf(doc), filename=f"challan-{dn.number}.pdf", download=download)
