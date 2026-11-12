"""GST adjustment vouchers, year-end closing, and overhead allocation."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_company_db
from app.core.deps import require_permission
from app.models.core import User
from app.models.overhead import AllocationBasis, OverheadAllocationRun, OverheadPool
from app.services import overhead as oh
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
def itc_reversal(payload: ITCReversalIn, db: Session = Depends(get_company_db),
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
def itc_reclaim(payload: ITCReversalIn, db: Session = Depends(get_company_db),
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
def round_off(payload: RoundOffIn, db: Session = Depends(get_company_db),
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
def rcm(payload: RCMIn, db: Session = Depends(get_company_db),
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
def manual_adjustment(payload: ManualIn, db: Session = Depends(get_company_db),
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
def close_year(payload: YearEndIn, db: Session = Depends(get_company_db),
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


# --------------------------- overhead allocation ----------------------------

class PoolIn(BaseModel):
    code: str
    name: str
    source_account_code: str
    basis: str = "OUTPUT_KG"


@router.post("/overhead/pools", status_code=status.HTTP_201_CREATED)
def create_pool(payload: PoolIn, db: Session = Depends(get_company_db),
                user: User = Depends(require_permission("voucher.post"))):
    try:
        basis = AllocationBasis(payload.basis)
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "basis must be OUTPUT_KG / MACHINE_HOURS / EQUAL")
    pool = OverheadPool(company_id=user.company_id, code=payload.code, name=payload.name,
                        source_account_code=payload.source_account_code, basis=basis, created_by=user.id)
    db.add(pool)
    db.commit()
    return {"id": str(pool.id), "code": pool.code, "name": pool.name, "basis": basis.value}


@router.get("/overhead/pools")
def list_pools(db: Session = Depends(get_company_db),
               user: User = Depends(require_permission("report.read"))):
    rows = db.execute(select(OverheadPool).where(OverheadPool.company_id == user.company_id,
                                                 OverheadPool.is_active.is_(True))).scalars().all()
    return [{"id": str(p.id), "code": p.code, "name": p.name,
             "source_account_code": p.source_account_code,
             "basis": p.basis.value if hasattr(p.basis, "value") else p.basis} for p in rows]


class AllocateIn(BaseModel):
    pool_id: uuid.UUID
    period_month: date
    amount: Decimal | None = None       # omit to absorb the source account's balance


@router.post("/overhead/allocate", status_code=status.HTTP_201_CREATED)
def allocate(payload: AllocateIn, db: Session = Depends(get_company_db),
             user: User = Depends(require_permission("voucher.post"))):
    pool = db.get(OverheadPool, payload.pool_id)
    if pool is None or pool.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Overhead pool not found")
    try:
        res = oh.allocate_overhead(db, company_id=user.company_id, pool=pool,
                                   period_month=payload.period_month, amount=payload.amount,
                                   created_by=user.id)
        db.commit()
    except oh.OverheadError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    voucher = res.get("voucher")
    return {"period": res["period"], "already_posted": res.get("already_posted", False),
            "total": str(res.get("total", "")), "machines_allocated": res.get("machines_allocated", 0),
            "voucher_number": voucher.number if voucher else None}
