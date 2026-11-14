"""Backend-agnostic print-document specification.

A ``PrintDocument`` describes *what* to print (letterhead, title, number, parties,
line items, totals, references, signatures, status). The PDF renderer consumes it,
so every business document in later stages is described once in these terms and
gets an identical, professional A4 layout.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CompanyLetterhead:
    """Active-company identity for the header (from branding + core.Company)."""
    legal_name: str
    address: str | None = None
    gstin: str | None = None
    state_code: str | None = None
    cin: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    logo_bytes: bytes | None = None
    logo_mime: str | None = None


@dataclass
class DocumentParty:
    """A named party/address block, e.g. 'Bill To', 'Ship To', 'Factory'."""
    label: str
    name: str
    lines: list[str] = field(default_factory=list)   # address / GSTIN / state etc.


@dataclass
class DocumentColumn:
    label: str
    key: str
    numeric: bool = False
    width: float | None = None   # relative weight; renderer normalises


@dataclass
class SignatureBlock:
    label: str                   # e.g. "Authorised Signatory"
    for_line: str | None = None  # e.g. "For Demo Textile Mills Pvt Ltd"


@dataclass
class PrintDocument:
    """Everything needed to render one document to A4."""
    title: str                              # e.g. "SAMPLE DOCUMENT"
    letterhead: CompanyLetterhead
    number: str                             # shown as-is (or a preview placeholder)
    date: str                               # ISO or formatted date string
    parties: list[DocumentParty] = field(default_factory=list)
    columns: list[DocumentColumn] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    totals: list[tuple[str, str]] = field(default_factory=list)   # (label, value)
    tax_lines: list[tuple[str, str]] = field(default_factory=list)
    references: list[tuple[str, str]] = field(default_factory=list)  # (label, value)
    signatures: list[SignatureBlock] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    footer_note: str | None = None

    # Status / rendering flags
    is_preview: bool = False                # preview watermark; number is a placeholder
    is_cancelled: bool = False              # CANCELLED watermark
    reprint_count: int = 0                  # > 0 renders a "REPRINT" marker
    document_kind: str = "Document"         # e.g. "Tax Invoice" (subtitle under title)
