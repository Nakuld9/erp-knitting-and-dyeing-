"""GST portal / ITC reconciliation & adjustment models (Stage 6F-P5 — GST module).

This module stores:

* **Portal imports** (``GstReturnImport``) — every GSTR-2A / GSTR-2B / electronic
  ledger / GSTR-3B file brought in, whether downloaded through an authorised GSP
  connector or imported manually. The ORIGINAL file bytes and their SHA-256 are
  retained for audit; identical re-imports are rejected by a uniqueness rule.
* **Portal documents** (``GstPortalDocument``) — the invoice / CN / DN / BoE / ISD
  rows parsed out of an import, head-wise (IGST/CGST/SGST/Cess).
* **Reconciliation** (``ItcReconciliationRun`` / ``ItcReconciliationMatch``) — a run
  that matches the ERP purchase register against portal documents, classifying
  each as exact / partial / mismatch / missing / duplicate / amendment / note.
* **ITC tracking register** (``ItcInvoiceStatus`` + append-only ``ItcStatusHistory``)
  — the monthly per-invoice ITC status lifecycle, including defer and
  carry-forward to a later GSTR-2B period.
* **ITC reversal / reclaim** (``ItcReversalReclaim``) — the linked, append-only
  reversal→reclaim lifecycle (partials supported, duplicate reclaim prevented).
* **Policy** (``GstPolicyVersion``) — the versioned, CA-approved GST policy that
  governs claim treatment and the legal utilisation order. Nothing is auto-claimed.
* **Cross-utilisation** (``GstCrossAdjustment``) — a CA-gated utilisation proposal
  that posts (only when explicitly authorised) through the existing engine.
* **Electronic ledgers** (``GstElectronicLedgerBalance``) — portal credit/cash/
  liability balances kept SEPARATE from ERP accounting balances.

No statutory filing, payment or posting happens from the models; posting is always
a separate, CA-gated, explicitly-authorised service action. Every row is
company-scoped for strict multi-company isolation.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.types import Money
from app.models.base import AuditMixin, Base

GST_PORTAL_SCHEMA_VERSION = "1"


# --------------------------------- enums ------------------------------------

class GstReturnType(str, enum.Enum):
    GSTR2A = "GSTR2A"          # dynamic statement
    GSTR2B = "GSTR2B"          # static statement for a tax period
    GSTR3B = "GSTR3B"
    ECL_CREDIT = "ECL_CREDIT"  # electronic credit ledger
    ECL_CASH = "ECL_CASH"      # electronic cash ledger
    ELIABILITY = "ELIABILITY"  # electronic liability register


class GstImportSource(str, enum.Enum):
    MANUAL_JSON = "MANUAL_JSON"
    MANUAL_EXCEL = "MANUAL_EXCEL"
    GSP_API = "GSP_API"


class GstImportStatus(str, enum.Enum):
    IMPORTED = "IMPORTED"
    SUPERSEDED = "SUPERSEDED"


class PortalDocCategory(str, enum.Enum):
    B2B = "B2B"
    B2BA = "B2BA"            # amendment
    CDNR = "CDNR"            # credit/debit notes
    CDNRA = "CDNRA"          # amendment
    IMPG = "IMPG"            # import of goods (BoE)
    IMPGSEZ = "IMPGSEZ"
    ISD = "ISD"
    ISDA = "ISDA"


class PortalDocType(str, enum.Enum):
    INV = "INV"
    CN = "CN"
    DN = "DN"
    BOE = "BOE"
    ISD = "ISD"


class ItcStatus(str, enum.Enum):
    BOOKED_NOT_IN_2B = "BOOKED_NOT_IN_2B"
    IN_2A_NOT_IN_2B = "IN_2A_NOT_IN_2B"
    IN_2B_MATCHED = "IN_2B_MATCHED"
    MISMATCH_UNDER_REVIEW = "MISMATCH_UNDER_REVIEW"
    ITC_DEFERRED = "ITC_DEFERRED"
    ELIGIBLE_FOR_REVIEW = "ELIGIBLE_FOR_REVIEW"
    ITC_CLAIMED = "ITC_CLAIMED"
    ITC_REVERSED = "ITC_REVERSED"
    ITC_RECLAIMED = "ITC_RECLAIMED"
    ITC_INELIGIBLE = "ITC_INELIGIBLE"


class MatchType(str, enum.Enum):
    EXACT = "EXACT"
    PARTIAL = "PARTIAL"
    INVOICE_FORMAT_DIFF = "INVOICE_FORMAT_DIFF"
    DATE_DIFF = "DATE_DIFF"
    TAXABLE_DIFF = "TAXABLE_DIFF"
    RATE_DIFF = "RATE_DIFF"
    HEAD_DIFF = "HEAD_DIFF"
    MISSING_IN_BOOKS = "MISSING_IN_BOOKS"
    MISSING_IN_2A = "MISSING_IN_2A"
    MISSING_IN_2B = "MISSING_IN_2B"
    DUPLICATE = "DUPLICATE"
    AMENDMENT = "AMENDMENT"
    CREDIT_NOTE = "CREDIT_NOTE"
    DEBIT_NOTE = "DEBIT_NOTE"


class MatchResolution(str, enum.Enum):
    UNRESOLVED = "UNRESOLVED"
    RESOLVED = "RESOLVED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class ItcAction(str, enum.Enum):
    REVERSAL = "REVERSAL"
    RECLAIM = "RECLAIM"


class PolicyStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"


class CrossAdjStatus(str, enum.Enum):
    PROPOSED = "PROPOSED"
    CA_APPROVED = "CA_APPROVED"
    POSTED = "POSTED"
    REVERSED = "REVERSED"


# ------------------------------- imports ------------------------------------

class GstReturnImport(AuditMixin, Base):
    __tablename__ = "gst_return_imports"
    __table_args__ = (
        UniqueConstraint("company_id", "return_type", "return_period", "file_hash",
                         name="uq_gst_import_period_hash"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    gstin: Mapped[str] = mapped_column(String(15), index=True)
    financial_year: Mapped[str] = mapped_column(String(9))          # e.g. 2026-27
    return_period: Mapped[str] = mapped_column(String(6), index=True)  # MMYYYY
    return_type: Mapped[str] = mapped_column(String(12), index=True)
    source: Mapped[str] = mapped_column(String(16))
    file_name: Mapped[str] = mapped_column(String(255))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    raw_content: Mapped[bytes] = mapped_column(LargeBinary)          # original retained for audit
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    doc_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(12), default=GstImportStatus.IMPORTED.value, index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)
    imported_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GstPortalDocument(AuditMixin, Base):
    __tablename__ = "gst_portal_documents"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    import_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("gst_return_imports.id", ondelete="CASCADE"), index=True)
    return_type: Mapped[str] = mapped_column(String(12), index=True)
    return_period: Mapped[str] = mapped_column(String(6), index=True)
    category: Mapped[str] = mapped_column(String(10))
    doc_type: Mapped[str] = mapped_column(String(4), default=PortalDocType.INV.value)
    supplier_gstin: Mapped[str | None] = mapped_column(String(15), index=True)
    supplier_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    doc_number: Mapped[str | None] = mapped_column(String(40), index=True)
    doc_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    taxable_value: Mapped[object] = mapped_column(Money, default=0)
    igst: Mapped[object] = mapped_column(Money, default=0)
    cgst: Mapped[object] = mapped_column(Money, default=0)
    sgst: Mapped[object] = mapped_column(Money, default=0)
    cess: Mapped[object] = mapped_column(Money, default=0)
    total_value: Mapped[object] = mapped_column(Money, default=0)
    rate: Mapped[object] = mapped_column(Money, default=0)
    place_of_supply: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reverse_charge: Mapped[bool] = mapped_column(Boolean, default=False)
    itc_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # GSTR-2B flag
    itc_unavailable_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_amendment: Mapped[bool] = mapped_column(Boolean, default=False)
    original_doc_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    original_doc_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    raw: Mapped[str | None] = mapped_column(Text, nullable=True)


# ----------------------------- reconciliation -------------------------------

class ItcReconciliationRun(AuditMixin, Base):
    __tablename__ = "gst_itc_reco_runs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    gstin: Mapped[str] = mapped_column(String(15))
    return_type: Mapped[str] = mapped_column(String(12), index=True)
    return_period: Mapped[str] = mapped_column(String(6), index=True)
    import_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("gst_return_imports.id", ondelete="SET NULL"), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ItcReconciliationMatch(AuditMixin, Base):
    __tablename__ = "gst_itc_reco_matches"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("gst_itc_reco_runs.id", ondelete="CASCADE"), index=True)
    return_period: Mapped[str] = mapped_column(String(6), index=True)
    invoice_status_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("gst_itc_invoice_status.id", ondelete="SET NULL"), nullable=True, index=True)
    portal_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("gst_portal_documents.id", ondelete="SET NULL"), nullable=True, index=True)
    supplier_gstin: Mapped[str | None] = mapped_column(String(15), index=True)
    doc_number: Mapped[str | None] = mapped_column(String(40))
    match_type: Mapped[str] = mapped_column(String(24), index=True)
    diff: Mapped[str | None] = mapped_column(Text, nullable=True)       # head-wise JSON diff
    resolution_status: Mapped[str] = mapped_column(
        String(12), default=MatchResolution.UNRESOLVED.value, index=True)
    resolution_remark: Mapped[str | None] = mapped_column(String(500), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --------------------------- ITC tracking register --------------------------

class ItcInvoiceStatus(AuditMixin, Base):
    """The books-side monthly ITC register row for one purchase document. Captured
    READ-ONLY from the ERP purchase register — never writes back to the invoice."""

    __tablename__ = "gst_itc_invoice_status"
    __table_args__ = (
        UniqueConstraint("company_id", "books_key", name="uq_gst_itc_books_key"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    gstin: Mapped[str] = mapped_column(String(15), index=True)
    books_key: Mapped[str] = mapped_column(String(120), index=True)
    # Read-only reference to the ERP purchase source (module + id/number).
    source_module: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("suppliers.id"), nullable=True, index=True)
    supplier_gstin: Mapped[str | None] = mapped_column(String(15), index=True)
    supplier_invoice_no: Mapped[str | None] = mapped_column(String(40), index=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    doc_type: Mapped[str] = mapped_column(String(4), default=PortalDocType.INV.value)
    booked_period: Mapped[str] = mapped_column(String(6), index=True)   # MMYYYY booked in books
    taxable_value: Mapped[object] = mapped_column(Money, default=0)
    igst: Mapped[object] = mapped_column(Money, default=0)
    cgst: Mapped[object] = mapped_column(Money, default=0)
    sgst: Mapped[object] = mapped_column(Money, default=0)
    cess: Mapped[object] = mapped_column(Money, default=0)
    status: Mapped[str] = mapped_column(
        String(24), default=ItcStatus.BOOKED_NOT_IN_2B.value, index=True)
    matched_period: Mapped[str | None] = mapped_column(String(6), nullable=True, index=True)
    claim_period: Mapped[str | None] = mapped_column(String(6), nullable=True)
    deferred_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    eligibility_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    matched_document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    # Running reversal/reclaim totals (sum of linked lifecycle rows), by head.
    reversed_total: Mapped[object] = mapped_column(Money, default=0)
    reclaimed_total: Mapped[object] = mapped_column(Money, default=0)
    last_reco_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


class ItcStatusHistory(AuditMixin, Base):
    """Append-only history of every ITC status change (never deleted/overwritten)."""

    __tablename__ = "gst_itc_status_history"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    invoice_status_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("gst_itc_invoice_status.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    to_status: Mapped[str] = mapped_column(String(24))
    period: Mapped[str | None] = mapped_column(String(6), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    voucher_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ItcReversalReclaim(AuditMixin, Base):
    """A linked ITC reversal or reclaim lifecycle entry (append-only). A reclaim
    links back to its originating reversal; partials are allowed and a reclaim can
    never exceed the net-reversed balance (duplicate reclaim prevented)."""

    __tablename__ = "gst_itc_reversal_reclaim"
    __table_args__ = (
        UniqueConstraint("company_id", "reference", name="uq_gst_itc_rr_reference"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    invoice_status_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("gst_itc_invoice_status.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(10), index=True)
    tax_period: Mapped[str] = mapped_column(String(6), index=True)
    igst: Mapped[object] = mapped_column(Money, default=0)
    cgst: Mapped[object] = mapped_column(Money, default=0)
    sgst: Mapped[object] = mapped_column(Money, default=0)
    cess: Mapped[object] = mapped_column(Money, default=0)
    is_partial: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reference: Mapped[str] = mapped_column(String(120), index=True)
    voucher_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    linked_reversal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("gst_itc_reversal_reclaim.id"), nullable=True)
    created_at_actor: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# -------------------------------- policy ------------------------------------

class GstPolicyVersion(AuditMixin, Base):
    """Versioned, CA-approved GST policy. Only an APPROVED + current version drives
    claim treatment and the utilisation order. Rules are JSON; nothing is hardcoded."""

    __tablename__ = "gst_policy_versions"
    __table_args__ = (
        UniqueConstraint("company_id", "version_no", name="uq_gst_policy_version"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    rules: Mapped[str] = mapped_column(Text)        # JSON: utilisation order, flags
    status: Mapped[str] = mapped_column(String(12), default=PolicyStatus.DRAFT.value, index=True)
    title: Mapped[str | None] = mapped_column(String(160), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approver_name: Mapped[str | None] = mapped_column(String(120), nullable=True)


# ----------------------------- cross-utilisation ----------------------------

class GstCrossAdjustment(AuditMixin, Base):
    """A CA-gated GST credit cross-utilisation for a tax period. Posting is blocked
    until the batch is CA-approved against an APPROVED policy AND explicitly
    authorised; it then posts ONE balanced voucher through the existing engine."""

    __tablename__ = "gst_cross_adjustments"
    __table_args__ = (
        UniqueConstraint("company_id", "posting_reference", name="uq_gst_crossadj_ref"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    tax_period: Mapped[str] = mapped_column(String(6), index=True)
    policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("gst_policy_versions.id"), nullable=True)
    posting_reference: Mapped[str] = mapped_column(String(120), index=True)
    proposal: Mapped[str] = mapped_column(Text)      # JSON: available/liability/utilisation
    status: Mapped[str] = mapped_column(String(12), default=CrossAdjStatus.PROPOSED.value, index=True)
    ca_approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    ca_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ca_approver_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    posted_voucher_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at_actor: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


# --------------------------- electronic ledgers -----------------------------

class GstElectronicLedgerBalance(AuditMixin, Base):
    """A portal electronic-ledger balance row (credit / cash / liability), kept
    strictly separate from ERP accounting balances. Never fabricated."""

    __tablename__ = "gst_electronic_ledger_balances"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    import_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("gst_return_imports.id", ondelete="SET NULL"), nullable=True, index=True)
    ledger_type: Mapped[str] = mapped_column(String(12), index=True)  # CREDIT/CASH/LIABILITY
    return_period: Mapped[str | None] = mapped_column(String(6), nullable=True, index=True)
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    head: Mapped[str] = mapped_column(String(12))                     # IGST/CGST/SGST/CESS/...
    opening_balance: Mapped[object] = mapped_column(Money, default=0)
    credit: Mapped[object] = mapped_column(Money, default=0)
    debit: Mapped[object] = mapped_column(Money, default=0)
    closing_balance: Mapped[object] = mapped_column(Money, default=0)
    raw: Mapped[str | None] = mapped_column(Text, nullable=True)
