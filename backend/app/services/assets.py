"""Fixed-asset capitalisation, depreciation runs and disposal.

Depreciation methods:
  * SLM  — straight-line: (cost - salvage) / useful_life_months each month.
  * WDV  — written-down value: book_value * annual_rate% / 12 each month.

Every charge is floored so an asset never depreciates below its salvage value.
A depreciation run posts one balanced DEP voucher for the whole batch and is
idempotent on the period, so a month can't be charged twice.
"""
from __future__ import annotations

import calendar
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.assets import (
    AssetStatus,
    DepMethod,
    DepreciationEntry,
    DepreciationRun,
    FixedAsset,
)
from app.services.accounting import posting_engine as pe
from app.services.accounting.posting_rules import resolve_account

MZERO = quantize(0, MONEY_SCALE)


class AssetError(Exception):
    """An asset operation was invalid (bad method, already disposed, ...)."""


def _first_of_month(d: date) -> date:
    return d.replace(day=1)


def _end_of_month(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def _D(v) -> Decimal:
    return Decimal(str(v or 0))


# ------------------------------ capitalisation ------------------------------

def capitalize_asset(
    db: Session, *, company_id: uuid.UUID, code: str, name: str,
    asset_account_code: str, acquisition_date: date, cost,
    category: str | None = None, salvage=0, method: str = "SLM",
    useful_life_months: int | None = None, dep_rate_pct=None,
    credit_account_code: str | None = None, created_by: uuid.UUID | None = None,
) -> FixedAsset:
    m = DepMethod(method)
    if m == DepMethod.SLM and not useful_life_months:
        raise AssetError("SLM assets need a useful life in months")
    if m == DepMethod.WDV and not dep_rate_pct:
        raise AssetError("WDV assets need an annual depreciation rate")

    asset = FixedAsset(
        company_id=company_id, code=code, name=name, category=category,
        asset_account_code=asset_account_code, acquisition_date=acquisition_date,
        capitalized_cost=_D(cost), salvage_value=_D(salvage), method=m,
        useful_life_months=useful_life_months, dep_rate_pct=dep_rate_pct,
        accumulated_depreciation=MZERO, status=AssetStatus.ACTIVE, created_by=created_by,
    )
    db.add(asset)
    db.flush()

    ref = f"FA:{code}"
    if pe.find_existing(db, company_id, ref) is None:
        credit = credit_account_code or resolve_account(db, company_id, "ASSET_CAPITALISATION", "credit")
        cost_q = quantize(_D(cost), MONEY_SCALE)
        voucher = pe.post_voucher(db, pe.PostingRequest(
            company_id=company_id, voucher_type_code="JV", voucher_date=acquisition_date,
            posting_reference=ref, narration=f"Capitalise asset {code} — {name}",
            source_module="assets", source_id=str(asset.id), created_by=created_by,
            lines=[
                pe.PostingLine(asset_account_code, debit=cost_q, narration="Asset cost"),
                pe.PostingLine(credit, credit=cost_q, narration="Asset acquisition"),
            ],
        ))
        asset.posting_reference = ref
        db.flush()
        return asset
    asset.posting_reference = ref
    db.flush()
    return asset


# ------------------------------ depreciation --------------------------------

def monthly_depreciation(asset: FixedAsset) -> Decimal:
    """One month's charge for an asset, floored at its salvage value."""
    cost = _D(asset.capitalized_cost)
    salvage = _D(asset.salvage_value)
    accumulated = _D(asset.accumulated_depreciation)
    remaining = cost - salvage - accumulated
    if remaining <= 0 or asset.status != AssetStatus.ACTIVE:
        return MZERO
    if asset.method == DepMethod.SLM:
        base = (cost - salvage) / Decimal(asset.useful_life_months or 1)
    else:  # WDV
        book = cost - accumulated
        base = book * _D(asset.dep_rate_pct) / Decimal("1200")
    charge = quantize(base, MONEY_SCALE)
    if charge > remaining:
        charge = quantize(remaining, MONEY_SCALE)
    return charge


def run_depreciation(
    db: Session, *, company_id: uuid.UUID, period_month: date,
    run_date: date | None = None, created_by: uuid.UUID | None = None,
) -> dict:
    """Charge one month's depreciation for every active asset, idempotently."""
    period = _first_of_month(period_month)
    run_date = run_date or _end_of_month(period)
    ref = f"DEP:{company_id}:{period:%Y-%m}"
    existing = pe.find_existing(db, company_id, ref)
    if existing is not None:
        return {"already_posted": True, "voucher": existing, "period": period.isoformat()}

    cutoff = _end_of_month(period)
    assets = db.execute(
        select(FixedAsset).where(
            FixedAsset.company_id == company_id,
            FixedAsset.status == AssetStatus.ACTIVE,
            FixedAsset.acquisition_date <= cutoff,
        ).order_by(FixedAsset.code)
    ).scalars().all()

    run = DepreciationRun(company_id=company_id, period_month=period, run_date=run_date,
                          total_amount=MZERO, created_by=created_by)
    db.add(run)
    db.flush()

    by_expense: dict[str, Decimal] = {}
    total = MZERO
    for asset in assets:
        charge = monthly_depreciation(asset)
        if charge <= 0:
            continue
        asset.accumulated_depreciation = quantize(_D(asset.accumulated_depreciation) + charge, MONEY_SCALE)
        book_after = quantize(_D(asset.capitalized_cost) - _D(asset.accumulated_depreciation), MONEY_SCALE)
        run.entries.append(DepreciationEntry(asset_id=asset.id, amount=charge, book_value_after=book_after))
        exp = resolve_account(db, company_id, "DEPRECIATION", "expense")
        by_expense[exp] = quantize(by_expense.get(exp, MZERO) + charge, MONEY_SCALE)
        total = quantize(total + charge, MONEY_SCALE)
    db.flush()

    voucher = None
    if total > 0:
        accum = resolve_account(db, company_id, "DEPRECIATION", "accumulated")
        lines = [pe.PostingLine(acct, debit=amt, narration="Depreciation") for acct, amt in by_expense.items()]
        lines.append(pe.PostingLine(accum, credit=total, narration="Accumulated depreciation"))
        voucher = pe.post_voucher(db, pe.PostingRequest(
            company_id=company_id, voucher_type_code="DEP", voucher_date=run_date,
            posting_reference=ref, narration=f"Depreciation {period:%Y-%m}",
            source_module="assets", source_id=str(run.id), created_by=created_by, lines=lines,
        ))
        run.posting_reference = ref
    run.total_amount = total
    db.flush()
    return {"run_id": str(run.id), "period": period.isoformat(), "total": total,
            "assets_charged": len(run.entries), "voucher": voucher}


# -------------------------------- disposal ----------------------------------

def dispose_asset(
    db: Session, *, company_id: uuid.UUID, asset: FixedAsset, disposal_date: date,
    proceeds=0, proceeds_account_code: str | None = None, created_by: uuid.UUID | None = None,
) -> dict:
    """Retire an asset: clear cost + accumulated depreciation, book gain/loss."""
    if asset.status == AssetStatus.DISPOSED:
        raise AssetError(f"Asset {asset.code} is already disposed")
    ref = f"FADISP:{asset.code}"
    if pe.find_existing(db, company_id, ref) is not None:
        raise AssetError(f"Disposal of {asset.code} is already posted")

    cost = quantize(_D(asset.capitalized_cost), MONEY_SCALE)
    accumulated = quantize(_D(asset.accumulated_depreciation), MONEY_SCALE)
    book_value = quantize(cost - accumulated, MONEY_SCALE)
    proceeds = quantize(_D(proceeds), MONEY_SCALE)
    gain = quantize(proceeds - book_value, MONEY_SCALE)

    lines = []
    if accumulated > 0:
        lines.append(pe.PostingLine(resolve_account(db, company_id, "ASSET_DISPOSAL", "accumulated"),
                                    debit=accumulated, narration="Reverse accumulated depreciation"))
    if proceeds > 0:
        acct = proceeds_account_code or resolve_account(db, company_id, "ASSET_DISPOSAL", "proceeds")
        lines.append(pe.PostingLine(acct, debit=proceeds, narration="Disposal proceeds"))
    if gain < 0:
        lines.append(pe.PostingLine(resolve_account(db, company_id, "ASSET_DISPOSAL", "loss"),
                                    debit=quantize(-gain, MONEY_SCALE), narration="Loss on disposal"))
    lines.append(pe.PostingLine(asset.asset_account_code, credit=cost, narration="Remove asset cost"))
    if gain > 0:
        lines.append(pe.PostingLine(resolve_account(db, company_id, "ASSET_DISPOSAL", "gain"),
                                    credit=gain, narration="Gain on disposal"))

    voucher = pe.post_voucher(db, pe.PostingRequest(
        company_id=company_id, voucher_type_code="JV", voucher_date=disposal_date,
        posting_reference=ref, narration=f"Dispose asset {asset.code}",
        source_module="assets", source_id=str(asset.id), created_by=created_by, lines=lines,
    ))
    asset.status = AssetStatus.DISPOSED
    asset.disposal_date = disposal_date
    db.flush()
    return {"asset_id": str(asset.id), "book_value": book_value, "proceeds": proceeds,
            "gain_loss": gain, "voucher": voucher}
