"""Journals / vouchers routed through the maker-checker approval workflow.

A voucher at or above the configured threshold for doc_type ``VOUCHER`` is held
as a :class:`PendingVoucher` (its full posting payload stored verbatim) and only
posted once it has the required approvals from someone other than the requester.
Below the threshold — or with no rule — it posts straight away.

The payload is validated (balanced, one-sided lines, positive) *before* it is
stored, so an unbalanced or unpostable entry is rejected at submit time and is
never parked as a pending approval.
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
    PendingVoucher,
    PendingVoucherStatus,
)
from app.services import approvals
from app.services.accounting import posting_engine as pe

DOC_TYPE = "VOUCHER"


class VoucherApprovalError(Exception):
    """A voucher submitted for approval was invalid (unbalanced, empty, ...)."""


def _d(v) -> Decimal:
    return quantize(Decimal(str(v or 0)), MONEY_SCALE)


def _validate(lines: list[dict]) -> Decimal:
    """Return the total debit after checking the lines are postable."""
    if len(lines) < 2:
        raise VoucherApprovalError("A voucher needs at least two lines")
    total_dr = total_cr = Decimal("0")
    for ln in lines:
        dr, cr = _d(ln.get("debit")), _d(ln.get("credit"))
        if dr < 0 or cr < 0:
            raise VoucherApprovalError("Line amounts cannot be negative")
        if (dr > 0) == (cr > 0):
            raise VoucherApprovalError("Each line must be exactly one of debit or credit")
        total_dr += dr
        total_cr += cr
    if total_dr != total_cr:
        raise VoucherApprovalError(f"Voucher is unbalanced: debit {total_dr} != credit {total_cr}")
    if total_dr <= 0:
        raise VoucherApprovalError("Voucher total must be positive")
    return total_dr


def _posting_lines(lines: list[dict]) -> list[pe.PostingLine]:
    out = []
    for ln in lines:
        cc = ln.get("cost_centre_id")
        out.append(pe.PostingLine(
            account_code=ln["account_code"], debit=_d(ln.get("debit")), credit=_d(ln.get("credit")),
            narration=ln.get("narration"),
            cost_centre_id=uuid.UUID(str(cc)) if cc else None,
        ))
    return out


def _post(db: Session, pv: PendingVoucher, *, created_by: uuid.UUID | None = None):
    ref = f"VCHAP:{pv.id}"
    existing = pe.find_existing(db, pv.company_id, ref)
    if existing is not None:
        voucher = existing
    else:
        req = pe.PostingRequest(
            company_id=pv.company_id, voucher_type_code=pv.voucher_type_code,
            voucher_date=pv.voucher_date, posting_reference=ref, narration=pv.narration,
            branch_id=pv.branch_id, created_by=created_by, approved_by=created_by,
            source_module="approval", source_id=str(pv.id), lines=_posting_lines(pv.lines),
        )
        voucher = pe.post_voucher(db, req)
    pv.status = PendingVoucherStatus.POSTED
    pv.posting_reference = ref
    pv.posted_voucher_id = voucher.id
    db.flush()
    return voucher


def submit_voucher(db: Session, *, company_id: uuid.UUID, voucher_type_code: str,
                   voucher_date: date, lines: list[dict], narration: str | None = None,
                   branch_id: uuid.UUID | None = None,
                   requested_by: uuid.UUID | None = None) -> dict:
    amount = _validate(lines)
    stored = [{"account_code": ln["account_code"], "debit": str(_d(ln.get("debit"))),
               "credit": str(_d(ln.get("credit"))), "narration": ln.get("narration"),
               "cost_centre_id": str(ln["cost_centre_id"]) if ln.get("cost_centre_id") else None}
              for ln in lines]
    pv = PendingVoucher(company_id=company_id, voucher_type_code=voucher_type_code,
                        voucher_date=voucher_date, narration=narration, branch_id=branch_id,
                        lines=stored, amount=amount, status=PendingVoucherStatus.PENDING,
                        created_by=requested_by)
    db.add(pv)
    db.flush()

    rule = approvals.rule_for(db, company_id, DOC_TYPE, amount)
    if rule is None:
        voucher = _post(db, pv, created_by=requested_by)
        return {"pending_voucher_id": str(pv.id), "status": pv.status.value,
                "approval_required": False, "voucher_number": voucher.number}

    req = approvals.create_request(
        db, company_id=company_id, doc_type=DOC_TYPE, doc_id=pv.id,
        title=f"{voucher_type_code} voucher: ₹{amount.quantize(Decimal('0.01')):,}",
        amount=amount, requested_by=requested_by, rule=rule,
    )
    pv.approval_request_id = req.id
    db.flush()
    return {"pending_voucher_id": str(pv.id), "status": pv.status.value, "approval_required": True,
            "approval_request_id": str(req.id), "approvals_required": req.approvals_required}


def post_approved_voucher(db: Session, request: ApprovalRequest, *,
                          created_by: uuid.UUID | None = None):
    """Called after an approval request for a voucher reaches APPROVED."""
    if request.status != ApprovalStatus.APPROVED:
        raise VoucherApprovalError("Approval request is not approved")
    pv = db.get(PendingVoucher, request.doc_id)
    if pv is None or pv.company_id != request.company_id:
        raise VoucherApprovalError("Pending voucher not found")
    if pv.status == PendingVoucherStatus.POSTED:
        return None
    return _post(db, pv, created_by=created_by)


def reject_voucher(db: Session, request: ApprovalRequest) -> None:
    pv = db.get(PendingVoucher, request.doc_id)
    if pv is not None and pv.status == PendingVoucherStatus.PENDING:
        pv.status = PendingVoucherStatus.REJECTED
        db.flush()
