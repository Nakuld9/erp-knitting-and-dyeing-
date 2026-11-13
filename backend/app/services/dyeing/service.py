"""Dyeing jobs: grey fabric in → dyed fabric out, with GENUINE actual costs.

Chemical-free by design: no chemical recipe is exploded and no chemical is
reserved. Chemical cost is accepted only when entered manually, and its absence
is surfaced as *incomplete* rather than filled in.

Cost model (all amounts genuine; none derived from a standard):

    total_actual = grey_cost_consumed (FIFO draw from grey FABRIC inventory)
                 + dyeing_charge | jobwork_charge        (by mode)
                 + chemical_cost  (when recorded)
                 + finishing_cost + testing_cost + other_cost + reprocess_cost
    dyed_cost_per_kg = total_actual / saleable_output_kg

The total is allocated across output lots by weight. ``dyed_actual_cost`` lists
every cost component that is **not recorded** and marks the result incomplete.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, QTY_SCALE, quantize
from app.models.dyeing_cost import (
    DyeingInput,
    DyeingJob,
    DyeingMode,
    DyeingOutput,
    DyeingStatus,
)
from app.models.fabric_bom import ProductionConsumption, ProductionOrder
from app.models.inventory import ItemType
from app.services.inventory import service as inventory

MZERO = Decimal("0")
QZERO = Decimal("0")

# Optional cost components (manual, when available).
_COST_FIELDS = ("dyeing_charge", "jobwork_charge", "chemical_cost",
                "finishing_cost", "testing_cost", "other_cost", "reprocess_cost")


class DyeingError(Exception):
    pass


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else MZERO


def _uid(v):
    return v if isinstance(v, uuid.UUID) or v is None else uuid.UUID(str(v))


def create_job(
    db: Session,
    *,
    company_id: uuid.UUID,
    number: str,
    mode: str = DyeingMode.INTERNAL.value,
    grey_production_order_id: uuid.UUID | None = None,
    sales_order_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    shade_id: uuid.UUID | None = None,
    dye_lot: str | None = None,
    jobwork_supplier_id: uuid.UUID | None = None,
    factory_id: uuid.UUID | None = None,
    dye_date: date | None = None,
    created_by: uuid.UUID | None = None,
) -> DyeingJob:
    mode = mode.value if isinstance(mode, DyeingMode) else str(mode)
    if mode not in (DyeingMode.INTERNAL.value, DyeingMode.JOBWORK.value):
        raise DyeingError("mode must be INTERNAL or JOBWORK")
    if mode == DyeingMode.JOBWORK.value and jobwork_supplier_id is None:
        raise DyeingError("JOBWORK dyeing requires a jobwork_supplier_id")
    job = DyeingJob(
        company_id=_uid(company_id), number=number, mode=mode,
        grey_production_order_id=_uid(grey_production_order_id),
        sales_order_id=_uid(sales_order_id), customer_id=_uid(customer_id),
        shade_id=_uid(shade_id), dye_lot=dye_lot, jobwork_supplier_id=_uid(jobwork_supplier_id),
        factory_id=_uid(factory_id), dye_date=dye_date, status=DyeingStatus.PLANNED,
        created_by=created_by,
    )
    db.add(job)
    db.flush()
    return job


def _get_open_job(db: Session, job_id: uuid.UUID) -> DyeingJob:
    job = db.get(DyeingJob, _uid(job_id))
    if job is None:
        raise DyeingError("dyeing job not found")
    if job.status in (DyeingStatus.COMPLETED, DyeingStatus.CANCELLED):
        raise DyeingError(f"job is {job.status.value}; no further changes allowed")
    return job


def issue_grey(
    db: Session,
    *,
    job_id: uuid.UUID,
    source_production_order_id: uuid.UUID,
    qty_kg,
    warehouse_id: uuid.UUID | None = None,
    method: str = "FIFO",
) -> DyeingInput:
    """Consume grey fabric from FABRIC inventory (keyed by its grey production
    order) into the dyeing job at genuine FIFO cost."""
    job = _get_open_job(db, job_id)
    src = _uid(source_production_order_id)
    qty = _d(qty_kg)
    if qty <= QZERO:
        raise DyeingError("grey issue qty must be positive")
    order = db.get(ProductionOrder, src)
    if order is None or order.company_id != job.company_id:
        raise DyeingError("source grey production order not found in this company")
    if order.grey_valued_at is None:
        raise DyeingError("source grey fabric has not been valued into inventory yet")

    pref = f"DYE:{job.number}:grey"
    res = inventory.consume(
        db, company_id=job.company_id, item_type=ItemType.FABRIC, item_id=src, qty=qty,
        warehouse_id=_uid(warehouse_id), method=method, event_type="DYE_GREY_ISSUE",
        source_module="DYEING_JOB", source_id=str(job.id), posting_reference=pref,
    )
    inp = DyeingInput(
        company_id=job.company_id, job_id=job.id, item_type=ItemType.FABRIC.value,
        item_id=src, source_production_order_id=src, qty_kg=quantize(qty, QTY_SCALE),
        cost=res["total_cost"], posting_reference=pref,
    )
    db.add(inp)
    job.input_qty_kg = quantize(_d(job.input_qty_kg) + qty, QTY_SCALE)
    job.grey_cost_consumed = quantize(_d(job.grey_cost_consumed) + _d(res["total_cost"]), MONEY_SCALE)
    if job.status == DyeingStatus.PLANNED:
        job.status = DyeingStatus.IN_PROGRESS
    db.flush()
    return inp


def record_costs(db: Session, *, job_id: uuid.UUID, **costs) -> DyeingJob:
    """Record actual dyeing cost components (only those provided). Non-negative.
    These are genuine recorded amounts — never defaulted from a standard."""
    job = _get_open_job(db, job_id)
    for f, val in costs.items():
        if f not in _COST_FIELDS:
            raise DyeingError(f"unknown cost component '{f}'")
        if val is None:
            continue
        amt = _d(val)
        if amt < QZERO:
            raise DyeingError(f"{f} cannot be negative")
        setattr(job, f, quantize(amt, MONEY_SCALE))
    db.flush()
    return job


def record_output(
    db: Session,
    *,
    job_id: uuid.UUID,
    lots: list[dict],
) -> list[DyeingOutput]:
    """Record saleable dyed output lots. ``lots``: [{lot_code, qty_kg, shade_id?,
    warehouse_id?}]. Quantities are accumulated; valuation happens at complete."""
    job = _get_open_job(db, job_id)
    out = []
    for lot in lots:
        qty = _d(lot.get("qty_kg"))
        if qty <= QZERO:
            raise DyeingError("each output lot needs a positive qty_kg")
        code = (lot.get("lot_code") or "").strip()
        if not code:
            raise DyeingError("each output lot needs a lot_code")
        row = DyeingOutput(
            company_id=job.company_id, job_id=job.id, lot_code=code, qty_kg=quantize(qty, QTY_SCALE),
            shade_id=_uid(lot.get("shade_id")) or job.shade_id, warehouse_id=_uid(lot.get("warehouse_id")),
        )
        db.add(row)
        out.append(row)
    db.flush()
    db.expire(job, ["outputs"])
    job.output_qty_kg = quantize(sum((_d(o.qty_kg) for o in job.outputs), QZERO), QTY_SCALE)
    db.flush()
    return out


def _accumulate(job: DyeingJob) -> tuple[Decimal, list[str]]:
    """Return (total_actual_cost, missing_components) for a job."""
    total = _d(job.grey_cost_consumed)
    missing = []
    if _d(job.grey_cost_consumed) <= QZERO:
        missing.append("grey_cost_consumed")
    # mode-specific processing charge is required for completeness
    if job.mode == DyeingMode.JOBWORK:
        if job.jobwork_charge is None:
            missing.append("jobwork_charge")
    else:
        if job.dyeing_charge is None:
            missing.append("dyeing_charge")
    # chemical cost: required for a *true* dyed cost, optional to enter (chemical
    # BOM is disabled) → its absence makes the result incomplete.
    if job.chemical_cost is None:
        missing.append("chemical_cost")
    for f in _COST_FIELDS:
        val = getattr(job, f)
        if val is not None:
            total += _d(val)
    return quantize(total, MONEY_SCALE), missing


def complete_job(
    db: Session,
    *,
    job_id: uuid.UUID,
    rejection_kg=0,
    reprocess_qty_kg=0,
    process_loss_kg=None,
    warehouse_id: uuid.UUID | None = None,
) -> DyeingJob:
    """Finalize a dyeing job: allocate accumulated cost across the saleable output
    lots by weight, value the dyed fabric into inventory, and mark COMPLETED.

    Completion needs grey input and at least one output lot. The actual cost is
    whatever has been genuinely recorded — completion does NOT require every
    component; ``dyed_actual_cost`` reports what is missing."""
    job = _get_open_job(db, job_id)
    if _d(job.input_qty_kg) <= QZERO:
        raise DyeingError("cannot complete: no grey fabric has been issued")
    db.expire(job, ["outputs"])
    outputs = list(job.outputs)
    saleable = quantize(sum((_d(o.qty_kg) for o in outputs), QZERO), QTY_SCALE)
    if saleable <= QZERO:
        raise DyeingError("cannot complete: no saleable output recorded")

    job.rejection_kg = quantize(_d(rejection_kg), QTY_SCALE)
    job.reprocess_qty_kg = quantize(_d(reprocess_qty_kg), QTY_SCALE)
    job.output_qty_kg = saleable
    # process loss defaults to input - output - rejection (can be negative = gain)
    if process_loss_kg is None:
        job.process_loss_kg = quantize(_d(job.input_qty_kg) - saleable - job.rejection_kg, QTY_SCALE)
    else:
        job.process_loss_kg = quantize(_d(process_loss_kg), QTY_SCALE)

    total, _missing = _accumulate(job)
    # Allocate total across lots by weight and value dyed fabric into inventory.
    # ``allocated_cost`` is the accounting allocation (sums exactly to total);
    # the inventory layer value is qty×unit_cost, which can differ from the
    # allocation by per-kg rounding dust — ``dyed_inventory_value`` records the
    # actual perpetual-inventory value received so the sub-ledger stays exact.
    allocated_sum = MZERO
    inventory_value = MZERO
    for i, o in enumerate(outputs):
        if i == len(outputs) - 1:
            alloc = quantize(total - allocated_sum, MONEY_SCALE)   # last lot absorbs allocation rounding
        else:
            alloc = quantize(total * (_d(o.qty_kg) / saleable), MONEY_SCALE)
        allocated_sum += alloc
        o.allocated_cost = alloc
        o.is_allocated = True
        o.dyed_item_id = o.dyed_item_id or uuid.uuid4()
        unit = quantize(alloc / _d(o.qty_kg), MONEY_SCALE) if _d(o.qty_kg) > QZERO else MZERO
        layer = inventory.receive(
            db, company_id=job.company_id, item_type=ItemType.FABRIC, item_id=o.dyed_item_id,
            qty=_d(o.qty_kg), unit_cost=unit, warehouse_id=_uid(warehouse_id) or o.warehouse_id,
            event_type="DYED_PRODUCE", source_module="DYEING_JOB", source_id=str(job.id),
            posting_reference=f"DYE:{job.number}:dyed:{o.lot_code}",
        )
        inventory_value += _d(layer.value_remaining)
    job.dyed_inventory_value = quantize(inventory_value, MONEY_SCALE)
    job.status = DyeingStatus.COMPLETED
    db.flush()
    return job


def cancel_job(db: Session, *, job_id: uuid.UUID) -> DyeingJob:
    job = _get_open_job(db, job_id)
    job.status = DyeingStatus.CANCELLED
    db.flush()
    return job


def dyed_actual_cost(db: Session, job_id: uuid.UUID) -> dict:
    """Genuine actual dyed-fabric cost for a job, with explicit incompleteness.

    ``cost_complete`` is False when any required component (grey, the mode's
    processing charge, chemical) is not recorded — the per-kg figure is then a
    **lower bound**, labelled incomplete, never an artificially precise actual.
    """
    job = db.get(DyeingJob, _uid(job_id))
    if job is None:
        raise DyeingError("dyeing job not found")
    db.expire(job, ["outputs", "inputs"])
    total, missing = _accumulate(job)
    saleable = quantize(sum((_d(o.qty_kg) for o in job.outputs), QZERO), QTY_SCALE)
    per_kg = quantize(total / saleable, MONEY_SCALE) if saleable > QZERO else None

    input_kg = _d(job.input_qty_kg)
    loss = quantize(input_kg - saleable - _d(job.rejection_kg), QTY_SCALE)
    loss_pct = quantize(loss / input_kg * Decimal("100"), 2) if input_kg > QZERO else None

    components = {
        "grey_cost_consumed": _d(job.grey_cost_consumed),
        "dyeing_charge": job.dyeing_charge, "jobwork_charge": job.jobwork_charge,
        "chemical_cost": job.chemical_cost, "finishing_cost": job.finishing_cost,
        "testing_cost": job.testing_cost, "other_cost": job.other_cost,
        "reprocess_cost": job.reprocess_cost,
    }
    lots = [{
        "lot_code": o.lot_code, "qty_kg": quantize(_d(o.qty_kg), QTY_SCALE),
        "allocated_cost": quantize(_d(o.allocated_cost), MONEY_SCALE),
        "cost_per_kg": quantize(_d(o.allocated_cost) / _d(o.qty_kg), MONEY_SCALE) if _d(o.qty_kg) > QZERO else None,
        "dyed_item_id": str(o.dyed_item_id) if o.dyed_item_id else None,
    } for o in job.outputs]

    return {
        "job_id": str(job.id), "number": job.number, "mode": job.mode.value if hasattr(job.mode, "value") else job.mode,
        "input_qty_kg": quantize(input_kg, QTY_SCALE),
        "saleable_output_kg": saleable,
        "rejection_kg": quantize(_d(job.rejection_kg), QTY_SCALE),
        "reprocess_qty_kg": quantize(_d(job.reprocess_qty_kg), QTY_SCALE),
        "process_loss_kg": loss, "loss_pct": loss_pct,
        "cost_components": {k: (quantize(_d(v), MONEY_SCALE) if v is not None else None)
                            for k, v in components.items()},
        "total_actual_cost": total,
        "actual_dyed_cost_per_kg": per_kg,
        "missing_cost_components": missing,
        "cost_complete": not missing,
        "note": (None if not missing else
                 "Actual dyed cost is INCOMPLETE — the components above are not "
                 "recorded (chemical BOM is disabled, so chemical cost is entered "
                 "manually only). The per-kg figure is a lower bound, not a precise actual."),
        "output_lots": lots,
    }


def trace(db: Session, job_id: uuid.UUID) -> dict:
    """Drill-down: dyed job → grey production order(s) → yarn lots → yarn issues."""
    job = db.get(DyeingJob, _uid(job_id))
    if job is None:
        raise DyeingError("dyeing job not found")
    db.expire(job, ["inputs", "outputs"])
    greys = []
    seen = set()
    for inp in job.inputs:
        po_id = inp.source_production_order_id
        if po_id is None or po_id in seen:
            continue
        seen.add(po_id)
        order = db.get(ProductionOrder, po_id)
        # yarn lots + issues for this grey order (from its yarn consumptions)
        yarn_issues = []
        for c in db.execute(
            select(ProductionConsumption).where(
                ProductionConsumption.production_order_id == po_id,
                ProductionConsumption.item_type == ItemType.YARN.value,
            )
        ).scalars().all():
            yarn_issues.append({
                "yarn_item_id": str(c.item_id), "lot_id": str(c.lot_id) if c.lot_id else None,
                "qty_kg": quantize(_d(c.qty), QTY_SCALE), "cost": quantize(_d(c.actual_cost), MONEY_SCALE),
                "posting_reference": c.posting_reference,
            })
        greys.append({
            "production_order_id": str(po_id),
            "number": order.number if order else None,
            "grey_issued_kg": quantize(_d(inp.qty_kg), QTY_SCALE),
            "grey_issue_cost": quantize(_d(inp.cost), MONEY_SCALE),
            "yarn_issues": yarn_issues,
        })
    return {
        "job_id": str(job.id), "number": job.number,
        "shade_id": str(job.shade_id) if job.shade_id else None,
        "dye_lot": job.dye_lot,
        "grey_sources": greys,
        "output_lots": [{"lot_code": o.lot_code, "qty_kg": quantize(_d(o.qty_kg), QTY_SCALE),
                         "dyed_item_id": str(o.dyed_item_id) if o.dyed_item_id else None} for o in job.outputs],
    }
