"""Voucher posting routes."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_permission
from app.models.accounting import Voucher
from app.models.core import User
from app.schemas import ReverseRequest, VoucherCreate, VoucherOut
from app.services.accounting import posting_engine as pe

router = APIRouter(prefix="/api/vouchers", tags=["accounting"])


@router.post("", response_model=VoucherOut, status_code=status.HTTP_201_CREATED)
def create_voucher(
    payload: VoucherCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("voucher.post")),
) -> Voucher:
    req = pe.PostingRequest(
        company_id=user.company_id,
        voucher_type_code=payload.voucher_type_code,
        voucher_date=payload.voucher_date,
        posting_reference=payload.posting_reference,
        narration=payload.narration,
        branch_id=payload.branch_id,
        created_by=user.id,
        actor_name=user.username,
        lines=[
            pe.PostingLine(
                account_code=ln.account_code,
                debit=ln.debit,
                credit=ln.credit,
                narration=ln.narration,
                cost_centre_id=ln.cost_centre_id,
            )
            for ln in payload.lines
        ],
    )
    try:
        voucher = pe.post_voucher(db, req)
        db.commit()
    except pe.PostingError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    db.refresh(voucher)
    return voucher


@router.get("/{voucher_id}", response_model=VoucherOut)
def get_voucher(
    voucher_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("report.read")),
) -> Voucher:
    voucher = db.get(Voucher, voucher_id)
    if voucher is None or voucher.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Voucher not found")
    return voucher


@router.post("/{voucher_id}/reverse", response_model=VoucherOut)
def reverse_voucher(
    voucher_id: uuid.UUID,
    payload: ReverseRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("voucher.reverse")),
) -> Voucher:
    voucher = db.get(Voucher, voucher_id)
    if voucher is None or voucher.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Voucher not found")
    try:
        reversal = pe.reverse_voucher(
            db, voucher_id, reason=payload.reason, created_by=user.id, actor_name=user.username
        )
        db.commit()
    except pe.PostingError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    db.refresh(reversal)
    return reversal


@router.get("", response_model=None)
def list_vouchers(db: Session = Depends(get_db), user: User = Depends(require_permission("report.read"))):
    from sqlalchemy import select
    rows = db.execute(select(Voucher).where(Voucher.company_id == user.company_id)
                      .order_by(Voucher.voucher_date.desc(), Voucher.number.desc())).scalars().all()
    return [{"id": str(v.id), "number": v.number, "voucher_date": v.voucher_date.isoformat(),
             "status": v.status, "narration": v.narration,
             "total_debit": str(v.total_debit), "total_credit": str(v.total_credit)} for v in rows[:50]]
