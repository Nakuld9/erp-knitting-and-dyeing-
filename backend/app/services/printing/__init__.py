"""Reusable document-printing framework (Stage 2).

A single engine renders any business document to a professional A4 PDF using the
active company's branding, plus a company/doc-type/FY-aware, concurrency-safe
number allocator that stamps a stable number on first issue and reuses it on every
reprint. Individual business documents are built on top of this in later stages.
"""
from app.services.printing.document import (  # noqa: F401
    CompanyLetterhead,
    DocumentColumn,
    DocumentParty,
    PrintDocument,
    SignatureBlock,
)
from app.services.printing.pdf import render_pdf  # noqa: F401
