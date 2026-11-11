"""Overhead absorption: spread an overhead pool across the work centres.

For a period, a pool's amount (an explicit figure, or the balance sitting in its
source expense account) is apportioned to each active machine by the chosen
basis and posted: Dr WIP tagged with the machine's work-centre cost centre /
Cr the source expense account. The overhead is thus absorbed into production and
shows up as each work centre's loaded cost in the cost-centre report.
"""
from __future__ import annotations

import calendar
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, QTY_SCALE, quantize
from app.models.accounting import ChartOfAccount, JournalLine, Voucher, VoucherStatus
from app.models.manufacturing import DyeBath, KnittingProduction
from app.models.masters import Machine
from app.models.overhead import (
    AllocationBasis,
    OverheadAllocationLine,
    OverheadAllocationRun,
    OverheadPool,
)
from app.services import work_centres
from app.services.accounting import posting_engine as pe
from app.services.accounting.posting_rules import resolve_account

MZERO = quantize(0, MONEY_SCALE)
QZERO = quantize(0, QTY_SCALE)
_POSTED = [s.value for s in (VoucherStatus.POSTED, VoucherStatus.REVERSED, VoucherStatus.REVERSAL)]


class OverheadError(Exception):
    """An overhead allocation could not be performed."""


def _D(v) -> Decimal:
    return Decimal(str(v or 0))


def _bounds(period_month: date) -> tuple[date, date]:
    first = period_month.replace(day=1)
    last = period_month.replace(day=calendar.monthrange(period_month.year, period_month.month)[1])
    return first, last


def _source_balance(db: Session, company_id: uuid.UUID, code: str, frm: date, to: date) -> Decimal:
    acc = db.execute(select(ChartOfAccount).where(
        ChartOfAccount.company_id == company_id, ChartOfAccount.code == code)).scalar_one_or_none()
    if acc is None:
        raise OverheadError(f"Source account {code} not found")
    rows = db.execute(
        select(JournalLine.debit, JournalLine.credit)
        .join(Voucher, JournalLine.voucher_id == Voucher.id)
        .where(Voucher.company_id == company_id, Voucher.status.in_(_POSTED),
               Voucher.voucher_date >= frm, Voucher.voucher_date <= to,
               JournalLine.account_id == acc.id)
    ).all()
    return quantize(sum((_D(d) - _D(c) for d, c in rows), Decimal("0")), MONEY_SCALE)


def _machine_basis(db: Session, company_id: uuid.UUID, machine: Machine,
                   basis: AllocationBasis, frm: date, to: date) -> Decimal:
    if basis == AllocationBasis.EQUAL:
        return Decimal("1")
    if basis == AllocationBasis.OUTPUT_KG:
        if machine.machine_type == "KNITTING":
            rows = db.execute(select(KnittingProduction.produced_kg).where(
                KnittingProduction.company_id == company_id, KnittingProduction.machine_id == machine.id,
                KnittingProduction.production_date >= frm, KnittingProduction.production_date <= to)).scalars().all()
            return quantize(sum((_D(x) for x in rows), Decimal("0")), QTY_SCALE)
        baths = db.execute(select(DyeBath).where(
            DyeBath.company_id == company_id, DyeBath.machine_id == machine.id)).scalars().all()
        total = Decimal("0")
        for b in baths:
            d = b.actual_start.date() if isinstance(b.actual_start, datetime) else None
            if d is None or (frm <= d <= to):
                total += _D(b.fabric_kg)
        return quantize(total, QTY_SCALE)
    # MACHINE_HOURS
    if machine.machine_type == "KNITTING":
        rows = db.execute(select(KnittingProduction.run_time_min).where(
            KnittingProduction.company_id == company_id, KnittingProduction.machine_id == machine.id,
            KnittingProduction.production_date >= frm, KnittingProduction.production_date <= to)).scalars().all()
        minutes = sum((int(x or 0) for x in rows), 0)
        return quantize(Decimal(minutes) / Decimal("60"), QTY_SCALE)
    baths = db.execute(select(DyeBath).where(
        DyeBath.company_id == company_id, DyeBath.machine_id == machine.id)).scalars().all()
    minutes = Decimal("0")
    for b in baths:
        if isinstance(b.actual_start, datetime) and isinstance(b.actual_end, datetime):
            minutes += Decimal((b.actual_end - b.actual_start).total_seconds()) / Decimal("60")
    return quantize(minutes / Decimal("60"), QTY_SCALE)


