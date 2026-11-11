"""Customer / supplier statements and party outstanding, from the posted ledger.

A statement is the party's opening balance, every transaction in the period with
a running balance, and the closing balance — built from the control-account
journal lines tagged with the party.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.accounting import ChartOfAccount, JournalLine, Voucher, VoucherStatus

ZERO = quantize(0, MONEY_SCALE)
_POSTED = [s.value for s in (VoucherStatus.POSTED, VoucherStatus.REVERSED, VoucherStatus.REVERSAL)]

# Receivables are debit-natural (customer owes us), payables credit-natural.
AR_ACCOUNT = "1040"
AP_ACCOUNT = "2010"


def _account(db: Session, company_id: uuid.UUID, code: str) -> ChartOfAccount | None:
    return db.execute(select(ChartOfAccount).where(
        ChartOfAccount.company_id == company_id, ChartOfAccount.code == code)).scalar_one_or_none()


def _lines(db: Session, company_id: uuid.UUID, acct_id, party_type: str, party_id: uuid.UUID,
           up_to: date | None = None):
    stmt = (select(JournalLine, Voucher).join(Voucher, JournalLine.voucher_id == Voucher.id)
            .where(Voucher.company_id == company_id, Voucher.status.in_(_POSTED),
                   JournalLine.account_id == acct_id, JournalLine.party_type == party_type,
                   JournalLine.party_id == party_id))
    if up_to is not None:
        stmt = stmt.where(Voucher.voucher_date <= up_to)
    return db.execute(stmt.order_by(Voucher.voucher_date, Voucher.number)).all()


def party_statement(db: Session, company_id: uuid.UUID, *, account_code: str, party_type: str,
                    party_id: uuid.UUID, date_from: date, date_to: date, debit_positive: bool) -> dict:
    acct = _account(db, company_id, account_code)
    if acct is None:
        return {"opening": ZERO, "rows": [], "closing": ZERO}

    sign = Decimal("1") if debit_positive else Decimal("-1")
    # Opening balance = net movement strictly before date_from.
    opening = ZERO
    for line, v in _lines(db, company_id, acct.id, party_type, party_id):
        delta = quantize((Decimal(str(line.debit)) - Decimal(str(line.credit))) * sign, MONEY_SCALE)
        if v.voucher_date < date_from:
            opening = quantize(opening + delta, MONEY_SCALE)

    running = opening
    rows = []
    for line, v in _lines(db, company_id, acct.id, party_type, party_id, up_to=date_to):
        if v.voucher_date < date_from:
            continue
        debit = quantize(Decimal(str(line.debit)), MONEY_SCALE)
        credit = quantize(Decimal(str(line.credit)), MONEY_SCALE)
        running = quantize(running + (debit - credit) * sign, MONEY_SCALE)
        rows.append({"date": v.voucher_date, "voucher": v.number, "narration": line.narration or v.narration,
                     "debit": debit, "credit": credit, "balance": running})
    return {"opening": opening, "rows": rows, "closing": running}


def customer_statement(db: Session, company_id: uuid.UUID, customer_id: uuid.UUID,
                       date_from: date, date_to: date) -> dict:
    return party_statement(db, company_id, account_code=AR_ACCOUNT, party_type="customer",
                           party_id=customer_id, date_from=date_from, date_to=date_to, debit_positive=True)


def supplier_statement(db: Session, company_id: uuid.UUID, supplier_id: uuid.UUID,
                       date_from: date, date_to: date) -> dict:
    return party_statement(db, company_id, account_code=AP_ACCOUNT, party_type="supplier",
                           party_id=supplier_id, date_from=date_from, date_to=date_to, debit_positive=False)


def customer_outstanding(db: Session, company_id: uuid.UUID, customer_id: uuid.UUID) -> Decimal:
    """Net receivable owed by a customer (debit - credit on AR)."""
    acct = _account(db, company_id, AR_ACCOUNT)
    if acct is None:
        return ZERO
    total = ZERO
    for line, _v in _lines(db, company_id, acct.id, "customer", customer_id):
        total = quantize(total + Decimal(str(line.debit)) - Decimal(str(line.credit)), MONEY_SCALE)
    return total
