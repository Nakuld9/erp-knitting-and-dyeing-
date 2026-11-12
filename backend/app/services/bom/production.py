"""Production integration for the fabric BOM.

A :class:`ProductionOrder` is created against an **approved** BOM version, which
it *stamps* so later BOM revisions never change a historical order. Creating an
order explodes the BOM into material **reservations** (yarn for knitting +
dyes/chemicals for dyeing), scaled to the planned quantity with wastage and
process-loss grossing. Materials are then **issued** (consumed from FIFO stock
via the inventory service, with production-order source linkage), and actual
consumption / wastage / rejection / reprocessing is recorded per stage.

``variance_report`` compares, stage by stage, the standard (planned) cost from
the stamped BOM version against the actual cost drawn from inventory.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, QTY_SCALE, quantize
from app.models.fabric_bom import (
    BomStatus,
    FabricBomVersion,
    ProductionConsumption,
    ProductionOrder,
    ProductionOrderStatus,
    ProductionReservation,
    ProductionStage,
    ReservationStatus,
)
from app.models.inventory import ItemType
from app.models.masters import Chemical, RecipeMaterial, RecipeVersion
from app.services.bom import service as bom_service
from app.services.inventory import service as inventory

MZERO = Decimal("0")
QZERO = Decimal("0")


class ProductionError(Exception):
    pass


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else MZERO


def _uid(v):
    return v if isinstance(v, uuid.UUID) or v is None else uuid.UUID(str(v))


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _gross_yarn_per_kg(y) -> Decimal:
    return (_d(y.std_consumption_per_kg)
            * (Decimal("1") + _d(y.wastage_percent) / Decimal("100"))
            * (Decimal("1") + _d(y.process_loss_percent) / Decimal("100")))


def _resolve_version(db: Session, *, bom_id, bom_version_id, on_date) -> FabricBomVersion:
    if bom_version_id is not None:
        v = db.get(FabricBomVersion, bom_version_id)
        if v is None:
            raise ProductionError("BOM version not found")
    elif bom_id is not None:
        v = bom_service.effective_version(db, bom_id, on_date)
        if v is None:
            raise ProductionError("no effective APPROVED BOM version on that date")
    else:
        raise ProductionError("either bom_id or bom_version_id is required")
    if v.status != BomStatus.APPROVED:
        raise ProductionError(
            f"BOM version {v.version_no} is {v.status.value}; production needs an APPROVED version"
        )
    return v


def create_order(
    db: Session,
    *,
    company_id: uuid.UUID,
    planned_qty_kg,
    bom_id: uuid.UUID | None = None,
    bom_version_id: uuid.UUID | None = None,
    number: str,
    order_date: date | None = None,
    factory_id: uuid.UUID | None = None,
    sales_order_id: uuid.UUID | None = None,
    shade_id: uuid.UUID | None = None,
    warehouse_id: uuid.UUID | None = None,
    planned_start: date | None = None,
    planned_end: date | None = None,
    created_by: uuid.UUID | None = None,
) -> ProductionOrder:
    """Create an order against an approved BOM version and explode reservations."""
    company_id, bom_id, bom_version_id = _uid(company_id), _uid(bom_id), _uid(bom_version_id)
    factory_id, sales_order_id = _uid(factory_id), _uid(sales_order_id)
    shade_id, warehouse_id = _uid(shade_id), _uid(warehouse_id)
    planned = _d(planned_qty_kg)
    if planned <= QZERO:
        raise ProductionError("planned_qty_kg must be positive")
    v = _resolve_version(db, bom_id=bom_id, bom_version_id=bom_version_id, on_date=order_date or _today())

    order = ProductionOrder(
        company_id=company_id, factory_id=factory_id, number=number,
        bom_version_id=v.id, sales_order_id=sales_order_id, shade_id=shade_id,
        planned_qty_kg=quantize(planned, QTY_SCALE), warehouse_id=warehouse_id,
        planned_start=planned_start, planned_end=planned_end,
        status=ProductionOrderStatus.PLANNED, created_by=created_by,
    )
    db.add(order)
    db.flush()

    # --- explode yarn reservations (knitting stage) ---
    for y in v.yarns:
        qty = quantize(planned * _gross_yarn_per_kg(y), QTY_SCALE)
        if qty <= QZERO:
            continue
        unit = _d(y.std_unit_cost)
        db.add(ProductionReservation(
            company_id=company_id, production_order_id=order.id, stage=ProductionStage.KNITTING.value,
            item_type=ItemType.YARN.value, item_id=y.yarn_id, planned_qty=qty,
            std_unit_cost=quantize(unit, MONEY_SCALE), std_cost=quantize(qty * unit, MONEY_SCALE),
            status=ReservationStatus.RESERVED,
        ))

    # --- explode dye/chemical reservations (dyeing stage) ---
    if v.dye_recipe_version_id is not None:
        mats = db.execute(
            select(RecipeMaterial).where(RecipeMaterial.version_id == v.dye_recipe_version_id)
        ).scalars().all()
        for m in mats:
            if m.percent_owf is not None:
                per_kg = _d(m.percent_owf) / Decimal("100")
            elif m.grams_per_kg is not None:
                per_kg = _d(m.grams_per_kg) / Decimal("1000")
            else:
                per_kg = MZERO
            qty = quantize(planned * per_kg, QTY_SCALE)
            if qty <= QZERO:
                continue
            chem = db.get(Chemical, m.chemical_id)
            unit = _d(chem.standard_cost) if chem else MZERO
            db.add(ProductionReservation(
                company_id=company_id, production_order_id=order.id, stage=ProductionStage.DYEING.value,
                item_type=ItemType.CHEMICAL.value, item_id=m.chemical_id, planned_qty=qty,
                std_unit_cost=quantize(unit, MONEY_SCALE), std_cost=quantize(qty * unit, MONEY_SCALE),
                status=ReservationStatus.RESERVED,
            ))
    db.flush()
    return order


def material_requirements(db: Session, order_id: uuid.UUID) -> list[dict]:
    """Planned vs on-hand per reserved material (shortage flagged)."""
    order_id = _uid(order_id)
    order = db.get(ProductionOrder, order_id)
    if order is None:
        raise ProductionError("order not found")
    out = []
    for r in db.execute(
        select(ProductionReservation).where(ProductionReservation.production_order_id == order_id)
    ).scalars().all():
        bal = inventory.balance(
            db, company_id=order.company_id, item_type=r.item_type, item_id=r.item_id,
            warehouse_id=order.warehouse_id,
        )
        on_hand = _d(bal["qty_on_hand"])
        planned = _d(r.planned_qty)
        out.append({
            "reservation_id": str(r.id), "stage": r.stage, "item_type": r.item_type,
            "item_id": str(r.item_id), "planned_qty": planned, "issued_qty": _d(r.issued_qty),
            "on_hand": on_hand, "shortage": max(QZERO, planned - _d(r.issued_qty) - on_hand),
            "status": r.status.value if hasattr(r.status, "value") else r.status,
        })
    return out


def issue_material(
    db: Session,
    *,
    order_id: uuid.UUID,
    reservation_id: uuid.UUID,
    qty,
    lot_id: uuid.UUID | None = None,
    warehouse_id: uuid.UUID | None = None,
    method: str = "FIFO",
    wastage_qty=0,
    rejection_qty=0,
    is_reprocess: bool = False,
) -> ProductionConsumption:
    """Issue (consume) a reserved material from stock against the order."""
    order_id = _uid(order_id)
    reservation_id = _uid(reservation_id)
    lot_id = _uid(lot_id)
    warehouse_id = _uid(warehouse_id)
    order = db.get(ProductionOrder, order_id)
    if order is None:
        raise ProductionError("order not found")
    if order.status in (ProductionOrderStatus.CANCELLED, ProductionOrderStatus.CLOSED):
        raise ProductionError(f"order is {order.status.value}; cannot issue materials")
    r = db.get(ProductionReservation, reservation_id)
    if r is None or r.production_order_id != order_id:
        raise ProductionError("reservation does not belong to this order")
    qty = _d(qty)
    if qty <= QZERO:
        raise ProductionError("issue qty must be positive")

    pref = f"PO:{order.number}:{r.stage}"
    res = inventory.consume(
        db, company_id=order.company_id, item_type=ItemType(r.item_type), item_id=r.item_id,
        qty=qty, lot_id=lot_id, warehouse_id=warehouse_id or order.warehouse_id, method=method,
        event_type="PRODUCTION_ISSUE", source_module="PRODUCTION_ORDER", source_id=str(order.id),
        posting_reference=pref,
    )
    cons = ProductionConsumption(
        company_id=order.company_id, production_order_id=order.id, stage=r.stage,
        item_type=r.item_type, item_id=r.item_id, lot_id=lot_id, qty=quantize(qty, QTY_SCALE),
        actual_cost=res["total_cost"], wastage_qty=_d(wastage_qty), rejection_qty=_d(rejection_qty),
        is_reprocess=is_reprocess, posting_reference=pref,
    )
    db.add(cons)
    r.issued_qty = quantize(_d(r.issued_qty) + qty, QTY_SCALE)
    if _d(r.issued_qty) >= _d(r.planned_qty):
        r.status = ReservationStatus.ISSUED
    else:
        r.status = ReservationStatus.PARTIALLY_ISSUED
    if order.status == ProductionOrderStatus.PLANNED:
        order.status = ProductionOrderStatus.IN_PROGRESS
    db.flush()
    return cons


def complete_order(db: Session, *, order_id: uuid.UUID, produced_qty_kg) -> ProductionOrder:
    order_id = _uid(order_id)
    order = db.get(ProductionOrder, order_id)
    if order is None:
        raise ProductionError("order not found")
    order.produced_qty_kg = quantize(_d(produced_qty_kg), QTY_SCALE)
    order.status = ProductionOrderStatus.COMPLETED
    db.flush()
    return order


def variance_report(db: Session, order_id: uuid.UUID) -> dict:
    """Stage-wise planned (standard) vs actual cost comparison for an order."""
    order_id = _uid(order_id)
    order = db.get(ProductionOrder, order_id)
    if order is None:
        raise ProductionError("order not found")
    version = db.get(FabricBomVersion, order.bom_version_id)
    planned_qty = _d(order.planned_qty_kg)
    produced_qty = _d(order.produced_qty_kg) or planned_qty

    stages = {s.value: {"planned_material": MZERO, "actual_material": MZERO,
                        "std_conversion": MZERO, "wastage_qty": MZERO, "rejection_qty": MZERO}
              for s in ProductionStage}

    # planned material cost = Σ reservation std_cost per stage
    for r in db.execute(
        select(ProductionReservation).where(ProductionReservation.production_order_id == order_id)
    ).scalars().all():
        stages[r.stage]["planned_material"] += _d(r.std_cost)

    # actual material cost + wastage/rejection = Σ consumption per stage
    for c in db.execute(
        select(ProductionConsumption).where(ProductionConsumption.production_order_id == order_id)
    ).scalars().all():
        stages[c.stage]["actual_material"] += _d(c.actual_cost)
        stages[c.stage]["wastage_qty"] += _d(c.wastage_qty)
        stages[c.stage]["rejection_qty"] += _d(c.rejection_qty)

    # standard conversion cost per stage (from the stamped version) × produced qty
    if version is not None:
        stages[ProductionStage.KNITTING.value]["std_conversion"] = _d(version.std_knitting_cost) * produced_qty
        stages[ProductionStage.DYEING.value]["std_conversion"] = _d(version.std_dyeing_cost) * produced_qty
        stages[ProductionStage.FINISHING.value]["std_conversion"] = _d(version.std_finishing_cost) * produced_qty

    stage_rows = []
    tot_planned = tot_actual = MZERO
    for name, s in stages.items():
        planned = s["planned_material"] + s["std_conversion"]
        actual = s["actual_material"] + s["std_conversion"]  # conversion at std (no GL actual here)
        var = actual - planned
        tot_planned += planned
        tot_actual += actual
        stage_rows.append({
            "stage": name,
            "planned_material_cost": quantize(s["planned_material"], MONEY_SCALE),
            "actual_material_cost": quantize(s["actual_material"], MONEY_SCALE),
            "std_conversion_cost": quantize(s["std_conversion"], MONEY_SCALE),
            "planned_cost": quantize(planned, MONEY_SCALE),
            "actual_cost": quantize(actual, MONEY_SCALE),
            "variance": quantize(var, MONEY_SCALE),
            "variance_pct": quantize((var / planned * Decimal("100")) if planned else MZERO, 2),
            "wastage_qty": quantize(s["wastage_qty"], QTY_SCALE),
            "rejection_qty": quantize(s["rejection_qty"], QTY_SCALE),
        })

    total_var = tot_actual - tot_planned
    return {
        "order_id": str(order.id), "number": order.number,
        "bom_version_id": str(order.bom_version_id),
        "planned_qty_kg": quantize(planned_qty, QTY_SCALE),
        "produced_qty_kg": quantize(_d(order.produced_qty_kg), QTY_SCALE),
        "std_total_cost_per_kg": quantize(_d(version.std_total_cost_per_kg) if version else MZERO, MONEY_SCALE),
        "stages": stage_rows,
        "total_planned_cost": quantize(tot_planned, MONEY_SCALE),
        "total_actual_cost": quantize(tot_actual, MONEY_SCALE),
        "total_variance": quantize(total_var, MONEY_SCALE),
        "total_variance_pct": quantize((total_var / tot_planned * Decimal("100")) if tot_planned else MZERO, 2),
    }
