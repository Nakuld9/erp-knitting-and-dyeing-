"""Banking routes: receipts/payments/contra, statement import, reconciliation."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_company_db
from app.core.deps import require_permission
from app.models.core import User
from app.services import banking
from app.services.accounting import posting_engine as pe

router = APIRouter(prefix="/api/banking", tags=["banking"])


@router.get("/accounts")
def list_bank_accounts(db: Session = Depends(get_company_db),
                       user: User = Depends(require_permission("report.read"))):
    from sqlalchemy import select
    from app.models.masters import BankAccount
    rows = db.execute(select(BankAccount).where(BankAccount.company_id == user.company_id)).scalars().all()
    return [{"id": str(b.id), "name": f"{b.name} ({b.bank_name})", "bank_name": b.bank_name}
            for b in rows]


def _stringify(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _stringify(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stringify(v) for v in obj]
    return obj


class ReceiptIn(BaseModel):
    bank_account_id: uuid.UUID
    customer_id: uuid.UUID
    amount: Decimal
    date: date
    reference: str


class PaymentIn(BaseModel):
    bank_account_id: uuid.UUID
    supplier_id: uuid.UUID
    amount: Decimal
    date: date
    reference: str


class ContraIn(BaseModel):
    from_bank_account_id: uuid.UUID
    to_bank_account_id: uuid.UUID
    amount: Decimal
    date: date
    reference: str


class ImportIn(BaseModel):
    csv_text: str


def _post(db, fn, **kw):
    try:
        v = fn(db, **kw)
        db.commit()
        return v
    except (pe.PostingError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))


@router.post("/receipts", status_code=status.HTTP_201_CREATED)
def receipt(payload: ReceiptIn, db: Session = Depends(get_company_db),
            user: User = Depends(require_permission("voucher.post"))):
    v = _post(db, banking.customer_receipt, company_id=user.company_id,
              bank_account_id=payload.bank_account_id, customer_id=payload.customer_id,
              amount=payload.amount, vdate=payload.date, ref=payload.reference, created_by=user.id)
    return {"voucher_number": v.number, "amount": str(v.total_debit)}


@router.post("/payments", status_code=status.HTTP_201_CREATED)
def payment(payload: PaymentIn, db: Session = Depends(get_company_db),
            user: User = Depends(require_permission("voucher.post"))):
    v = _post(db, banking.supplier_payment, company_id=user.company_id,
              bank_account_id=payload.bank_account_id, supplier_id=payload.supplier_id,
              amount=payload.amount, vdate=payload.date, ref=payload.reference, created_by=user.id)
    return {"voucher_number": v.number, "amount": str(v.total_debit)}


@router.post("/contra", status_code=status.HTTP_201_CREATED)
def contra(payload: ContraIn, db: Session = Depends(get_company_db),
           user: User = Depends(require_permission("voucher.post"))):
    v = _post(db, banking.contra, company_id=user.company_id,
              from_bank_account_id=payload.from_bank_account_id,
              to_bank_account_id=payload.to_bank_account_id, amount=payload.amount,
              vdate=payload.date, ref=payload.reference, created_by=user.id)
    return {"voucher_number": v.number, "amount": str(v.total_debit)}


@router.post("/{bank_account_id}/import-statement")
def import_statement(bank_account_id: uuid.UUID, payload: ImportIn, db: Session = Depends(get_company_db),
                     user: User = Depends(require_permission("bank.reconcile"))):
    try:
        result = banking.import_statement_csv(
            db, company_id=user.company_id, bank_account_id=bank_account_id, csv_text=payload.csv_text
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return result


@router.post("/{bank_account_id}/reconcile")
def reconcile(bank_account_id: uuid.UUID, as_of: date | None = None, save: bool = True,
              db: Session = Depends(get_company_db),
              user: User = Depends(require_permission("bank.reconcile"))):
    try:
        result = banking.reconcile(db, company_id=user.company_id, bank_account_id=bank_account_id,
                                   as_of=as_of or date.today(), save=save)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return _stringify(result)
