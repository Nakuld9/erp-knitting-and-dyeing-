"""Maker-checker approval workflow.

A configurable rule decides whether a document of a given type and amount needs
approval, and how many approvals from which permission. An approval request
tracks the decision; every action is recorded as an immutable event. The
requester can never approve their own request (maker != checker).

The first consumer is supplier payments: a payment at or above the threshold is
held until approved, then posted; below the threshold it posts immediately.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.types import Money
from app.models.base import AuditMixin, Base


class ApprovalStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class PaymentStatus(str, enum.Enum):
    PENDING = "PENDING"
    REJECTED = "REJECTED"
    PAID = "PAID"


class ApprovalAction(str, enum.Enum):
    SUBMIT = "SUBMIT"
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class ApprovalRule(AuditMixin, Base):
    """Documents of ``doc_type`` with amount >= ``min_amount`` need
    ``approvals_required`` approvals from holders of ``approver_permission``."""
    __tablename__ = "approval_rules"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    doc_type: Mapped[str] = mapped_column(String(40), index=True)
    min_amount: Mapped[object] = mapped_column(Money, default=0)
    approvals_required: Mapped[int] = mapped_column(Integer, default=1)
    approver_permission: Mapped[str] = mapped_column(String(60), default="voucher.approve")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ApprovalRequest(AuditMixin, Base):
    __tablename__ = "approval_requests"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    doc_type: Mapped[str] = mapped_column(String(40), index=True)
    doc_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    amount: Mapped[object] = mapped_column(Money, default=0)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[ApprovalStatus] = mapped_column(String(12), default=ApprovalStatus.PENDING, index=True)
    approvals_required: Mapped[int] = mapped_column(Integer, default=1)
    approvals_received: Mapped[int] = mapped_column(Integer, default=0)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    events: Mapped[list["ApprovalEvent"]] = relationship(
        back_populates="request", cascade="all, delete-orphan"
    )


class ApprovalEvent(AuditMixin, Base):
    __tablename__ = "approval_events"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("approval_requests.id", ondelete="CASCADE"), index=True)
    action: Mapped[ApprovalAction] = mapped_column(String(10))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    request: Mapped[ApprovalRequest] = relationship(back_populates="events")


class PaymentApproval(AuditMixin, Base):
    """A supplier payment routed through the approval workflow."""
    __tablename__ = "payment_approvals"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"), index=True)
    bank_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bank_accounts.id"))
    amount: Mapped[object] = mapped_column(Money, default=0)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[PaymentStatus] = mapped_column(String(10), default=PaymentStatus.PENDING, index=True)
    approval_request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approval_requests.id"), nullable=True)
    posting_reference: Mapped[str | None] = mapped_column(String(120), index=True)
