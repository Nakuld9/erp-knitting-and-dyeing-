"""Document numbering + print-history for the printing framework.

Guarantees:
- **Preview allocates nothing** — callers render a preview without touching this.
- **Issue on first print** — ``issue_document`` reserves a number the first time a
  given (company, doc_type, source) is issued, and is **idempotent**: a second
  call returns the same number (a reprint), never a new one.
- **Company / doc-type / FY-aware** numbers via ``sequences.next_document_number``.
- **Concurrency-safe** — the sequence row is locked during allocation and a unique
  constraint on (company, doc_type, source) makes a duplicate physically
  impossible; a racing inserter re-reads the winner's row.
- **Audit history** — every issue and every reprint appends an immutable
  ``AuditLog`` row; the registry row also carries ``reprint_count`` and timestamps.

None of this writes any inventory, accounting or production data.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.branding import CompanyBranding
from app.models.core import Company
from app.models.printing import PrintedDocument
from app.services.printing.document import CompanyLetterhead
from app.services.sequences import next_document_number


def fiscal_year_for(d: date, start_month: int = 4) -> str:
    """Indian FY string, e.g. 2026-2027, for a date (FY starts in April)."""
    y = d.year if d.month >= start_month else d.year - 1
    return f"{y}-{y + 1}"


def build_letterhead(db: Session, company_id: uuid.UUID) -> CompanyLetterhead:
    """Active-company letterhead from core.Company (identity) + CompanyBranding
    (logo + extra contact). Reused by every document."""
    company = db.get(Company, company_id)
    brand = db.execute(
        select(CompanyBranding).where(CompanyBranding.company_id == company_id)
    ).scalar_one_or_none()
    return CompanyLetterhead(
        legal_name=(company.legal_name or company.name) if company else "—",
        address=company.address if company else None,
        gstin=company.gstin if company else None,
        state_code=company.state_code if company else None,
        cin=brand.cin if brand else None,
        phone=brand.phone if brand else None,
        email=brand.email if brand else None,
        website=brand.website if brand else None,
        logo_bytes=brand.logo_bytes if brand else None,
        logo_mime=brand.logo_mime_type if brand else None,
    )


def _audit(db: Session, company_id, actor, action: str, number: str, doc_type: str):
    db.add(AuditLog(actor_id=actor, action=action, entity=f"printed_document:{doc_type}",
                    entity_id=number, detail=f'{{"company_id":"{company_id}"}}'))


def get_issued(db: Session, company_id: uuid.UUID, *, doc_type: str,
               source_type: str, source_id: str) -> PrintedDocument | None:
    return db.execute(select(PrintedDocument).where(
        PrintedDocument.company_id == company_id,
        PrintedDocument.doc_type == doc_type,
        PrintedDocument.source_type == source_type,
        PrintedDocument.source_id == str(source_id),
    )).scalar_one_or_none()


def issue_document(db: Session, company_id: uuid.UUID, *, doc_type: str, source_type: str,
                   source_id: str, fiscal_year: str, prefix: str = "",
                   issued_by: uuid.UUID | None = None) -> tuple[PrintedDocument, bool]:
    """Reserve (or return the existing) stable number for a source document.

    Returns ``(row, created)``. ``created`` is True only on the first issue.
    """
    existing = get_issued(db, company_id, doc_type=doc_type, source_type=source_type,
                          source_id=str(source_id))
    if existing is not None:
        return existing, False

    number = next_document_number(db, company_id=company_id, doc_type=doc_type,
                                  fiscal_year=fiscal_year, prefix=prefix)
    now = datetime.now(timezone.utc)
    row = PrintedDocument(
        company_id=company_id, doc_type=doc_type, source_type=source_type,
        source_id=str(source_id), fiscal_year=fiscal_year, number=number,
        first_issued_at=now, last_printed_at=now, issued_by=issued_by, created_by=issued_by,
    )
    db.add(row)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        # A concurrent request won the race — re-read and return its row (reprint).
        db.rollback()
        existing = get_issued(db, company_id, doc_type=doc_type, source_type=source_type,
                              source_id=str(source_id))
        if existing is not None:
            return existing, False
        raise
    _audit(db, company_id, issued_by, "DOC_ISSUE", number, doc_type)
    db.flush()
    return row, True


def record_reprint(db: Session, row: PrintedDocument,
                   by: uuid.UUID | None = None) -> PrintedDocument:
    """Record a reprint of an already-issued document (keeps the same number)."""
    row.reprint_count = (row.reprint_count or 0) + 1
    row.last_printed_at = datetime.now(timezone.utc)
    row.updated_by = by
    _audit(db, row.company_id, by, "DOC_REPRINT", row.number, row.doc_type)
    db.flush()
    return row


def cancel_document(db: Session, row: PrintedDocument,
                    by: uuid.UUID | None = None) -> PrintedDocument:
    """Mark a printed document cancelled (prints a CANCELLED watermark).

    This flips the print-layer flag only; it does NOT reverse or alter the
    underlying business transaction, which follows its own reversal controls.
    """
    row.cancelled = True
    row.updated_by = by
    _audit(db, row.company_id, by, "DOC_CANCEL", row.number, row.doc_type)
    db.flush()
    return row
