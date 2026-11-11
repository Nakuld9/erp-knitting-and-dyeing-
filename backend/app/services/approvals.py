"""Generic maker-checker approval engine.

A rule maps (doc_type, amount) to an approval requirement. A request collects
the required number of approvals; every action is an immutable event. The
requester cannot approve their own request, and no action can touch a request
that is already decided.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.approvals import (
    ApprovalAction,
    ApprovalEvent,
    ApprovalRequest,
    ApprovalRule,
    ApprovalStatus,
)


class ApprovalError(Exception):
    """An approval action was invalid (self-approval, already decided, ...)."""


def rule_for(db: Session, company_id: uuid.UUID, doc_type: str, amount) -> ApprovalRule | None:
    """The active rule that applies to this document, or None if no approval is
    required. The highest ``min_amount`` threshold that the amount meets wins."""
    amt = quantize(Decimal(str(amount or 0)), MONEY_SCALE)
    rules = db.execute(
        select(ApprovalRule).where(
            ApprovalRule.company_id == company_id, ApprovalRule.doc_type == doc_type,
            ApprovalRule.is_active.is_(True),
        ).order_by(ApprovalRule.min_amount.desc())
    ).scalars().all()
    for r in rules:
        if amt >= quantize(Decimal(str(r.min_amount or 0)), MONEY_SCALE):
            return r
    return None


def needs_approval(db: Session, company_id: uuid.UUID, doc_type: str, amount) -> bool:
    return rule_for(db, company_id, doc_type, amount) is not None


def create_request(db: Session, *, company_id: uuid.UUID, doc_type: str, title: str,
                   amount, requested_by: uuid.UUID | None, doc_id: uuid.UUID | None = None,
                   rule: ApprovalRule | None = None) -> ApprovalRequest:
    rule = rule or rule_for(db, company_id, doc_type, amount)
    required = rule.approvals_required if rule else 1
    req = ApprovalRequest(
        company_id=company_id, doc_type=doc_type, doc_id=doc_id, title=title,
        amount=quantize(Decimal(str(amount or 0)), MONEY_SCALE), requested_by=requested_by,
        status=ApprovalStatus.PENDING, approvals_required=required, approvals_received=0,
        created_by=requested_by,
    )
    db.add(req)
    db.flush()
    req.events.append(ApprovalEvent(action=ApprovalAction.SUBMIT, actor_id=requested_by,
                                    note="Submitted for approval", created_by=requested_by))
    db.flush()
    # Notify approvers that something is waiting (broadcast to the company).
    from app.services import notifications
    notifications.notify(db, company_id=company_id, kind="APPROVAL",
                         title="Approval needed", message=title,
                         link_doc_type="APPROVAL", link_doc_id=req.id)
    return req


def approve(db: Session, request: ApprovalRequest, *, approver_id: uuid.UUID,
            note: str | None = None) -> dict:
    if request.status != ApprovalStatus.PENDING:
        raise ApprovalError(f"Request is {request.status.value}, not pending")
    if request.requested_by is not None and approver_id == request.requested_by:
        raise ApprovalError("The requester cannot approve their own request")
    # An approver may only approve once.
    for ev in request.events:
        if ev.action == ApprovalAction.APPROVE and ev.actor_id == approver_id:
            raise ApprovalError("You have already approved this request")

    request.events.append(ApprovalEvent(action=ApprovalAction.APPROVE, actor_id=approver_id,
                                        note=note, created_by=approver_id))
    request.approvals_received += 1
    completed = request.approvals_received >= request.approvals_required
    if completed:
        request.status = ApprovalStatus.APPROVED
        request.decided_at = datetime.now(timezone.utc)
    db.flush()
    return {"completed": completed, "approvals_received": request.approvals_received,
            "approvals_required": request.approvals_required, "status": request.status.value}


def reject(db: Session, request: ApprovalRequest, *, approver_id: uuid.UUID,
           note: str | None = None) -> dict:
    if request.status != ApprovalStatus.PENDING:
        raise ApprovalError(f"Request is {request.status.value}, not pending")
    if request.requested_by is not None and approver_id == request.requested_by:
        raise ApprovalError("The requester cannot reject their own request")
    request.events.append(ApprovalEvent(action=ApprovalAction.REJECT, actor_id=approver_id,
                                        note=note, created_by=approver_id))
    request.status = ApprovalStatus.REJECTED
    request.decided_at = datetime.now(timezone.utc)
    db.flush()
    return {"status": request.status.value}
