"""Financial opening-balance lines + CA decision register (Stage 6F-P5).

A Financial Opening Batch is a P1 ``OpeningBatch`` of type ``GL_BALANCES`` that
carries six kinds of typed financial opening line, all company-scoped and hung
off the shared ``OpeningImportFile`` import pipeline:

* ``OpeningGLLine``        — one trial-balance account balance (debit XOR credit).
* ``OpeningArLine``        — invoice-level customer receivable detail (+ advances).
* ``OpeningApLine``        — invoice-level supplier payable detail (+ advances).
* ``OpeningBankLine``      — bank/cash book vs statement balance + instruments.
* ``OpeningFixedAssetLine``— fixed-asset register opening (cost + accum. deprec.).
* ``OpeningStatutoryLine`` — GST / TDS / other statutory account balances.

Posting is HARD-GATED (see ``opening_financial`` service): the batch must be
approved (maker-checker), every required ``OpeningCaDecision`` must be APPROVED,
every reconciliation must pass, and an explicit posting authorisation must be
given, before a single balanced ``OB`` voucher is posted via the existing posting
engine. No value is ever auto-plugged to equity/retained earnings; no sub-ledger
is re-created; no fake invoice/receipt/filing is generated.
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
    String,
    Text,
    Uuid,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.types import Money
from app.models.base import AuditMixin, Base

OPENING_FINANCIAL_TEMPLATE_VERSION = "1"


class FinancialLineType(str, enum.Enum):
    GL = "GL_BALANCES"
    AR = "CUSTOMER_AR"
    AP = "SUPPLIER_AP"
    BANK = "BANK_CASH"
    FIXED_ASSET = "FIXED_ASSETS"
    STATUTORY = "STATUTORY"


class ArKind(str, enum.Enum):
    INVOICE = "INVOICE"
    ADVANCE = "ADVANCE"
    UNALLOCATED_RECEIPT = "UNALLOCATED_RECEIPT"
    CREDIT_BALANCE = "CREDIT_BALANCE"


class ApKind(str, enum.Enum):
    INVOICE = "INVOICE"
    JOBWORK_BILL = "JOBWORK_BILL"
    ADVANCE = "ADVANCE"
    UNALLOCATED_PAYMENT = "UNALLOCATED_PAYMENT"
    CREDIT_BALANCE = "CREDIT_BALANCE"


class CaDecisionStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class FinancialPostingState(str, enum.Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    CA_APPROVAL_PENDING = "CA_APPROVAL_PENDING"
    READY_FOR_AUTHORISED_POSTING = "READY_FOR_AUTHORISED_POSTING"
    POSTED = "POSTED"
    REVERSED = "REVERSED"


class _LineBase(AuditMixin):
    """Shared columns for every financial opening line."""

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    import_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("opening_import_files.id", ondelete="SET NULL"), nullable=True, index=True)
    row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_document: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    disputed_unknown: Mapped[bool] = mapped_column(Boolean, default=False)
    validation_status: Mapped[str] = mapped_column(String(12), default="PENDING", index=True)
    validation_errors: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedup_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OpeningGLLine(_LineBase, Base):
    __tablename__ = "opening_gl_lines"
    __table_args__ = (UniqueConstraint("batch_id", "dedup_key", name="uq_opening_gl_batch_dedup"),)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opening_batches.id", ondelete="CASCADE"), index=True)
    account_code: Mapped[str] = mapped_column(String(20), index=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    debit: Mapped[object] = mapped_column(Money, default=0)
    credit: Mapped[object] = mapped_column(Money, default=0)
    narration: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # When true this GL control line is split into party-tagged lines from the
    # AR/AP sub-ledger detail at posting (never double-counted).
    is_control_split: Mapped[bool] = mapped_column(Boolean, default=False)


class OpeningArLine(_LineBase, Base):
    __tablename__ = "opening_ar_lines"
    __table_args__ = (UniqueConstraint("batch_id", "dedup_key", name="uq_opening_ar_batch_dedup"),)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opening_batches.id", ondelete="CASCADE"), index=True)
    ar_kind: Mapped[str] = mapped_column(String(20), default=ArKind.INVOICE.value, index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id"), nullable=True, index=True)
    invoice_no: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    original_amount: Mapped[object] = mapped_column(Money, default=0)
    received_amount: Mapped[object] = mapped_column(Money, default=0)
    outstanding_amount: Mapped[object] = mapped_column(Money, default=0)
    tax_reference: Mapped[str | None] = mapped_column(String(60), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)


class OpeningApLine(_LineBase, Base):
    __tablename__ = "opening_ap_lines"
    __table_args__ = (UniqueConstraint("batch_id", "dedup_key", name="uq_opening_ap_batch_dedup"),)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opening_batches.id", ondelete="CASCADE"), index=True)
    ap_kind: Mapped[str] = mapped_column(String(20), default=ApKind.INVOICE.value, index=True)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("suppliers.id"), nullable=True, index=True)
    invoice_no: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    original_amount: Mapped[object] = mapped_column(Money, default=0)
    paid_amount: Mapped[object] = mapped_column(Money, default=0)
    outstanding_amount: Mapped[object] = mapped_column(Money, default=0)
    tds_amount: Mapped[object] = mapped_column(Money, default=0)
    tax_reference: Mapped[str | None] = mapped_column(String(60), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)


class OpeningBankLine(_LineBase, Base):
    __tablename__ = "opening_bank_lines"
    __table_args__ = (UniqueConstraint("batch_id", "dedup_key", name="uq_opening_bank_batch_dedup"),)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opening_batches.id", ondelete="CASCADE"), index=True)
    bank_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("bank_accounts.id"), nullable=True, index=True)
    account_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    is_cash: Mapped[bool] = mapped_column(Boolean, default=False)
    book_balance: Mapped[object] = mapped_column(Money, default=0)
    statement_balance: Mapped[object] = mapped_column(Money, default=0)
    outstanding_cheques: Mapped[object] = mapped_column(Money, default=0)
    deposits_in_transit: Mapped[object] = mapped_column(Money, default=0)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)


class OpeningFixedAssetLine(_LineBase, Base):
    __tablename__ = "opening_fixed_asset_lines"
    __table_args__ = (UniqueConstraint("batch_id", "dedup_key", name="uq_opening_fa_batch_dedup"),)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opening_batches.id", ondelete="CASCADE"), index=True)
    asset_code: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    category: Mapped[str | None] = mapped_column(String(60), nullable=True)
    factory_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    asset_account_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    accum_dep_account_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    acquisition_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    original_cost: Mapped[object] = mapped_column(Money, default=0)
    accumulated_depreciation: Mapped[object] = mapped_column(Money, default=0)
    net_book_value: Mapped[object] = mapped_column(Money, default=0)
    salvage_value: Mapped[object] = mapped_column(Money, default=0)
    method: Mapped[str | None] = mapped_column(String(3), nullable=True)   # SLM / WDV
    useful_life_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verification_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    fixed_asset_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)


class OpeningStatutoryLine(_LineBase, Base):
    __tablename__ = "opening_statutory_lines"
    __table_args__ = (UniqueConstraint("batch_id", "dedup_key", name="uq_opening_stat_batch_dedup"),)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opening_batches.id", ondelete="CASCADE"), index=True)
    statutory_type: Mapped[str | None] = mapped_column(String(20), nullable=True)   # GST / TDS / OTHER
    account_code: Mapped[str] = mapped_column(String(20), index=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    debit: Mapped[object] = mapped_column(Money, default=0)
    credit: Mapped[object] = mapped_column(Money, default=0)
    period_reference: Mapped[str | None] = mapped_column(String(40), nullable=True)
    challan_reference: Mapped[str | None] = mapped_column(String(60), nullable=True)
    ca_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)


class OpeningCaDecision(AuditMixin, Base):
    """Per-batch CA decision register. Every required decision must be APPROVED
    (by a CA reviewer, with a supporting reference) before posting is permitted.
    A developer cannot mark these APPROVED to make tests pass in production use;
    tests use clearly-labelled synthetic fixtures."""

    __tablename__ = "opening_ca_decisions"
    __table_args__ = (
        UniqueConstraint("batch_id", "decision_key", name="uq_opening_ca_batch_key"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opening_batches.id", ondelete="CASCADE"), index=True)
    decision_key: Mapped[str] = mapped_column(String(60), index=True)
    title: Mapped[str] = mapped_column(String(200))
    proposed_options: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_treatment: Mapped[str | None] = mapped_column(Text, nullable=True)
    supporting_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reviewer_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(10), default=CaDecisionStatus.PENDING.value, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_required: Mapped[bool] = mapped_column(Boolean, default=True)


# The CA decision keys that MUST be APPROVED before any financial opening posts.
REQUIRED_CA_DECISIONS: list[tuple[str, str]] = [
    ("opening_effective_date", "Opening effective date and cutover basis"),
    ("inventory_carrying_basis", "Inventory carrying-value basis (= P1-P4 sub-ledger)"),
    ("inventory_control_mapping", "Inventory control-account mappings incl. trading fabric & vendor custody"),
    ("ar_recognition", "Customer opening invoice recognition"),
    ("ap_recognition", "Supplier opening invoice recognition"),
    ("advances_treatment", "Customer and supplier advances treatment"),
    ("gst_treatment", "GST input/output and reverse-charge opening"),
    ("tds_treatment", "TDS receivable/payable opening"),
    ("bank_reconciliation", "Bank reconciliation and outstanding instruments"),
    ("fixed_asset_valuation", "Fixed asset valuation and accumulated depreciation basis"),
    ("loans_liabilities", "Loans, interest, provisions and liabilities"),
    ("equity_balancing", "Opening equity / retained earnings balancing treatment"),
    ("rounding_policy", "Rounding-difference treatment"),
    ("journal_structure", "Opening journal structure and period/correction policy"),
]
