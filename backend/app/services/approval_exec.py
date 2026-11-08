"""Execute the action behind an approved request, dispatched by document type.

The maker-checker engine (services/approvals.py) is generic; this module runs
the right thing once a request is fully approved (or rejected), so new document
types plug in here without touching the engine.
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models.approvals import ApprovalRequest

PAYMENT = "PAYMENT"
PURCHASE_ORDER = "PURCHASE_ORDER"
VOUCHER = "VOUCHER"


def execute_approved(db: Session, request: ApprovalRequest, *, created_by: uuid.UUID | None = None):
    """Run the approved document's action. Returns a short reference string."""
    if request.doc_type == PAYMENT:
        from app.services import payments
        v = payments.post_approved_payment(db, request, created_by=created_by)
        return v.number if v else None
    if request.doc_type == PURCHASE_ORDER:
        from app.models.commercial import DocStatus, PurchaseOrder
        from app.services import order_lifecycle as ol
        po = db.get(PurchaseOrder, request.doc_id)
        if po is not None and po.company_id == request.company_id and po.status == DocStatus.DRAFT:
            ol.confirm_purchase_order(db, po)
            return po.number
        return po.number if po is not None else None
    if request.doc_type == VOUCHER:
        from app.services import voucher_approvals
        v = voucher_approvals.post_approved_voucher(db, request, created_by=created_by)
        return v.number if v else None
    return None


def on_rejected(db: Session, request: ApprovalRequest) -> None:
    if request.doc_type == PAYMENT:
        from app.services import payments
        payments.reject_payment(db, request)
    elif request.doc_type == VOUCHER:
        from app.services import voucher_approvals
        voucher_approvals.reject_voucher(db, request)
    # Purchase orders simply stay in DRAFT when rejected.
