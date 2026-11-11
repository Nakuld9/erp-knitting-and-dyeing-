"""Supplier payments routed through the approval workflow.

A payment at or above the configured threshold is held as a pending approval
and only posts once it has the required approvals (from someone other than the
requester). Below the threshold — or with no rule — it posts straight away.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.approvals import (
    ApprovalRequest,
    ApprovalStatus,
    PaymentApproval,
    PaymentStatus,
)
from app.models.masters import Supplier
from app.services import approvals, banking

DOC_TYPE = "PAYMENT"


class PaymentError(Exception):
    """A payment action was invalid."""


def _post(db: Session, pay: PaymentApproval, *, created_by: uuid.UUID | None = None):
    ref = f"PAY:{pay.id}"
    supplier = db.get(Supplier, pay.supplier_id)
    voucher = banking.supplier_payment(
        db, company_id=pay.company_id, bank_account_id=pay.bank_account_id,
        supplier_id=pay.supplier_id, amount=Decimal(str(pay.amount)),
        vdate=date.today(), ref=ref, created_by=created_by,
    )
    pay.status = PaymentStatus.PAID
    pay.posting_reference = ref
    db.flush()
    return voucher


def request_payment(db: Session, *, company_id: uuid.UUID, supplier_id: uuid.UUID,
                    bank_account_id: uuid.UUID, amount, note: str | None = None,
                    requested_by: uuid.UUID | None = None) -> dict:
    amt = quantize(Decimal(str(amount or 0)), MONEY_SCALE)
    if amt <= 0:
        raise PaymentError("Payment amount must be positive")
    pay = PaymentApproval(company_id=company_id, supplier_id=supplier_id,
                          bank_account_id=bank_account_id, amount=amt, note=note,
                          status=PaymentStatus.PENDING, created_by=requested_by)
    db.add(pay)
    db.flush()

    rule = approvals.rule_for(db, company_id, DOC_TYPE, amt)
    if rule is None:
        # No approval needed — post immediately.
        voucher = _post(db, pay, created_by=requested_by)
        return {"payment_id": str(pay.id), "status": pay.status.value, "approval_required": False,
                "voucher_number": voucher.number}

    supplier = db.get(Supplier, supplier_id)
    req = approvals.create_request(
        db, company_id=company_id, doc_type=DOC_TYPE, doc_id=pay.id,
        title=f"Payment to {supplier.name if supplier else supplier_id}: {amt}",
        amount=amt, requested_by=requested_by, rule=rule,
    )
    pay.approval_request_id = req.id
    db.flush()
    return {"payment_id": str(pay.id), "status": pay.status.value, "approval_required": True,
            "approval_request_id": str(req.id), "approvals_required": req.approvals_required}


def post_approved_payment(db: Session, request: ApprovalRequest, *,
                          created_by: uuid.UUID | None = None):
    """Called after an approval request for a payment reaches APPROVED."""
    if request.status != ApprovalStatus.APPROVED:
        raise PaymentError("Approval request is not approved")
    pay = db.get(PaymentApproval, request.doc_id)
    if pay is None or pay.company_id != request.company_id:
        raise PaymentError("Payment not found")
    if pay.status == PaymentStatus.PAID:
        return None
    return _post(db, pay, created_by=created_by)


def reject_payment(db: Session, request: ApprovalRequest) -> None:
    pay = db.get(PaymentApproval, request.doc_id)
    if pay is not None and pay.status == PaymentStatus.PENDING:
        pay.status = PaymentStatus.REJECTED
        db.flush()
