"""Gap-free document number generation."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import DocumentSequence


def _seed_sequence_row(db: Session, dialect: str, *, company_id, doc_type, fiscal_year,
                       prefix, padding) -> None:
    """Idempotently insert the seed sequence row (next_number=1) for a new
    (company, doc_type, FY). Uses ON CONFLICT DO NOTHING on PostgreSQL/SQLite so a
    concurrent creator never raises; falls back to a plain insert elsewhere."""
    values = dict(id=uuid.uuid4(), company_id=company_id, doc_type=doc_type,
                  fiscal_year=fiscal_year, prefix=prefix, next_number=1, padding=padding)
    table = DocumentSequence.__table__
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        stmt = pg_insert(table).values(**values).on_conflict_do_nothing(
            index_elements=["company_id", "doc_type", "fiscal_year"])
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sl_insert
        stmt = sl_insert(table).values(**values).on_conflict_do_nothing(
            index_elements=["company_id", "doc_type", "fiscal_year"])
    else:
        stmt = table.insert().values(**values)
    db.execute(stmt)
    db.flush()


def next_document_number(
    db: Session,
    *,
    company_id: uuid.UUID,
    doc_type: str,
    fiscal_year: str,
    prefix: str = "",
    padding: int = 5,
) -> str:
    """Return the next formatted number for (company, doc_type, fiscal_year).

    Uses a row-level lock on supporting databases so concurrent posters don't
    collide. The number is reserved atomically within the caller's transaction.
    """
    base = select(DocumentSequence).where(
        DocumentSequence.company_id == company_id,
        DocumentSequence.doc_type == doc_type,
        DocumentSequence.fiscal_year == fiscal_year,
    )
    dialect = db.bind.dialect.name if db.bind is not None else ""
    locking = dialect not in ("", "sqlite")
    # Lock the row where the dialect supports it (PostgreSQL). Harmless on SQLite.
    stmt = base.with_for_update() if locking else base

    seq = db.execute(stmt).scalar_one_or_none()
    if seq is None:
        # First allocation for this (company, doc_type, FY). Under concurrency
        # several transactions can all see "no row"; seed it race-safely with an
        # INSERT ... ON CONFLICT DO NOTHING so a concurrent create never raises,
        # then re-read the row WITH a lock. Exactly one physical row exists, so no
        # duplicate number is ever minted.
        _seed_sequence_row(db, dialect, company_id=company_id, doc_type=doc_type,
                           fiscal_year=fiscal_year, prefix=prefix, padding=padding)
        seq = db.execute(stmt).scalar_one()

    number = seq.next_number
    seq.next_number = number + 1
    db.flush()

    fy_suffix = fiscal_year.replace("-", "")[2:6] if "-" in fiscal_year else fiscal_year
    body = str(number).zfill(seq.padding or padding)
    pfx = seq.prefix or prefix
    return f"{pfx}{fy_suffix}-{body}" if pfx else f"{fy_suffix}-{body}"