def allocate_overhead(db: Session, *, company_id: uuid.UUID, pool: OverheadPool, period_month: date,
                      amount=None, run_date: date | None = None,
                      created_by: uuid.UUID | None = None) -> dict:
    frm, to = _bounds(period_month)
    run_date = run_date or to
    ref = f"OHALLOC:{company_id}:{pool.code}:{frm:%Y-%m}"
    existing = pe.find_existing(db, company_id, ref)
    if existing is not None:
        return {"already_posted": True, "voucher": existing, "period": frm.isoformat()}

    total = quantize(_D(amount), MONEY_SCALE) if amount is not None else \
        _source_balance(db, company_id, pool.source_account_code, frm, to)
    if total <= 0:
        raise OverheadError("No overhead amount to allocate (source balance is zero)")

    machines = db.execute(select(Machine).where(
        Machine.company_id == company_id, Machine.is_active.is_(True)).order_by(Machine.code)).scalars().all()
    if not machines:
        raise OverheadError("No active machines to allocate to")

    weights = []
    total_weight = QZERO
    for m in machines:
        w = _machine_basis(db, company_id, m, pool.basis, frm, to)
        weights.append((m, w))
        total_weight = quantize(total_weight + w, QTY_SCALE)
    if total_weight <= 0:
        raise OverheadError(f"No activity to allocate against for basis {pool.basis.value}")

    run = OverheadAllocationRun(company_id=company_id, pool_id=pool.id, period_month=frm,
                                run_date=run_date, basis=pool.basis, total_amount=total, created_by=created_by)
    db.add(run)
    db.flush()

    wip = resolve_account(db, company_id, "OVERHEAD_ALLOCATION", "wip")
    lines = []
    allocated = MZERO
    positive = [(m, w) for (m, w) in weights if w > 0]
    for idx, (m, w) in enumerate(positive):
        if idx == len(positive) - 1:
            share = quantize(total - allocated, MONEY_SCALE)        # last absorbs the remainder
        else:
            share = quantize(total * w / total_weight, MONEY_SCALE)
        allocated = quantize(allocated + share, MONEY_SCALE)
        cc_id = work_centres.machine_cost_centre_id(db, company_id, m.id)
        run.lines.append(OverheadAllocationLine(machine_id=m.id, cost_centre_id=cc_id,
                                                basis_qty=w, amount=share))
        if share > 0:
            lines.append(pe.PostingLine(wip, debit=share, cost_centre_id=cc_id,
                                        narration=f"Overhead {pool.code} -> {m.code}"))
    db.flush()

    lines.append(pe.PostingLine(pool.source_account_code, credit=total,
                                narration=f"Absorb {pool.name}"))
    voucher = pe.post_voucher(db, pe.PostingRequest(
        company_id=company_id, voucher_type_code="PJ", voucher_date=run_date, posting_reference=ref,
        narration=f"Overhead allocation {pool.code} {frm:%Y-%m}", source_module="overhead",
        source_id=str(run.id), created_by=created_by, lines=lines,
    ))
    run.posting_reference = ref
    db.flush()
    return {"run_id": str(run.id), "period": frm.isoformat(), "total": total,
            "machines_allocated": len(positive), "voucher": voucher,
            "lines": [{"machine_id": str(li.machine_id), "basis_qty": li.basis_qty, "amount": li.amount}
                      for li in run.lines]}
