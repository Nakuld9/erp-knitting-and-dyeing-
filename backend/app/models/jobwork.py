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

from sqlalchemy import Date, ForeignKey, String, Uuid
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
