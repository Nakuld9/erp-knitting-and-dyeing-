"""Inventory-to-GL reconciliation for the BOM-driven grey→dyed workflow.

Every grey production order and dyeing job posts balanced production journals
through the posting engine. This module proves, directly from the immutable
ledger (never from a cache, and without any balancing adjustment), that:

* yarn inventory reduction == yarn consumption posted to WIP;
* grey fabric inventory receipt == grey production cost (greige debit);
* grey fabric issue == dyeing WIP (greige credit into WIP);
* dyeing WIP debits == accumulated job cost, and credits move it all out;
* dyed fabric inventory receipt == accumulated actual dyed cost (FG debit);
* a COMPLETED order/job leaves **no** WIP balance of its own;
* the FABRIC sub-ledger (grey + dyed layers) ties to the greige + finished GL
  control accounts contributed by this workflow.

Discrepancies are reported, never hidden.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.accounting import ChartOfAccount, JournalLine, Voucher, VoucherStatus
from app.models.dyeing_cost import DyeingJob, DyeingOutput, DyeingStatus
from app.models.fabric_bom import ProductionOrder, ProductionOrderStatus
from app.models.inventory import ItemType, StockLayer

ZERO = quantize(0, MONEY_SCALE)
_POSTED = [s.value for s in (VoucherStatus.POSTED, VoucherStatus.REVERSED, VoucherStatus.REVERSAL)]

# account codes used by the workflow (existing chart of accounts)
WIP = "1080"; YARN = "1060"; GREIGE = "1070"; FG = "1085"; EXP_PAYABLE = "2020"; AP = "2010"


def _d(v):
    return quantize(v or 0, MONEY_SCALE)


def _source_balances(db: Session, company_id: uuid.UUID, source_module: str, source_id: str) -> dict:
    """Per-account (debit_sum, credit_sum) across all POSTED vouchers for a source
    document (grey order or dyeing job)."""
    rows = db.execute(
        select(ChartOfAccount.code, JournalLine.debit, JournalLine.credit)
        .join(Voucher, JournalLine.voucher_id == Voucher.id)
        .join(ChartOfAccount, JournalLine.account_id == ChartOfAccount.id)
        .where(Voucher.company_id == company_id, Voucher.status.in_(_POSTED),
               Voucher.source_module == source_module, Voucher.source_id == str(source_id))
    ).all()
    out: dict[str, list] = {}
    for code, debit, credit in rows:
        d, c = out.setdefault(code, [ZERO, ZERO])
        out[code] = [d + _d(debit), c + _d(credit)]
    return out


def _check(rows, label, lhs, rhs):
    ok = quantize(lhs, MONEY_SCALE) == quantize(rhs, MONEY_SCALE)
    rows.append({"check": label, "lhs": quantize(lhs, MONEY_SCALE),
                 "rhs": quantize(rhs, MONEY_SCALE),
                 "difference": quantize(lhs - rhs, MONEY_SCALE), "matched": ok})
    return ok


def grey_order_reconciliation(db: Session, company_id: uuid.UUID, order_id: uuid.UUID) -> dict:
    order = db.get(ProductionOrder, order_id)
    if order is None or order.company_id != company_id:
        raise ValueError("order not found in this company")
    bal = _source_balances(db, company_id, "PRODUCTION_ORDER", str(order_id))
    wip_d, wip_c = bal.get(WIP, [ZERO, ZERO])
    yarn_d, yarn_c = bal.get(YARN, [ZERO, ZERO])
    greige_d, greige_c = bal.get(GREIGE, [ZERO, ZERO])
    from app.services.bom.production import _yarn_actual_cost
    yarn_cost = _yarn_actual_cost(db, order_id)
    rows, ok = [], True
    if order.grey_valued_at is not None:
        ok &= _check(rows, "yarn consumption (GL credit) == actual yarn FIFO cost", yarn_c, yarn_cost)
        ok &= _check(rows, "grey receipt (greige GL debit) == grey production cost",
                     greige_d, _d(order.grey_inventory_value))
        ok &= _check(rows, "grey order WIP nets to zero", wip_d - wip_c, ZERO)
    return {"order_id": str(order.id), "number": order.number,
            "grey_valued": order.grey_valued_at is not None,
            "checks": rows, "is_reconciled": ok}


def dyeing_job_reconciliation(db: Session, company_id: uuid.UUID, job_id: uuid.UUID) -> dict:
    job = db.get(DyeingJob, job_id)
    if job is None or job.company_id != company_id:
        raise ValueError("dyeing job not found in this company")
    bal = _source_balances(db, company_id, "DYEING_JOB", str(job_id))
    wip_d, wip_c = bal.get(WIP, [ZERO, ZERO])
    greige_d, greige_c = bal.get(GREIGE, [ZERO, ZERO])
    fg_d, fg_c = bal.get(FG, [ZERO, ZERO])

    # accumulated recorded cost (grey consumed + recorded cost components)
    from app.services.dyeing.service import _accumulate
    total, _missing = _accumulate(job)
    grey = _d(job.grey_cost_consumed)
    completed = job.status == DyeingStatus.COMPLETED

    rows, ok = [], True
    ok &= _check(rows, "grey issue (greige GL credit) == grey cost consumed", greige_c, grey)
    if completed:
        ok &= _check(rows, "dyeing WIP debits == accumulated job cost (grey + charges)", wip_d, total)
        ok &= _check(rows, "dyed receipt (finished GL debit) == accumulated dyed cost", fg_d, total)
        ok &= _check(rows, "dyed receipt (finished GL debit) == dyed inventory value",
                     fg_d, _d(job.dyed_inventory_value))
        ok &= _check(rows, "completed job leaves no WIP balance", wip_d - wip_c, ZERO)
    else:
        # in-progress: WIP holds exactly the grey issued so far (charges post at completion)
        ok &= _check(rows, "in-progress dyeing WIP == grey issued into WIP", wip_d - wip_c, grey)
    return {"job_id": str(job.id), "number": job.number, "status":
            job.status.value if hasattr(job.status, "value") else job.status,
            "checks": rows, "is_reconciled": ok}


def _fabric_subledger_split(db: Session, company_id: uuid.UUID):
    """Partition FABRIC stock-layer value into grey (keyed by a grey production
    order) vs dyed (keyed by a dyeing output) for this workflow."""
    grey_ids = {o.id for o in db.execute(
        select(ProductionOrder).where(ProductionOrder.company_id == company_id)).scalars()}
    dyed_ids = {o.dyed_item_id for o in db.execute(
        select(DyeingOutput).where(DyeingOutput.company_id == company_id)).scalars()
        if o.dyed_item_id is not None}
    grey_val = dyed_val = ZERO
    for layer in db.execute(select(StockLayer).where(
            StockLayer.company_id == company_id, StockLayer.item_type == ItemType.FABRIC.value)).scalars():
        if layer.item_id in dyed_ids:
            dyed_val += _d(layer.value_remaining)
        elif layer.item_id in grey_ids:
            grey_val += _d(layer.value_remaining)
    return grey_val, dyed_val


def production_ledger_reconciliation(db: Session, company_id: uuid.UUID) -> dict:
    """Reconcile every grey order + dyeing job, plus a workflow-level check that
    no COMPLETED order/job leaves WIP and the FABRIC sub-ledger ties to the GL
    contribution of this workflow (greige debits − credits, finished debits)."""
    order_rows, job_rows = [], []
    all_ok = True
    for o in db.execute(select(ProductionOrder).where(
            ProductionOrder.company_id == company_id)).scalars():
        if o.grey_valued_at is None:
            continue
        r = grey_order_reconciliation(db, company_id, o.id)
        all_ok &= r["is_reconciled"]
        order_rows.append(r)
    for j in db.execute(select(DyeingJob).where(DyeingJob.company_id == company_id)).scalars():
        r = dyeing_job_reconciliation(db, company_id, j.id)
        all_ok &= r["is_reconciled"]
        job_rows.append(r)

    # workflow-level FABRIC sub-ledger vs GL contribution
    grey_sub, dyed_sub = _fabric_subledger_split(db, company_id)
    greige_gl = ZERO; fg_gl = ZERO
    for src in ("PRODUCTION_ORDER", "DYEING_JOB"):
        rows = db.execute(
            select(ChartOfAccount.code, JournalLine.debit, JournalLine.credit)
            .join(Voucher, JournalLine.voucher_id == Voucher.id)
            .join(ChartOfAccount, JournalLine.account_id == ChartOfAccount.id)
            .where(Voucher.company_id == company_id, Voucher.status.in_(_POSTED),
                   Voucher.source_module == src)).all()
        for code, d, c in rows:
            if code == GREIGE:
                greige_gl += _d(d) - _d(c)
            elif code == FG:
                fg_gl += _d(d) - _d(c)
    wf, ok = [], True
    ok &= _check(wf, "grey FABRIC sub-ledger == net greige GL from workflow", grey_sub, greige_gl)
    ok &= _check(wf, "dyed FABRIC sub-ledger == net finished GL from workflow", dyed_sub, fg_gl)

    return {
        "grey_orders": order_rows, "dyeing_jobs": job_rows, "workflow_checks": wf,
        "is_reconciled": bool(all_ok and ok),
    }
