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
