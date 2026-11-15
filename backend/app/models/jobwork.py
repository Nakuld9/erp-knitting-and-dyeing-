"""Job work — processing fabric for others (inward) or sending ours out (outward).

INWARD: a customer's fabric comes to us for dyeing/finishing; the material is
*theirs* (never valued in our books) and we bill a processing charge (income).

OUTWARD: our fabric goes to another dyer/finisher; it stays our asset, and when
it comes back we book the processing charge they bill us (a direct cost) against
them as a payable.

Material movement is tracked at the quantity (memo) level; the money events are
posted through the double-entry engine like any other document.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.types import Money, Quantity
from app.models.base import AuditMixin, Base


class JobWorkDirection(str, enum.Enum):
    INWARD = "INWARD"     # we process others' material (we earn)
    OUTWARD = "OUTWARD"   # we send ours out (we pay)


class JobWorkProcess(str, enum.Enum):
    DYEING = "DYEING"
    FINISHING = "FINISHING"
    KNITTING = "KNITTING"
    OTHER = "OTHER"


class JobWorkStatus(str, enum.Enum):
    OPEN = "OPEN"
    SENT = "SENT"           # outward: goods dispatched to subcontractor
    RECEIVED = "RECEIVED"   # material back / processed
    BILLED = "BILLED"       # money event posted
    CANCELLED = "CANCELLED"


class JobWork(AuditMixin, Base):
    __tablename__ = "job_works"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    direction: Mapped[JobWorkDirection] = mapped_column(String(10), index=True)
    process: Mapped[JobWorkProcess] = mapped_column(String(12), default=JobWorkProcess.DYEING)
    order_date: Mapped[date] = mapped_column(Date)

    # counterparty: a customer (inward) or a subcontractor supplier (outward)
    party_type: Mapped[str] = mapped_column(String(12))
    party_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)

    fabric_type_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    qty_kg: Mapped[object] = mapped_column(Quantity, default=0)
    returned_qty: Mapped[object] = mapped_column(Quantity, default=0)

    rate: Mapped[object] = mapped_column(Money, default=0)            # processing rate per kg
    tax_code_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tax_codes.id"), nullable=True)
    taxable_value: Mapped[object] = mapped_column(Money, default=0)
    tax_amount: Mapped[object] = mapped_column(Money, default=0)
    total_value: Mapped[object] = mapped_column(Money, default=0)

    status: Mapped[JobWorkStatus] = mapped_column(String(10), default=JobWorkStatus.OPEN, index=True)
    posting_reference: Mapped[str | None] = mapped_column(String(120), index=True)


class JobWorkIssueStatus(str, enum.Enum):
    """Lifecycle of material issued into a job worker's custody.

    OUTSTANDING -> PARTIALLY_RECEIVED (some input accounted) -> CLOSED (all input
    accounted). RESOLVED is kept as a legacy alias of CLOSED for stability."""
    OUTSTANDING = "OUTSTANDING"
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"
    CLOSED = "CLOSED"
    RESOLVED = "RESOLVED"


class JobWorkInspection(str, enum.Enum):
    ACCEPT = "ACCEPT"
    REWORK = "REWORK"
    REJECT = "REJECT"


class JobWorkIssue(AuditMixin, Base):
    """Material issued into a job worker's custody (an internal job-work issue
    challan). The material physically moves — in the authoritative inventory
    ledger — from a source warehouse into the vendor's custody warehouse; it stays
    company-owned and valued. This record carries the challan/traceability detail
    and the vendor-wise outstanding position. It never posts a voucher."""

    __tablename__ = "job_work_issues"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    factory_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True, index=True)
    # the job-work order this issue belongs to (optional link)
    job_work_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_works.id"), nullable=True, index=True)
    job_worker_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"), index=True)
    process: Mapped[str] = mapped_column(String(12), index=True)   # JobWorkProcess

    source_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    custody_warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"), index=True)

    # inventory item that moved (ItemType + id); a roll reference where applicable
    item_type: Mapped[str] = mapped_column(String(12))
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    fabric_roll_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fabric_rolls.id"), nullable=True, index=True)

    # weight (kg) and length (m) are DISTINCT and never conflated
    qty: Mapped[object] = mapped_column(Quantity, default=0)
    uom: Mapped[str] = mapped_column(String(10), default="KG")
    weight_kg: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    length_m: Mapped[object | None] = mapped_column(Quantity, nullable=True)

    issue_date: Mapped[date] = mapped_column(Date, index=True)
    challan_no: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[JobWorkIssueStatus] = mapped_column(String(18), default=JobWorkIssueStatus.OUTSTANDING, index=True)
    posting_reference: Mapped[str | None] = mapped_column(String(120), index=True)

    # Reconciliation running totals (Stage 3D). Input disposition:
    #   issued qty = resolved_input_qty + remaining_outstanding, where
    #   resolved_input_qty = accepted + returned + rejected + loss + shortage.
    # kg and metres are tracked independently and never converted.
    resolved_input_qty: Mapped[object] = mapped_column(Quantity, default=0)
    accepted_output_qty: Mapped[object] = mapped_column(Quantity, default=0)
    returned_qty: Mapped[object] = mapped_column(Quantity, default=0)
    rejected_qty: Mapped[object] = mapped_column(Quantity, default=0)
    process_loss_qty: Mapped[object] = mapped_column(Quantity, default=0)
    shortage_qty: Mapped[object] = mapped_column(Quantity, default=0)


class JobWorkReceipt(AuditMixin, Base):
    """One receipt event against a :class:`JobWorkIssue`. An issue may have many
    receipts (partial receipts). Each records its own date, quantities (input
    disposition + accepted output), weight/length, inspection result, the output
    roll (new or the same preserved roll) and audit. It never posts a voucher;
    accepted output and unprocessed returns move via the inventory ledger
    (GL-neutral). Recorded loss/rejection/shortage are reconciliation quantities
    whose inventory write-off is deferred (no accounting-policy change here)."""

    __tablename__ = "job_work_receipts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    factory_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True, index=True)
    job_work_issue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_work_issues.id"), index=True)
    job_work_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_works.id"), nullable=True, index=True)
    job_worker_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"), index=True)
    process: Mapped[str] = mapped_column(String(12), index=True)

    receipt_date: Mapped[date] = mapped_column(Date, index=True)
    challan_no: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)

    # input disposition (reconciles against the issue's input qty)
    input_resolved_qty: Mapped[object] = mapped_column(Quantity, default=0)
    accepted_input_qty: Mapped[object] = mapped_column(Quantity, default=0)
    returned_qty: Mapped[object] = mapped_column(Quantity, default=0)
    rejected_qty: Mapped[object] = mapped_column(Quantity, default=0)
    rework_qty: Mapped[object] = mapped_column(Quantity, default=0)
    process_loss_qty: Mapped[object] = mapped_column(Quantity, default=0)
    shortage_qty: Mapped[object] = mapped_column(Quantity, default=0)

    # accepted OUTPUT (separate; weight & length independent, never converted)
    accepted_output_qty: Mapped[object] = mapped_column(Quantity, default=0)
    output_weight_kg: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    output_length_m: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    uom: Mapped[str] = mapped_column(String(10), default="KG")

    inspection_result: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # lineage: the original roll issued, and the accepted output roll (new, or the
    # same preserved roll when returned without transformation)
    source_roll_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fabric_rolls.id"), nullable=True, index=True)
    output_roll_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fabric_rolls.id"), nullable=True, index=True)
    output_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    return_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)

    is_excess: Mapped[bool] = mapped_column(Boolean, default=False)
    authorized_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    posting_reference: Mapped[str | None] = mapped_column(String(120), index=True)
