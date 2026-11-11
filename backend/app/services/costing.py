"""Manufacturing cost accounting and profitability (brief §14).

Actual production cost is read from what was really posted: dispensing
consumption (dyes + chemicals) per bath/batch, and yarn issued per knitting run
(from the inventory sub-ledger). Revenue comes from posted sales invoices. These
roll up into batch cost, order/customer profitability, machine- and shade-wise
cost, standard-vs-actual variance and cost-per-kg.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, QTY_SCALE, quantize
from app.models.commercial import DocStatus, SalesInvoice, SalesOrder
from app.models.dispensing import DispensingTransaction
from app.models.inventory import InventoryTransaction
from app.models.manufacturing import DyeBatch, DyeBath, KnittingProduction
from app.models.masters import Chemical, Customer, Machine, RecipeVersion, ShadeCode
from app.services.manufacturing.recipes import scale_recipe

MZERO = quantize(0, MONEY_SCALE)
QZERO = quantize(0, QTY_SCALE)


def _pct(part, whole) -> Decimal:
    if whole and whole != 0:
        return quantize(Decimal(str(part)) / Decimal(str(whole)) * Decimal("100"), QTY_SCALE)
    return QZERO


# --------------------------- dispensing cost helpers ------------------------

def _batch_bath_ids(db, company_id, batch_id):
    return db.execute(
        select(DyeBath.id).where(DyeBath.company_id == company_id, DyeBath.batch_id == batch_id)
    ).scalars().all()


def _dispensing_cost_split(db, company_id, bath_ids):
    """Return (dye_cost, chemical_cost) over the given baths."""
    dye = chem = MZERO
    if not bath_ids:
        return dye, chem
    for t in db.execute(
        select(DispensingTransaction).where(
            DispensingTransaction.company_id == company_id,
            DispensingTransaction.bath_id.in_(bath_ids))
    ).scalars():
        c = db.get(Chemical, t.chemical_id)
        cost = t.consumption_cost or MZERO
        if c and c.is_dye:
            dye += cost
        else:
            chem += cost
    return dye, chem


def batch_cost(db: Session, company_id: uuid.UUID, batch_id: uuid.UUID) -> dict:
    batch = db.get(DyeBatch, batch_id)
    if batch is None or batch.company_id != company_id:
        raise ValueError("Batch not found")
    bath_ids = _batch_bath_ids(db, company_id, batch_id)
    dye, chem = _dispensing_cost_split(db, company_id, bath_ids)
    total = dye + chem
    fabric = Decimal(str(batch.fabric_kg)) or Decimal("1")
    return {
        "batch_code": batch.batch_code, "fabric_kg": quantize(fabric, QTY_SCALE),
        "dye_cost": dye, "chemical_cost": chem, "total_cost": total,
        "cost_per_kg": quantize(total / fabric, MONEY_SCALE),
    }


# --------------------------- standard vs actual -----------------------------

def bath_standard_vs_actual(db: Session, company_id: uuid.UUID, bath_id: uuid.UUID) -> dict:
    bath = db.get(DyeBath, bath_id)
    if bath is None or bath.company_id != company_id:
        raise ValueError("Bath not found")
    standards = {}
    if bath.recipe_version_id:
        version = db.get(RecipeVersion, bath.recipe_version_id)
        for m in scale_recipe(db, version, bath.fabric_kg, require_approved=False):
            standards[str(m["chemical_id"])] = Decimal(str(m["required_kg"]))

    actual_qty, actual_cost = {}, {}
    for t in db.execute(select(DispensingTransaction).where(
            DispensingTransaction.bath_id == bath.id)).scalars():
        k = str(t.chemical_id)
        actual_qty[k] = actual_qty.get(k, QZERO) + Decimal(str(t.actual_weight))
        actual_cost[k] = actual_cost.get(k, MZERO) + Decimal(str(t.consumption_cost or 0))

    rows = []
    tot_std = tot_act = MZERO
    for cid in sorted(set(standards) | set(actual_qty)):
        chem = db.get(Chemical, uuid.UUID(cid))
        std_qty = standards.get(cid, Decimal("0"))
        std_cost = quantize(std_qty * Decimal(str(chem.standard_cost or 0)), MONEY_SCALE) if chem else MZERO
        act_qty = actual_qty.get(cid, QZERO)
        act_cost = quantize(actual_cost.get(cid, MZERO), MONEY_SCALE)
        tot_std += std_cost; tot_act += act_cost
        rows.append({
            "material_code": chem.code if chem else None,
            "standard_qty": quantize(std_qty, QTY_SCALE), "actual_qty": quantize(act_qty, QTY_SCALE),
            "qty_variance": quantize(act_qty - std_qty, QTY_SCALE),
            "standard_cost": std_cost, "actual_cost": act_cost,
            "cost_variance": quantize(act_cost - std_cost, MONEY_SCALE),
        })
    return {
        "bath_code": bath.bath_code, "rows": rows,
        "total_standard_cost": tot_std, "total_actual_cost": tot_act,
        "total_cost_variance": tot_act - tot_std,
    }


# --------------------------- yarn / knitting cost ---------------------------

def _yarn_cost_for_sources(db, company_id, source_ids):
    if not source_ids:
        return MZERO
    total = db.execute(
        select(func.coalesce(func.sum(InventoryTransaction.total_cost), 0)).where(
            InventoryTransaction.company_id == company_id,
            InventoryTransaction.source_module == "knitting",
            InventoryTransaction.direction == "OUT",
            InventoryTransaction.source_id.in_([str(s) for s in source_ids]))
    ).scalar_one()
    return quantize(total, MONEY_SCALE)


# --------------------------- order / customer -------------------------------

def _order_revenue(db, company_id, order_id):
    total = db.execute(
        select(func.coalesce(func.sum(SalesInvoice.taxable_value), 0)).where(
            SalesInvoice.company_id == company_id, SalesInvoice.sales_order_id == order_id,
            SalesInvoice.status == DocStatus.POSTED)
    ).scalar_one()
    return quantize(total, MONEY_SCALE)


def _order_cost(db, company_id, order_id):
    kp_ids = db.execute(select(KnittingProduction.id).where(
        KnittingProduction.company_id == company_id,
        KnittingProduction.sales_order_id == order_id)).scalars().all()
    yarn = _yarn_cost_for_sources(db, company_id, kp_ids)
    batch_ids = db.execute(select(DyeBatch.id).where(
        DyeBatch.company_id == company_id, DyeBatch.sales_order_id == order_id)).scalars().all()
    bath_ids = []
    for bid in batch_ids:
        bath_ids += _batch_bath_ids(db, company_id, bid)
    dye, chem = _dispensing_cost_split(db, company_id, bath_ids)
    return {"yarn_cost": yarn, "dyeing_cost": dye + chem, "total_cost": yarn + dye + chem}


def order_profitability(db: Session, company_id: uuid.UUID, order_id: uuid.UUID) -> dict:
    order = db.get(SalesOrder, order_id)
    if order is None or order.company_id != company_id:
        raise ValueError("Sales order not found")
    revenue = _order_revenue(db, company_id, order_id)
    cost = _order_cost(db, company_id, order_id)
    gp = revenue - cost["total_cost"]
    return {"order_number": order.number, "revenue": revenue, **cost,
            "gross_profit": gp, "margin_pct": _pct(gp, revenue)}


def order_profitability_list(db: Session, company_id: uuid.UUID) -> list[dict]:
    orders = db.execute(select(SalesOrder).where(SalesOrder.company_id == company_id)).scalars().all()
    return [order_profitability(db, company_id, o.id) for o in orders]


def customer_profitability(db: Session, company_id: uuid.UUID) -> list[dict]:
    customers = db.execute(select(Customer).where(Customer.company_id == company_id)).scalars().all()
    out = []
    for cust in customers:
        orders = db.execute(select(SalesOrder.id).where(
            SalesOrder.company_id == company_id, SalesOrder.customer_id == cust.id)).scalars().all()
        revenue = cost = MZERO
        for oid in orders:
            revenue += _order_revenue(db, company_id, oid)
            cost += _order_cost(db, company_id, oid)["total_cost"]
        # also invoices billed directly to the customer without an order link
        direct = db.execute(
            select(func.coalesce(func.sum(SalesInvoice.taxable_value), 0)).where(
                SalesInvoice.company_id == company_id, SalesInvoice.customer_id == cust.id,
                SalesInvoice.sales_order_id.is_(None), SalesInvoice.status == DocStatus.POSTED)
        ).scalar_one()
        revenue += quantize(direct, MONEY_SCALE)
        if revenue == MZERO and cost == MZERO:
            continue
        gp = revenue - cost
        out.append({"customer": cust.name, "revenue": revenue, "cost": cost,
                    "gross_profit": gp, "margin_pct": _pct(gp, revenue)})
    return out


# --------------------------- machine / shade / per-kg -----------------------

def machine_cost(db: Session, company_id: uuid.UUID) -> list[dict]:
    rows = []
    for m in db.execute(select(Machine).where(Machine.company_id == company_id)).scalars():
        if m.machine_type == "DYEING":
            bath_ids = db.execute(select(DyeBath.id).where(
                DyeBath.company_id == company_id, DyeBath.machine_id == m.id)).scalars().all()
            dye, chem = _dispensing_cost_split(db, company_id, bath_ids)
            cost = dye + chem
        elif m.machine_type == "KNITTING":
            kp_ids = db.execute(select(KnittingProduction.id).where(
                KnittingProduction.company_id == company_id,
                KnittingProduction.machine_id == m.id)).scalars().all()
            cost = _yarn_cost_for_sources(db, company_id, kp_ids)
        else:
            cost = MZERO
        if cost != MZERO:
            rows.append({"machine": m.code, "machine_type": m.machine_type, "cost": cost})
    return rows


def shade_cost(db: Session, company_id: uuid.UUID) -> list[dict]:
    rows = []
    for s in db.execute(select(ShadeCode).where(ShadeCode.company_id == company_id)).scalars():
        batch_ids = db.execute(select(DyeBatch.id).where(
            DyeBatch.company_id == company_id, DyeBatch.shade_id == s.id)).scalars().all()
        bath_ids = []
        for bid in batch_ids:
            bath_ids += _batch_bath_ids(db, company_id, bid)
        dye, chem = _dispensing_cost_split(db, company_id, bath_ids)
        cost = dye + chem
        if cost != MZERO:
            rows.append({"shade": s.code, "name": s.name, "cost": cost})
    return rows


def cost_per_kg_summary(db: Session, company_id: uuid.UUID) -> dict:
    produced = db.execute(select(func.coalesce(func.sum(KnittingProduction.produced_kg), 0)).where(
        KnittingProduction.company_id == company_id)).scalar_one()
    yarn_cost = quantize(db.execute(
        select(func.coalesce(func.sum(InventoryTransaction.total_cost), 0)).where(
            InventoryTransaction.company_id == company_id,
            InventoryTransaction.source_module == "knitting",
            InventoryTransaction.direction == "OUT")).scalar_one(), MONEY_SCALE)
    dyed_kg = db.execute(select(func.coalesce(func.sum(DyeBath.fabric_kg), 0)).where(
        DyeBath.company_id == company_id)).scalar_one()
    all_baths = db.execute(select(DyeBath.id).where(DyeBath.company_id == company_id)).scalars().all()
    d, c = _dispensing_cost_split(db, company_id, all_baths)
    dye_total = d + c
    return {
        "knitted_kg": quantize(produced, QTY_SCALE), "knitted_yarn_cost": yarn_cost,
        "knitted_cost_per_kg": quantize(yarn_cost / produced, MONEY_SCALE) if produced else MZERO,
        "dyed_kg": quantize(dyed_kg, QTY_SCALE), "dyeing_cost": dye_total,
        "dyeing_cost_per_kg": quantize(dye_total / dyed_kg, MONEY_SCALE) if dyed_kg else MZERO,
    }


def production_by_factory(db: Session, company_id: uuid.UUID) -> list[dict]:
    """Factory-wise manufacturing throughput & efficiency.

    Groups knitting production (and dyeing throughput) by the owning factory
    (``factory_id`` on the production records). Reports actual physical output,
    consumption, wastage and rejection per factory, and knitting efficiency
    (produced / (produced + wastage + rejection)). Rows with no factory assigned
    (legacy/unassigned) are grouped under a null factory so they remain visible
    for reconciliation.
    """
    from app.models.core import Branch

    kn = db.execute(
        select(
            KnittingProduction.factory_id,
            func.coalesce(func.sum(KnittingProduction.produced_kg), 0),
            func.coalesce(func.sum(KnittingProduction.yarn_consumed_kg), 0),
            func.coalesce(func.sum(KnittingProduction.wastage_kg), 0),
            func.coalesce(func.sum(KnittingProduction.rejection_kg), 0),
        ).where(KnittingProduction.company_id == company_id)
        .group_by(KnittingProduction.factory_id)
    ).all()
    dye = dict(db.execute(
        select(DyeBatch.factory_id, func.coalesce(func.sum(DyeBatch.fabric_kg), 0))
        .where(DyeBatch.company_id == company_id).group_by(DyeBatch.factory_id)
    ).all())
    names = {b.id: f"{b.code} — {b.name}" for b in db.execute(
        select(Branch).where(Branch.company_id == company_id)).scalars()}

    rows = []
    factory_ids = {r[0] for r in kn} | set(dye.keys())
    for fid in factory_ids:
        produced = next((Decimal(str(r[1])) for r in kn if r[0] == fid), Decimal("0"))
        consumed = next((Decimal(str(r[2])) for r in kn if r[0] == fid), Decimal("0"))
        wastage = next((Decimal(str(r[3])) for r in kn if r[0] == fid), Decimal("0"))
        rejection = next((Decimal(str(r[4])) for r in kn if r[0] == fid), Decimal("0"))
        gross = produced + wastage + rejection
        efficiency = quantize((produced / gross * Decimal("100")) if gross > 0 else Decimal("0"), MONEY_SCALE)
        rows.append({
            "factory_id": str(fid) if fid else None,
            "factory": names.get(fid, "— unassigned —") if fid else "— unassigned —",
            "knitting_produced_kg": quantize(produced, QTY_SCALE),
            "yarn_consumed_kg": quantize(consumed, QTY_SCALE),
            "wastage_kg": quantize(wastage, QTY_SCALE),
            "rejection_kg": quantize(rejection, QTY_SCALE),
            "knitting_efficiency_pct": efficiency,
            "dyeing_fabric_kg": quantize(Decimal(str(dye.get(fid, 0))), QTY_SCALE),
        })
    rows.sort(key=lambda r: r["factory"] or "")
    return rows
