"""Financial statements derived from the posted ledger.

Profit & Loss, Balance Sheet, Cash Flow and debtor/creditor ageing. Everything
is computed from posted journal lines (via the trial balance), so the statements
are always consistent with the immutable ledger. The Balance Sheet folds the
period's computed profit into equity so it balances without a posted year-end
closing entry.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.accounting import ChartOfAccount, JournalLine, Voucher, VoucherStatus
from app.services.accounting.trial_balance import trial_balance

ZERO = quantize(0, MONEY_SCALE)
_POSTED = [s.value for s in (VoucherStatus.POSTED, VoucherStatus.REVERSED, VoucherStatus.REVERSAL)]


def _fy_start(d: date, start_month: int = 4) -> date:
    year = d.year if d.month >= start_month else d.year - 1
    return date(year, start_month, 1)


# --------------------------- Profit & Loss ---------------------------------

def profit_and_loss(db: Session, company_id: uuid.UUID, date_from: date, date_to: date) -> dict:
    tb = trial_balance(db, company_id, date_from=date_from, date_to=date_to)
    income, direct, indirect = [], [], []
    rev_total = direct_total = indirect_total = ZERO

    for row in tb["rows"]:
        period_net = row["period_debit"] - row["period_credit"]  # debit-positive
        if row["account_type"] == "INCOME":
            amount = -period_net  # income is credit-natural
            if amount != ZERO:
                income.append({"account_code": row["account_code"], "name": row["account_name"], "amount": amount})
                rev_total += amount
        elif row["account_type"] == "EXPENSE":
            amount = period_net
            if amount == ZERO:
                continue
            entry = {"account_code": row["account_code"], "name": row["account_name"], "amount": amount}
            if row["account_code"].startswith("5"):
                direct.append(entry); direct_total += amount
            else:
                indirect.append(entry); indirect_total += amount

    gross_profit = rev_total - direct_total
    net_profit = gross_profit - indirect_total
    return {
        "date_from": date_from, "date_to": date_to,
        "income": income, "direct_expenses": direct, "indirect_expenses": indirect,
        "revenue_total": rev_total, "direct_expense_total": direct_total,
        "indirect_expense_total": indirect_total,
        "gross_profit": gross_profit, "net_profit": net_profit,
    }


# ---------------- Manufacturing / Trading / Funds Flow ----------------------

def _tb_index(db, company_id, date_from, date_to):
    tb = trial_balance(db, company_id, date_from=date_from, date_to=date_to)
    return {r["account_code"]: r for r in tb["rows"]}


def _per_debit(idx, code):
    r = idx.get(code)
    return (r["period_debit"] - r["period_credit"]) if r else ZERO


def _per_credit(idx, code):
    r = idx.get(code)
    return (r["period_credit"] - r["period_debit"]) if r else ZERO


def _opening(idx, code):
    r = idx.get(code)
    return (r["opening_debit"] - r["opening_credit"]) if r else ZERO


def _closing(idx, code):
    r = idx.get(code)
    return (r["closing_debit"] - r["closing_credit"]) if r else ZERO


# Fabric stock = greige + finished goods; WIP = 1080.
STOCK_ACCOUNTS = ["1070", "1085"]


def manufacturing_account(db: Session, company_id: uuid.UUID, date_from: date, date_to: date) -> dict:
    """Cost of goods manufactured, built from the WIP (1080) account: opening WIP
    plus everything charged to production (materials, dispensing, overhead) less
    closing WIP equals the cost transferred out to finished stock."""
    idx = _tb_index(db, company_id, date_from, date_to)
    opening_wip = _opening(idx, "1080")
    closing_wip = _closing(idx, "1080")
    wip = idx.get("1080")
    charged = wip["period_debit"] if wip else ZERO     # gross debits into WIP (inputs)
    cogm = wip["period_credit"] if wip else ZERO       # gross credits out of WIP (to greige/FG)
    # Supplementary: direct-cost accounts that carry a period balance.
    DIRECT = ["5010", "5020", "5030", "5040", "5050", "5060", "5070", "5080",
              "5090", "5100", "5110", "5120", "5130", "5140"]
    breakdown = [{"account_code": c, "name": idx[c]["account_name"], "amount": _per_debit(idx, c)}
                 for c in DIRECT if c in idx and _per_debit(idx, c) != ZERO]
    return {"date_from": date_from, "date_to": date_to,
            "opening_wip": opening_wip, "charged_to_production": charged,
            "closing_wip": closing_wip, "cost_of_goods_manufactured": cogm,
            "direct_cost_breakdown": breakdown}


def trading_account(db: Session, company_id: uuid.UUID, date_from: date, date_to: date) -> dict:
    """Gross profit: sales less cost of goods sold, where COGS = opening fabric
    stock + cost of goods manufactured − closing fabric stock."""
    idx = _tb_index(db, company_id, date_from, date_to)
    sales = _per_credit(idx, "4010")
    opening_stock = sum((_opening(idx, c) for c in STOCK_ACCOUNTS), ZERO)
    closing_stock = sum((_closing(idx, c) for c in STOCK_ACCOUNTS), ZERO)
    cogm = manufacturing_account(db, company_id, date_from, date_to)["cost_of_goods_manufactured"]
    goods_available = quantize(opening_stock + cogm, MONEY_SCALE)
    cogs = quantize(goods_available - closing_stock, MONEY_SCALE)
    gross_profit = quantize(sales - cogs, MONEY_SCALE)
    return {"date_from": date_from, "date_to": date_to, "sales": sales,
            "opening_stock": opening_stock, "cost_of_goods_manufactured": cogm,
            "goods_available": goods_available, "closing_stock": closing_stock,
            "cost_of_goods_sold": cogs, "gross_profit": gross_profit}


def funds_flow(db: Session, company_id: uuid.UUID, date_from: date, date_to: date) -> dict:
    """Sources and applications of funds for the period (simplified): funds from
    operations (net profit + depreciation) and the movements in capital, loans
    and fixed assets; the balance is the net change in working capital."""
    idx = _tb_index(db, company_id, date_from, date_to)
    pnl = profit_and_loss(db, company_id, date_from, date_to)
    depreciation = _per_debit(idx, "6110")
    ffo = quantize(pnl["net_profit"] + depreciation, MONEY_SCALE)

    capital_introduced = sum((_per_credit(idx, c) for c in ["3010", "3020"]), ZERO)
    loans_raised = sum((_per_credit(idx, c) for c in ["2510", "2520", "2530"]), ZERO)
    FIXED = ["1510", "1520", "1530", "1531", "1532", "1533", "1540", "1550", "1560", "1570"]
    fixed_assets_bought = sum((_per_debit(idx, c) for c in FIXED), ZERO)
    drawings = _per_debit(idx, "3050")

    sources = []
    if ffo > 0:
        sources.append({"item": "Funds from operations", "amount": ffo})
    if capital_introduced > 0:
        sources.append({"item": "Capital introduced", "amount": capital_introduced})
    if loans_raised > 0:
        sources.append({"item": "Long-term loans raised", "amount": loans_raised})
    applications = []
    if ffo < 0:
        applications.append({"item": "Funds lost in operations", "amount": quantize(-ffo, MONEY_SCALE)})
    if fixed_assets_bought > 0:
        applications.append({"item": "Fixed assets purchased", "amount": fixed_assets_bought})
    if drawings > 0:
        applications.append({"item": "Drawings", "amount": drawings})

    total_sources = sum((s["amount"] for s in sources), ZERO)
    total_applications = sum((a["amount"] for a in applications), ZERO)
    net_working_capital_change = quantize(total_sources - total_applications, MONEY_SCALE)
    return {"date_from": date_from, "date_to": date_to,
            "funds_from_operations": ffo, "depreciation_addback": depreciation,
            "net_profit": pnl["net_profit"], "sources": sources, "applications": applications,
            "total_sources": total_sources, "total_applications": total_applications,
            "net_working_capital_change": net_working_capital_change}


# --------------------------- Balance Sheet ---------------------------------

def balance_sheet(db: Session, company_id: uuid.UUID, as_of: date,
                  *, fy_start: date | None = None) -> dict:
    fy_start = fy_start or _fy_start(as_of)
    tb = trial_balance(db, company_id, date_from=None, date_to=as_of)

    assets, liabilities, equity = [], [], []
    assets_total = liabilities_total = equity_total = ZERO

    for row in tb["rows"]:
        net_debit = row["closing_debit"] - row["closing_credit"]
        t = row["account_type"]
        if t == "ASSET":
            if net_debit != ZERO:
                assets.append({"account_code": row["account_code"], "name": row["account_name"], "amount": net_debit})
                assets_total += net_debit
        elif t == "LIABILITY":
            amt = -net_debit
            if amt != ZERO:
                liabilities.append({"account_code": row["account_code"], "name": row["account_name"], "amount": amt})
                liabilities_total += amt
        elif t == "EQUITY":
            amt = -net_debit
            if amt != ZERO:
                equity.append({"account_code": row["account_code"], "name": row["account_name"], "amount": amt})
                equity_total += amt

    # Current-year earnings (not yet closed to retained earnings) belong in equity.
    pnl = profit_and_loss(db, company_id, fy_start, as_of)
    current_profit = pnl["net_profit"]
    equity.append({"account_code": "3040", "name": "Current Year Profit (computed)", "amount": current_profit})
    equity_total += current_profit

    return {
        "as_of": as_of,
        "assets": assets, "liabilities": liabilities, "equity": equity,
        "assets_total": assets_total, "liabilities_total": liabilities_total,
        "equity_total": equity_total,
        "liabilities_and_equity_total": liabilities_total + equity_total,
        "is_balanced": assets_total == liabilities_total + equity_total,
    }


# ----------------------------- Cash Flow -----------------------------------

def _classify(code: str) -> str:
    if code.startswith("15"):
        return "investing"
    if code.startswith("3") or code.startswith("25") or code == "2070":
        return "financing"
    return "operating"


def cash_flow(db: Session, company_id: uuid.UUID, date_from: date, date_to: date) -> dict:
    cash_accts = db.execute(
        select(ChartOfAccount).where(
            ChartOfAccount.company_id == company_id,
            (ChartOfAccount.is_cash.is_(True)) | (ChartOfAccount.is_bank.is_(True)),
        )
    ).scalars().all()
    cash_ids = {a.id for a in cash_accts}
    code_by_id = {
        a.id: a.code
        for a in db.execute(select(ChartOfAccount).where(ChartOfAccount.company_id == company_id)).scalars()
    }

    # Opening cash balance (before the window).
    opening = ZERO
    for line, voucher in db.execute(
        select(JournalLine, Voucher).join(Voucher, JournalLine.voucher_id == Voucher.id).where(
            Voucher.company_id == company_id, Voucher.status.in_(_POSTED),
            JournalLine.account_id.in_(cash_ids), Voucher.voucher_date < date_from,
        )
    ):
        opening += (line.debit or ZERO) - (line.credit or ZERO)

    buckets = {"operating": ZERO, "investing": ZERO, "financing": ZERO}
    # Group period cash lines by voucher so we can read their counter accounts.
    by_voucher: dict[uuid.UUID, Decimal] = {}
    voucher_lines: dict[uuid.UUID, list[JournalLine]] = {}
    for line, voucher in db.execute(
        select(JournalLine, Voucher).join(Voucher, JournalLine.voucher_id == Voucher.id).where(
            Voucher.company_id == company_id, Voucher.status.in_(_POSTED),
            Voucher.voucher_date >= date_from, Voucher.voucher_date <= date_to,
        )
    ):
        voucher_lines.setdefault(voucher.id, []).append(line)
        if line.account_id in cash_ids:
            by_voucher[voucher.id] = by_voucher.get(voucher.id, ZERO) + (line.debit or ZERO) - (line.credit or ZERO)

    net_change = ZERO
    for vid, cash_delta in by_voucher.items():
        if cash_delta == ZERO:
            continue
        net_change += cash_delta
        counters = [l for l in voucher_lines[vid] if l.account_id not in cash_ids]
        if not counters:
            buckets["operating"] += cash_delta  # pure cash<->cash transfer (nets out overall)
            continue
        dominant = max(counters, key=lambda l: (l.debit or ZERO) + (l.credit or ZERO))
        buckets[_classify(code_by_id.get(dominant.account_id, ""))] += cash_delta

    closing = opening + net_change
    return {
        "date_from": date_from, "date_to": date_to,
        "opening_cash": opening, "closing_cash": closing, "net_change": net_change,
        "operating": buckets["operating"], "investing": buckets["investing"],
        "financing": buckets["financing"],
        "is_reconciled": net_change == buckets["operating"] + buckets["investing"] + buckets["financing"],
    }


# ----------------------------- Ageing --------------------------------------

def _ageing(db: Session, company_id: uuid.UUID, account_code: str, party_type: str,
            as_of: date, invoice_on: str) -> dict:
    """FIFO ageing of open items on a control account.

    invoice_on='debit' for debtors (AR: invoices debit, receipts credit);
    'credit' for creditors (AP: invoices credit, payments debit).
    """
    acct = db.execute(
        select(ChartOfAccount).where(ChartOfAccount.company_id == company_id, ChartOfAccount.code == account_code)
    ).scalar_one_or_none()
    if acct is None:
        return {"parties": [], "total": ZERO}

    rows = db.execute(
        select(JournalLine, Voucher).join(Voucher, JournalLine.voucher_id == Voucher.id).where(
            Voucher.company_id == company_id, Voucher.status.in_(_POSTED),
            JournalLine.account_id == acct.id, JournalLine.party_type == party_type,
            Voucher.voucher_date <= as_of,
        ).order_by(Voucher.voucher_date, Voucher.number)
    ).all()

    per_party: dict[uuid.UUID, dict] = {}
    for line, voucher in rows:
        p = per_party.setdefault(line.party_id, {"open": [], "payments": ZERO})
        inv_amt = (line.debit if invoice_on == "debit" else line.credit) or ZERO
        pay_amt = (line.credit if invoice_on == "debit" else line.debit) or ZERO
        if inv_amt > ZERO:
            p["open"].append([voucher.voucher_date, inv_amt])
        if pay_amt > ZERO:
            p["payments"] += pay_amt

    parties = []
    grand_total = ZERO
    for party_id, data in per_party.items():
        pay = data["payments"]
        # Apply payments FIFO to oldest invoices.
        for item in data["open"]:
            if pay <= ZERO:
                break
            applied = item[1] if item[1] <= pay else pay
            item[1] -= applied
            pay -= applied
        buckets = {"0-30": ZERO, "31-60": ZERO, "61-90": ZERO, "90+": ZERO}
        outstanding = ZERO
        for inv_date, amt in data["open"]:
            if amt <= ZERO:
                continue
            age = (as_of - inv_date).days
            key = "0-30" if age <= 30 else "31-60" if age <= 60 else "61-90" if age <= 90 else "90+"
            buckets[key] += amt
            outstanding += amt
        if outstanding != ZERO or pay != ZERO:
            parties.append({"party_id": str(party_id), "outstanding": outstanding,
                            "unapplied_payment": pay, **buckets})
            grand_total += outstanding
    return {"as_of": as_of, "parties": parties, "total": grand_total}


def debtors_ageing(db: Session, company_id: uuid.UUID, as_of: date) -> dict:
    return _ageing(db, company_id, "1040", "customer", as_of, invoice_on="debit")


def creditors_ageing(db: Session, company_id: uuid.UUID, as_of: date) -> dict:
    return _ageing(db, company_id, "2010", "supplier", as_of, invoice_on="credit")
