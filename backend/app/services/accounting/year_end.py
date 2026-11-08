"""Year-end closing.

Posts a closing voucher that zeroes the income and expense accounts for a
fiscal year and moves the net result to Retained Earnings, then locks every
period in that year. Idempotent on the fiscal year.

After closing, the P&L for the year nets to zero (the closing entries offset the
activity) and the profit sits as a real balance in Retained Earnings, so the
Balance Sheet keeps balancing without double-counting.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.accounting import AccountingPeriod, PeriodStatus
from app.services.accounting import posting_engine as pe
from app.services.accounting.posting_rules import resolve_account
from app.services.accounting.trial_balance import trial_balance

MZERO = quantize(0, MONEY_SCALE)


class YearEndError(Exception):
    """A year-end close could not be performed."""


def _fy_bounds(fiscal_year: str) -> tuple[date, date]:
    # "2026-2027" -> 2026-04-01 .. 2027-03-31
    try:
        start_year = int(fiscal_year.split("-")[0])
    except (ValueError, IndexError) as exc:
        raise YearEndError(f"Bad fiscal year '{fiscal_year}'") from exc
    return date(start_year, 4, 1), date(start_year + 1, 3, 31)


def close_year(db: Session, *, company_id: uuid.UUID, fiscal_year: str,
               created_by: uuid.UUID | None = None) -> dict:
    ref = f"YEC:{company_id}:{fiscal_year}"
    existing = pe.find_existing(db, company_id, ref)
    if existing is not None:
        return {"already_closed": True, "voucher": existing, "fiscal_year": fiscal_year}

    start, end = _fy_bounds(fiscal_year)
    tb = trial_balance(db, company_id, date_from=start, date_to=end)

    lines = []
    income_total = MZERO
    expense_total = MZERO
    for row in tb["rows"]:
        net = Decimal(str(row["period_debit"])) - Decimal(str(row["period_credit"]))  # debit-positive
        if row["account_type"] == "INCOME":
            credit_bal = quantize(-net, MONEY_SCALE)   # income is credit-natural
            if credit_bal > 0:
                lines.append(pe.PostingLine(row["account_code"], debit=credit_bal,
                                            narration="Close income"))
                income_total += credit_bal
        elif row["account_type"] == "EXPENSE":
            debit_bal = quantize(net, MONEY_SCALE)
            if debit_bal > 0:
                lines.append(pe.PostingLine(row["account_code"], credit=debit_bal,
                                            narration="Close expense"))
                expense_total += debit_bal

    net_profit = quantize(income_total - expense_total, MONEY_SCALE)
    if not lines:
        raise YearEndError("Nothing to close for this fiscal year")

    re_acct = resolve_account(db, company_id, "YEAR_END", "retained_earnings")
    if net_profit > 0:
        lines.append(pe.PostingLine(re_acct, credit=net_profit, narration="Transfer profit to retained earnings"))
    elif net_profit < 0:
        lines.append(pe.PostingLine(re_acct, debit=quantize(-net_profit, MONEY_SCALE),
                                    narration="Transfer loss to retained earnings"))
    else:
        # Income and expense exactly offset: balance the close with a nil-effect
        # retained-earnings line is not needed; the lines already balance.
        pass

    voucher = pe.post_voucher(db, pe.PostingRequest(
        company_id=company_id, voucher_type_code="YEC", voucher_date=end, posting_reference=ref,
        narration=f"Year-end closing {fiscal_year}", source_module="year_end", created_by=created_by,
        lines=lines,
    ))

    # Lock every period in the year.
    periods = db.execute(
        select(AccountingPeriod).where(
            AccountingPeriod.company_id == company_id,
            AccountingPeriod.fiscal_year == fiscal_year,
        )
    ).scalars().all()
    for p in periods:
        p.status = PeriodStatus.CLOSED
    db.flush()

    return {"fiscal_year": fiscal_year, "income_total": income_total,
            "expense_total": expense_total, "net_profit": net_profit,
            "periods_closed": len(periods), "voucher": voucher}
