"""Printed-document registry (company-scoped).

A row records that a business document (identified by ``doc_type`` +
``source_type`` + ``source_id``) has been **issued** a stable, printable number.
It is the reusable backbone for "allocate a number on first issue/print, reuse it
on every reprint": later stages attach real business sources to it.

Creating/reading a row NEVER touches inventory, accounting or production data —
it only reserves a document number and records print history. Numbers are
allocated through ``services.sequences.next_document_number`` (company-specific,
document-type-specific, financial-year-aware, concurrency-safe), and uniqueness is
enforced at the database level so a race can never mint two numbers for one source
document.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Uuid, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AuditMixin, Base


class PrintedDocument(AuditMixin, Base):
    """One issued, numbered printable document for a business source row."""

    __tablename__ = "printed_documents"
    __table_args__ = (
        # one number per (company, doc_type, source) — idempotent issue / reprint
        UniqueConstraint("company_id", "doc_type", "source_type", "source_id",
                         name="uq_printed_doc_source"),
        # a number is unique within a company + document type
        UniqueConstraint("company_id", "doc_type", "number",
                         name="uq_printed_doc_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)

    # Logical identity of the source business document.
    doc_type: Mapped[str] = mapped_column(String(40), index=True)   # e.g. SALES_INVOICE
    source_type: Mapped[str] = mapped_column(String(40))            # e.g. delivery_note
    source_id: Mapped[str] = mapped_column(String(80))             # the business row id

    fiscal_year: Mapped[str] = mapped_column(String(9))
    number: Mapped[str] = mapped_column(String(40), index=True)

    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    reprint_count: Mapped[int] = mapped_column(Integer, default=0)

    first_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_printed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    issued_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
