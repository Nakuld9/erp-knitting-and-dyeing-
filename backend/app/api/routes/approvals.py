"""Maker-checker approvals: rules, payment requests, approve / reject."""
from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_company_db
from app.core.deps import require_permission
from app.models.approvals import ApprovalRequest, ApprovalRule, PaymentApproval
from app.models.core import User
from app.services import approvals as svc
from app.services import payments

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


# --------------------------------- rules ------------------------------------

class RuleIn(BaseModel):
    doc_type: str = "PAYMENT"
    min_amount: Decimal = Decimal("0")
    approvals_required: int = 1
    approver_permission: str = "voucher.approve"


@router.post("/rules", status_code=status.HTTP_201_CREATED)
def create_rule(payload: RuleIn, db: Session = Depends(get_company_db),
                user: User = Depends(require_permission("admin.manage"))):
    rule = ApprovalRule(company_id=user.company_id, **payload.model_dump())
    db.add(rule)
    db.commit()
    return {"id": str(rule.id), "doc_type": rule.doc_type, "min_amount": str(rule.min_amount),
            "approvals_required": rule.approvals_required}


@router.get("/rules")
def list_rules(db: Session = Depends(get_company_db),
               user: User = Depends(require_permission("voucher.approve"))):
    rows = db.execute(select(ApprovalRule).where(ApprovalRule.company_id == user.company_id,
                                                 ApprovalRule.is_active.is_(True))).scalars().all()
    return [{"id": str(r.id), "doc_type": r.doc_type, "min_amount": str(r.min_amount),
             "approvals_required": r.approvals_required,
             "approver_permission": r.approver_permission} for r in rows]


# ------------------------------ payment request -----------------------------

class PaymentIn(BaseModel):
    supplier_id: uuid.UUID
    bank_account_id: uuid.UUID
    amount: Decimal
    note: str | None = None


@router.post("/payments", status_code=status.HTTP_201_CREATED)
def request_payment(payload: PaymentIn, db: Session = Depends(get_company_db),
                    user: User = Depends(require_permission("voucher.create"))):
    try:
        res = payments.request_payment(
            db, company_id=user.company_id, supplier_id=payload.supplier_id,
            bank_account_id=payload.bank_account_id, amount=payload.amount,
            note=payload.note, requested_by=user.id)
        db.commit()
    except payments.PaymentError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return res


@router.get("/payments")
def list_payments(db: Session = Depends(get_company_db),
                  user: User = Depends(require_permission("voucher.create"))):
    rows = db.execute(select(PaymentApproval).where(PaymentApproval.company_id == user.company_id)
                      .order_by(PaymentApproval.created_at.desc())).scalars().all()
    out = []
    for p in rows[:200]:
        from app.models.masters import Supplier
        sup = db.get(Supplier, p.supplier_id)
        out.append({"id": str(p.id), "supplier": sup.name if sup else None,
                    "amount": str(p.amount), "status": p.status, "note": p.note,
                    "approval_request_id": str(p.approval_request_id) if p.approval_request_id else None})
    return out


# ------------------------------- requests -----------------------------------

class DecisionIn(BaseModel):
    note: str | None = None


def _request_out(db: Session, r: ApprovalRequest) -> dict:
    return {"id": str(r.id), "doc_type": r.doc_type, "title": r.title, "amount": str(r.amount),
            "status": r.status.value if hasattr(r.status, "value") else r.status,
            "approvals_required": r.approvals_required, "approvals_received": r.approvals_received,
            "events": [{"action": e.action.value if hasattr(e.action, "value") else e.action,
                        "note": e.note} for e in r.events]}


@router.get("/requests")
def list_requests(status_filter: str | None = None, db: Session = Depends(get_company_db),
                  user: User = Depends(require_permission("voucher.approve"))):
    stmt = select(ApprovalRequest).where(ApprovalRequest.company_id == user.company_id)
    if status_filter:
        stmt = stmt.where(ApprovalRequest.status == status_filter)
    rows = db.execute(stmt.order_by(ApprovalRequest.created_at.desc())).scalars().all()
    return [_request_out(db, r) for r in rows[:200]]


@router.post("/requests/{request_id}/approve")
def approve_request(request_id: uuid.UUID, payload: DecisionIn, db: Session = Depends(get_company_db),
                    user: User = Depends(require_permission("voucher.approve"))):
    req = db.get(ApprovalRequest, request_id)
    if req is None or req.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")
    from app.services import approval_exec
    try:
        res = svc.approve(db, req, approver_id=user.id, note=payload.note)
        executed = None
        if res["completed"]:
            executed = approval_exec.execute_approved(db, req, created_by=user.id)
        db.commit()
    except svc.ApprovalError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return {**res, "executed_ref": executed}


@router.post("/requests/{request_id}/reject")
def reject_request(request_id: uuid.UUID, payload: DecisionIn, db: Session = Depends(get_company_db),
                   user: User = Depends(require_permission("voucher.approve"))):
    req = db.get(ApprovalRequest, request_id)
    if req is None or req.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")
    from app.services import approval_exec
    try:
        res = svc.reject(db, req, approver_id=user.id, note=payload.note)
        approval_exec.on_rejected(db, req)
        db.commit()
    except svc.ApprovalError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return res
