"""GST adjustment vouchers and year-end closing."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_permission
from app.models.core import User
from app.services.accounting import gst_adjustment as gadj
from app.services.accounting import year_end

router = APIRouter(prefix="/api/accounting", tags=["accounting-ops"])


# --------------------------- GST adjustments --------------------------------

class ITCReversalIn(BaseModel):
    vdate: date
    reference: str
    cgst: Decimal = Decimal("0")
    sgst: Decimal = Decimal("0")
    igst: Decimal = Decimal("0")
    expense_account: str | None = None
    narration: str | None = None


@router.post("/gst-adjustments/itc-reversal", status_code=status.HTTP_201_CREATED)
def itc_reversal(payload: ITCReversalIn, db: Session = Depends(get_db),
                 user: User = Depends(require_permission("voucher.post"))):
    try:
        v = gadj.post_itc_reversal(db, company_id=user.company_id, vdate=payload.vdate,
                                   reference=payload.reference, cgst=payload.cgst, sgst=payload.sgst,
                                   igst=payload.igst, expense_account=payload.expense_account,
                                   narration=payload.narration, created_by=user.id)
        db.commit()
    except gadj.GSTAdjustmentError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return {"voucher_number": v.number, "total": str(v.total_debit)}


@router.post("/gst-adjustments/itc-reclaim", status_code=status.HTTP_201_CREATED)
def itc_reclaim(payload: ITCReversalIn, db: Session = Depends(get_db),
                user: User = Depends(require_permission("voucher.post"))):
    try:
        v = gadj.post_itc_reclaim(db, company_id=user.company_id, vdate=payload.vdate,
                                  reference=payload.reference, cgst=payload.cgst, sgst=payload.sgst,
                                  igst=payload.igst, expense_account=payload.expense_account,
                                  narration=payload.narration, created_by=user.id)
        db.commit()
    except gadj.GSTAdjustmentError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return {"voucher_number": v.number, "total": str(v.total_debit)}


class RoundOffIn(BaseModel):
    vdate: date
    reference: str
    amount: Decimal                       # signed: + gain, - cost
    counter_account: str | None = None
    narration: str | None = None


@router.post("/gst-adjustments/round-off", status_code=status.HTTP_201_CREATED)
def round_off(payload: RoundOffIn, db: Session = Depends(get_db),
              user: User = Depends(require_permission("voucher.post"))):
    try:
        v = gadj.post_round_off(db, company_id=user.company_id, vdate=payload.vdate,
                                reference=payload.reference, amount=payload.amount,
                                counter_account=payload.counter_account,
                                narration=payload.narration, created_by=user.id)
        db.commit()
    except gadj.GSTAdjustmentError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return {"voucher_number": v.number, "total": str(v.total_debit)}


class RCMIn(BaseModel):
    vdate: date
    reference: str
    cgst: Decimal = Decimal("0")
    sgst: Decimal = Decimal("0")
    igst: Decimal = Decimal("0")
    narration: str | None = None


@router.post("/gst-adjustments/rcm", status_code=status.HTTP_201_CREATED)
def rcm(payload: RCMIn, db: Session = Depends(get_db),
        user: User = Depends(require_permission("voucher.post"))):
    try:
        v = gadj.post_rcm(db, company_id=user.company_id, vdate=payload.vdate,
                          reference=payload.reference, cgst=payload.cgst, sgst=payload.sgst,
                          igst=payload.igst, narration=payload.narration, created_by=user.id)
        db.commit()
    except gadj.GSTAdjustmentError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return {"voucher_number": v.number, "total": str(v.total_debit)}


class ManualIn(BaseModel):
    vdate: date
    reference: str
    debit_account: str
    credit_account: str
    amount: Decimal
    narration: str | None = None


@router.post("/gst-adjustments/manual", status_code=status.HTTP_201_CREATED)
def manual_adjustment(payload: ManualIn, db: Session = Depends(get_db),
                      user: User = Depends(require_permission("voucher.post"))):
    try:
        v = gadj.post_manual(db, company_id=user.company_id, vdate=payload.vdate,
                             reference=payload.reference, debit_account=payload.debit_account,
                             credit_account=payload.credit_account, amount=payload.amount,
                             narration=payload.narration, created_by=user.id)
        db.commit()
    except gadj.GSTAdjustmentError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return {"voucher_number": v.number, "total": str(v.total_debit)}


# ----------------------------- year-end close -------------------------------

class YearEndIn(BaseModel):
    fiscal_year: str   # e.g. "2026-2027"


@router.post("/year-end/close", status_code=status.HTTP_201_CREATED)
def close_year(payload: YearEndIn, db: Session = Depends(get_db),
               user: User = Depends(require_permission("period.close"))):
    try:
        res = year_end.close_year(db, company_id=user.company_id,
                                  fiscal_year=payload.fiscal_year, created_by=user.id)
        db.commit()
    except year_end.YearEndError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    voucher = res.get("voucher")
    return {"fiscal_year": res["fiscal_year"], "already_closed": res.get("already_closed", False),
            "net_profit": str(res.get("net_profit", "")),
            "income_total": str(res.get("income_total", "")),
            "expense_total": str(res.get("expense_total", "")),
            "periods_closed": res.get("periods_closed", 0),
            "voucher_number": voucher.number if voucher else None}
