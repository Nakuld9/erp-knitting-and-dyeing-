"""Management dashboard aggregations (owner / accounts / factory / chemical).

All figures are derived from the posted ledger and operational tables, reusing
the statement, GST and reconciliation services so a dashboard number always
matches the report it drills into.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, QTY_SCALE, quantize
from app.models.accounting import ChartOfAccount, JournalLine, Voucher, VoucherStatus, VoucherType
from app.models.dispensing import DispensingTransaction, DosingStatus
from app.models.manufacturing import BatchStatus, DyeBatch, KnittingProduction
from app.models.masters import BankAccount, Chemical
from app.services.accounting import financial_statements as fs
from app.services.accounting import gst as gst_svc
from app.services.accounting import trial_balance as tb
from app.services.inventory import balance as inv_balance
from app.models.inventory import ItemType

ZERO = quantize(0, MONEY_SCALE)
_POSTED = [s.value for s in (VoucherStatus.POSTED, VoucherStatus.REVERSED, VoucherStatus.REVERSAL)]

INVENTORY_ACCOUNTS = ["1060", "1070", "1080", "1085", "1090", "1100", "1110"]


def _fy_start(d: date) -> date:
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1)


def _acct_balance(db, company_id, code, as_of=None):
    acct = db.execute(
        select(ChartOfAccount).where(ChartOfAccount.company_id == company_id, ChartOfAccount.code == code)
    ).scalar_one_or_none()
    if acct is None:
        return ZERO
    stmt = (select(JournalLine.debit, JournalLine.credit)
            .join(Voucher, JournalLine.voucher_id == Voucher.id)
            .where(Voucher.company_id == company_id, Voucher.status.in_(_POSTED),
                   JournalLine.account_id == acct.id))
    if as_of is not None:
        stmt = stmt.where(Voucher.voucher_date <= as_of)
    bal = ZERO
    for d, c in db.execute(stmt):
        bal += (d or ZERO) - (c or ZERO)
    return bal


def _cash_and_bank(db, company_id, as_of):
    accts = db.execute(
        select(ChartOfAccount.code).where(
            ChartOfAccount.company_id == company_id,
            (ChartOfAccount.is_cash.is_(True)) | (ChartOfAccount.is_bank.is_(True)))
    ).scalars().all()
    return sum((_acct_balance(db, company_id, c, as_of) for c in accts), ZERO)


def _sales_trend(db, company_id, as_of, months=6):
    """Sales (4010) credited per calendar month, last `months` months."""
    acct = db.execute(
        select(ChartOfAccount).where(ChartOfAccount.company_id == company_id, ChartOfAccount.code == "4010")
    ).scalar_one_or_none()
    out = []
    if acct is None:
        return out
    y, m = as_of.year, as_of.month
    buckets = []
    for _ in range(months):
        buckets.append((y, m))
        m -= 1
        if m == 0:
            m = 12; y -= 1
    buckets.reverse()
    for (yy, mm) in buckets:
        start = date(yy, mm, 1)
        end = date(yy + (mm == 12), 1 if mm == 12 else mm + 1, 1)
        total = ZERO
        for d, c in db.execute(
            select(JournalLine.debit, JournalLine.credit)
            .join(Voucher, JournalLine.voucher_id == Voucher.id)
            .where(Voucher.company_id == company_id, Voucher.status.in_(_POSTED),
                   JournalLine.account_id == acct.id,
                   Voucher.voucher_date >= start, Voucher.voucher_date < end)):
            total += (c or ZERO) - (d or ZERO)
        out.append({"month": start.strftime("%b %Y"), "amount": total})
    return out


def _pct_change(cur, prev) -> float | None:
    """Percentage change cur vs prev; None when there is no prior base."""
    prev = Decimal(str(prev or 0))
    if prev == 0:
        return None
    return float(quantize((Decimal(str(cur)) - prev) / abs(prev) * Decimal("100"), QTY_SCALE))


def owner_dashboard(db: Session, company_id: uuid.UUID, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    pnl = fs.profit_and_loss(db, company_id, _fy_start(as_of), as_of)
    inventory_value = sum((_acct_balance(db, company_id, c, as_of) for c in INVENTORY_ACCOUNTS), ZERO)

    # Honest "vs last month": trailing 30 days vs the prior 30 days.
    cur = fs.profit_and_loss(db, company_id, as_of - timedelta(days=29), as_of)
    prev = fs.profit_and_loss(db, company_id, as_of - timedelta(days=59), as_of - timedelta(days=30))
    deltas = {
        "revenue": _pct_change(cur["revenue_total"], prev["revenue_total"]),
        "gross_profit": _pct_change(cur["gross_profit"], prev["gross_profit"]),
        "net_profit": _pct_change(cur["net_profit"], prev["net_profit"]),
    }
    return {
        "as_of": as_of,
        "revenue": pnl["revenue_total"],
        "gross_profit": pnl["gross_profit"],
        "net_profit": pnl["net_profit"],
        "cash_and_bank": _cash_and_bank(db, company_id, as_of),
        "receivables": _acct_balance(db, company_id, "1040", as_of),
        "payables": _acct_balance(db, company_id, "2010", as_of),
        "inventory_value": inventory_value,
        "deltas": deltas,
        "sales_trend": _sales_trend(db, company_id, as_of),
    }


def accounts_dashboard(db: Session, company_id: uuid.UUID, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()

    def _day_total(vtype_code):
        vt = db.execute(select(VoucherType).where(VoucherType.code == vtype_code)).scalar_one_or_none()
        if vt is None:
            return ZERO
        total = db.execute(
            select(func.count()).select_from(Voucher).where(
                Voucher.company_id == company_id, Voucher.voucher_type_id == vt.id,
                Voucher.voucher_date == as_of, Voucher.status.in_(_POSTED))
        ).scalar_one()
        if not total:
            return ZERO
        s = ZERO
        for (td,) in db.execute(
            select(Voucher.total_debit).where(
                Voucher.company_id == company_id, Voucher.voucher_type_id == vt.id,
                Voucher.voucher_date == as_of, Voucher.status.in_(_POSTED))):
            s += td or ZERO
        return s

    banks = []
    for ba in db.execute(select(BankAccount).where(BankAccount.company_id == company_id)).scalars():
        code = None
        if ba.coa_account_id:
            coa = db.get(ChartOfAccount, ba.coa_account_id)
            code = coa.code if coa else None
        banks.append({"name": ba.name,
                      "balance": _acct_balance(db, company_id, code, as_of) if code else ZERO})

    g = gst_svc.gstr3b_summary(db, company_id, _fy_start(as_of), as_of)
    trial = tb.trial_balance(db, company_id, date_to=as_of)
    return {
        "as_of": as_of,
        "today_receipts": _day_total("RV"),
        "today_payments": _day_total("PV"),
        "bank_balances": banks,
        "debtors_total": fs.debtors_ageing(db, company_id, as_of)["total"],
        "creditors_total": fs.creditors_ageing(db, company_id, as_of)["total"],
        "gst_net_payable": g["net_tax_payable"]["total"],
        "trial_balance_balanced": trial["is_balanced"],
    }


def production_trend(db: Session, company_id: uuid.UUID, as_of: date | None = None, days: int = 14) -> list[dict]:
    """Daily knitting and dyeing output (kg) for the last ``days`` days."""
    from app.models.manufacturing import DyeBath
    as_of = as_of or date.today()
    start = as_of - timedelta(days=days - 1)
    series = {start + timedelta(days=i): {"knitting_kg": ZERO, "dyeing_kg": ZERO} for i in range(days)}

    knit = db.execute(
        select(KnittingProduction.production_date, func.sum(KnittingProduction.produced_kg))
        .where(KnittingProduction.company_id == company_id,
               KnittingProduction.production_date >= start, KnittingProduction.production_date <= as_of)
        .group_by(KnittingProduction.production_date)).all()
    for d, kg in knit:
        if d in series:
            series[d]["knitting_kg"] = quantize(kg or 0, QTY_SCALE)

    baths = db.execute(select(DyeBath.actual_start, DyeBath.fabric_kg).where(
        DyeBath.company_id == company_id)).all()
    for ts, kg in baths:
        if ts is None:
            continue
        d = ts.date() if hasattr(ts, "date") else ts
        if d in series:
            series[d]["dyeing_kg"] = quantize(Decimal(str(series[d]["dyeing_kg"])) + Decimal(str(kg or 0)), QTY_SCALE)

    return [{"date": d.isoformat(), "knitting_kg": v["knitting_kg"], "dyeing_kg": v["dyeing_kg"]}
            for d, v in sorted(series.items())]


def factory_dashboard(db: Session, company_id: uuid.UUID, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    knit_kg = db.execute(
        select(func.coalesce(func.sum(KnittingProduction.produced_kg), 0)).where(
            KnittingProduction.company_id == company_id)
    ).scalar_one()
    active_batches = db.execute(
        select(func.count()).select_from(DyeBatch).where(
            DyeBatch.company_id == company_id, DyeBatch.status == BatchStatus.RUNNING.value)
    ).scalar_one()
    dyed_kg = db.execute(
        select(func.coalesce(func.sum(DyeBatch.fabric_kg), 0)).where(DyeBatch.company_id == company_id)
    ).scalar_one()
    chem_cost = db.execute(
        select(func.coalesce(func.sum(DispensingTransaction.consumption_cost), 0)).where(
            DispensingTransaction.company_id == company_id)
    ).scalar_one()
    return {
        "as_of": as_of,
        "knitting_output_kg": quantize(knit_kg, QTY_SCALE),
        "dyeing_output_kg": quantize(dyed_kg, QTY_SCALE),
        "active_batches": active_batches,
        "chemical_consumption_cost": quantize(chem_cost, MONEY_SCALE),
    }


def chemical_dashboard(db: Session, company_id: uuid.UUID, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    txns = db.execute(
        select(DispensingTransaction).where(DispensingTransaction.company_id == company_id)
    ).scalars().all()
    dye_cost = chem_cost = ZERO
    by_material: dict[str, object] = {}
    errors = 0
    for t in txns:
        if t.dosing_status == DosingStatus.FAILED.value:
            errors += 1
            continue
        chem = db.get(Chemical, t.chemical_id)
        cost = t.consumption_cost or ZERO
        if chem and chem.is_dye:
            dye_cost += cost
        else:
            chem_cost += cost
        key = t.material_code or (chem.code if chem else "?")
        by_material[key] = (by_material.get(key, ZERO) or ZERO) + cost

    # Stock alerts: chemicals whose on-hand qty is below the reorder level (>0).
    alerts = []
    for chem in db.execute(select(Chemical).where(Chemical.company_id == company_id)).scalars():
        reorder = chem.reorder_level or ZERO
        if reorder and reorder > 0:
            onhand = inv_balance(db, company_id=company_id, item_type=ItemType.CHEMICAL,
                                 item_id=chem.id)["qty_on_hand"]
            if onhand < reorder:
                alerts.append({"code": chem.code, "name": chem.name,
                               "on_hand": onhand, "reorder_level": reorder})

    top = sorted(({"material": k, "cost": v} for k, v in by_material.items()),
                 key=lambda r: r["cost"], reverse=True)[:8]
    return {
        "as_of": as_of,
        "dye_cost": dye_cost,
        "chemical_cost": chem_cost,
        "total_consumption_cost": dye_cost + chem_cost,
        "dispensing_errors": errors,
        "stock_alerts": alerts,
        "top_consumption": top,
    }
