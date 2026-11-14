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
from app.models.manufacturing import DyeBatch, KnittingProduction
from app.models.quality import FinishingTransaction
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

    # Yarn-only BOM: dye/chemical usage is not tracked at this stage, so no
    # chemical (dyeing) reservations are exploded. Only yarn is reserved/issued.
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
    allow_over_issue: bool = False,
    voucher_date=None,
    created_by: uuid.UUID | None = None,
    post_gl: bool = True,
) -> ProductionConsumption:
    """Issue (consume) a reserved material from stock against the order.

    Materials may only be issued while the order is PLANNED or IN_PROGRESS.
    Issuing beyond the reservation's planned quantity is rejected unless
    ``allow_over_issue`` is set (the authorized over-issue path). The inventory
    consume, the consumption record and the reservation/order status change all
    happen in the caller's single transaction (consume takes a PG advisory lock),
    so a concurrent double-issue cannot draw the same FIFO layer twice.
    """
    order_id = _uid(order_id)
    reservation_id = _uid(reservation_id)
    lot_id = _uid(lot_id)
    warehouse_id = _uid(warehouse_id)
    order = db.get(ProductionOrder, order_id)
    if order is None:
        raise ProductionError("order not found")
    if order.status not in (ProductionOrderStatus.PLANNED, ProductionOrderStatus.IN_PROGRESS):
        raise ProductionError(f"order is {order.status.value}; materials can only be issued "
                              "while PLANNED or IN_PROGRESS")
    r = db.get(ProductionReservation, reservation_id)
    if r is None or r.production_order_id != order_id:
        raise ProductionError("reservation does not belong to this order")
    qty = _d(qty)
    if qty <= QZERO:
        raise ProductionError("issue qty must be positive")
    new_issued = _d(r.issued_qty) + qty
    if not allow_over_issue and new_issued > _d(r.planned_qty):
        raise ProductionError(
            f"issue exceeds planned {r.planned_qty} for this reservation "
            f"(already issued {r.issued_qty}); use the authorized over-issue path")

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
    db.flush()
    # GL at issue time, in the SAME transaction as the FIFO consume (HIGH-2):
    #   Dr WIP (yarn cost)   Cr Yarn Inventory (yarn cost)
    # keyed by a per-consumption reference so re-issues never duplicate, and the
    # DB unique(company_id, posting_reference) prevents concurrent duplicates.
    if post_gl and _d(res["total_cost"]) > QZERO and r.item_type == ItemType.YARN.value:
        from app.services.accounting import posting_engine as pe
        from app.services.accounting.posting_rules import resolve_account
        acc = lambda slot: resolve_account(db, order.company_id, "GREY_PRODUCTION", slot)
        pe.post_voucher(db, pe.PostingRequest(
            company_id=order.company_id, voucher_type_code="PJ",
            voucher_date=voucher_date or _today(), posting_reference=f"PO:{order.number}:issue:{cons.id}",
            narration=f"Yarn issue to WIP {order.number}", branch_id=order.factory_id,
            source_module="PRODUCTION_ORDER", source_id=str(order.id), created_by=created_by,
            lines=[pe.PostingLine(acc("wip"), debit=res["total_cost"], narration="Yarn into WIP"),
                   pe.PostingLine(acc("yarn"), credit=res["total_cost"], narration="Yarn inventory issue")]))
    r.issued_qty = quantize(_d(r.issued_qty) + qty, QTY_SCALE)
    if _d(r.issued_qty) >= _d(r.planned_qty):
        r.status = ReservationStatus.ISSUED
    else:
        r.status = ReservationStatus.PARTIALLY_ISSUED
    if order.status == ProductionOrderStatus.PLANNED:
        order.status = ProductionOrderStatus.IN_PROGRESS
    db.flush()
    return cons


def complete_order(db: Session, *, order_id: uuid.UUID, produced_qty_kg,
                   allow_outstanding: bool = False) -> ProductionOrder:
    """Record produced quantity and mark the order COMPLETED.

    * produced quantity must be positive;
    * only a PLANNED/IN_PROGRESS order can be completed (a COMPLETED/CLOSED/
      CANCELLED order is not silently re-completed — that is an error);
    * completion is refused while any reservation is still outstanding
      (RESERVED / PARTIALLY_ISSUED), unless ``allow_outstanding`` is set.
    """
    order_id = _uid(order_id)
    order = db.get(ProductionOrder, order_id)
    if order is None:
        raise ProductionError("order not found")
    if order.status not in (ProductionOrderStatus.PLANNED, ProductionOrderStatus.IN_PROGRESS):
        raise ProductionError(f"order is {order.status.value}; cannot complete")
    produced = _d(produced_qty_kg)
    if produced <= QZERO:
        raise ProductionError("produced_qty_kg must be positive")
    if not allow_outstanding:
        outstanding = db.execute(
            select(ProductionReservation).where(
                ProductionReservation.production_order_id == order_id,
                ProductionReservation.status.in_(
                    [ReservationStatus.RESERVED, ReservationStatus.PARTIALLY_ISSUED]),
            )
        ).scalars().first()
        if outstanding is not None:
            raise ProductionError(
                "cannot complete: materials are still outstanding (reserved/partially "
                "issued). Issue or release them first, or complete with override.")
    order.produced_qty_kg = quantize(produced, QTY_SCALE)
    order.status = ProductionOrderStatus.COMPLETED
    db.flush()
    return order


# --- grey fabric actual cost -------------------------------------------------

def _yarn_actual_cost(db: Session, order_id: uuid.UUID) -> Decimal:
    """Σ genuine actual yarn cost issued to the order (FIFO draws), knitting stage."""
    total = MZERO
    for c in db.execute(
        select(ProductionConsumption).where(
            ProductionConsumption.production_order_id == order_id,
            ProductionConsumption.item_type == ItemType.YARN.value,
        )
    ).scalars().all():
        total += _d(c.actual_cost)
    return quantize(total, MONEY_SCALE)


def record_actual_knitting_conversion_cost(db: Session, *, order_id: uuid.UUID, amount) -> ProductionOrder:
    """Record the ACTUAL knitting conversion cost for an order, independently of
    the standard. Only a non-negative amount is accepted; it is never derived
    from the standard. Can be updated until the grey output is valued."""
    order_id = _uid(order_id)
    order = db.get(ProductionOrder, order_id)
    if order is None:
        raise ProductionError("order not found")
    if order.grey_valued_at is not None:
        raise ProductionError("grey output already valued; conversion cost is frozen")
    amt = _d(amount)
    if amt < QZERO:
        raise ProductionError("actual conversion cost cannot be negative")
    order.actual_knitting_conversion_cost = quantize(amt, MONEY_SCALE)
    db.flush()
    return order


def value_grey_output(db: Session, *, order_id: uuid.UUID, warehouse_id: uuid.UUID | None = None,
                      voucher_date: date | None = None, created_by: uuid.UUID | None = None,
                      post_gl: bool = True) -> dict:
    """Value the completed order's grey fabric into FABRIC inventory at ACTUAL
    cost, so dyeing can consume it by FIFO, and post the production journal.

    Grey actual cost = actual yarn issued (FIFO) + actual knitting conversion
    (only if entered). The grey fabric is received as an inventory item keyed by
    the production order id (``ItemType.FABRIC``) at exactly the accumulated cost.
    Idempotent: refuses a second valuation; the GL voucher posting_reference is
    unique at the DB level.

    GL (one balanced production journal, ``PO:{num}:grey``):
        Dr WIP (yarn)                 Cr Yarn Inventory (yarn)
        Dr WIP (conversion)           Cr Expenses Payable (conversion)   [if any]
        Dr Greige Inventory (total)   Cr WIP (total)
    WIP nets to zero; greige inventory debit equals the sub-ledger receipt.
    """
    order_id = _uid(order_id)
    warehouse_id = _uid(warehouse_id)
    order = db.get(ProductionOrder, order_id)
    if order is None:
        raise ProductionError("order not found")
    if order.status != ProductionOrderStatus.COMPLETED:
        raise ProductionError("order must be COMPLETED before valuing grey output")
    if order.grey_valued_at is not None:
        raise ProductionError("grey output already valued")
    grey_kg = _d(order.produced_qty_kg)
    if grey_kg <= QZERO:
        raise ProductionError("no produced quantity to value")

    yarn_cost = _yarn_actual_cost(db, order_id)
    conv = _d(order.actual_knitting_conversion_cost) if order.actual_knitting_conversion_cost is not None else MZERO
    total = quantize(yarn_cost + conv, MONEY_SCALE)
    # receive grey fabric at EXACTLY the accumulated cost (no per-kg dust)
    inventory.receive(
        db, company_id=order.company_id, item_type=ItemType.FABRIC, item_id=order.id,
        qty=grey_kg, unit_cost=quantize(total / grey_kg, MONEY_SCALE), value=total,
        warehouse_id=warehouse_id or order.warehouse_id,
        event_type="GREY_PRODUCE", source_module="PRODUCTION_ORDER", source_id=str(order.id),
        posting_reference=f"PO:{order.number}:grey",
    )
    if post_gl and total > QZERO:
        # Yarn->WIP was posted at issue time (HIGH-2). This voucher adds the
        # recorded knitting conversion into WIP and moves WIP into greige
        # inventory; WIP (holding yarn from issue + conversion here) nets to zero.
        from app.services.accounting import posting_engine as pe
        from app.services.accounting.posting_rules import resolve_account
        acc = lambda slot: resolve_account(db, order.company_id, "GREY_PRODUCTION", slot)
        lines = []
        if conv > QZERO:
            lines.append(pe.PostingLine(acc("wip"), debit=conv, narration="Knitting conversion into WIP"))
            lines.append(pe.PostingLine(acc("conversion_payable"), credit=conv, narration="Conversion accrued"))
        lines.append(pe.PostingLine(acc("greige"), debit=total, narration="Grey fabric received"))
        lines.append(pe.PostingLine(acc("wip"), credit=total, narration="WIP to greige"))
        pe.post_voucher(db, pe.PostingRequest(
            company_id=order.company_id, voucher_type_code="PJ",
            voucher_date=voucher_date or _today(), posting_reference=f"PO:{order.number}:grey",
            narration=f"Grey production {order.number}", branch_id=order.factory_id,
            source_module="PRODUCTION_ORDER", source_id=str(order.id), created_by=created_by,
            lines=lines))
    order.grey_inventory_value = total
    order.grey_valued_at = datetime.now(timezone.utc)
    db.flush()
    return {
        "order_id": str(order.id), "grey_output_kg": quantize(grey_kg, QTY_SCALE),
        "yarn_cost": yarn_cost,
        "conversion_cost": quantize(conv, MONEY_SCALE) if order.actual_knitting_conversion_cost is not None else None,
        "grey_total_cost": order.grey_inventory_value,
        "grey_cost_per_kg": quantize(total / grey_kg, MONEY_SCALE) if grey_kg > QZERO else None,
    }


def grey_actual_cost(db: Session, order_id: uuid.UUID) -> dict:
    """Grey fabric standard-vs-actual cost report for an order (knitting scope).

    Standard grey cost/kg = std_yarn_cost + std_knitting_cost (from the stamped
    BOM version). Actual = actual yarn (FIFO) + actual knitting conversion
    (only if entered). Missing actual components are flagged, not substituted.
    """
    order_id = _uid(order_id)
    order = db.get(ProductionOrder, order_id)
    if order is None:
        raise ProductionError("order not found")
    version = db.get(FabricBomVersion, order.bom_version_id)
    grey_kg = _d(order.produced_qty_kg)

    yarn_actual = _yarn_actual_cost(db, order_id)
    conv = order.actual_knitting_conversion_cost
    conv_entered = conv is not None
    actual_total = yarn_actual + (_d(conv) if conv_entered else MZERO)

    std_yarn = _d(version.std_yarn_cost) if version else MZERO
    std_knit = _d(version.std_knitting_cost) if version else MZERO
    std_per_kg = quantize(std_yarn + std_knit, MONEY_SCALE)

    missing = []
    if not conv_entered:
        missing.append("actual_knitting_conversion_cost")
    actual_per_kg = quantize(actual_total / grey_kg, MONEY_SCALE) if grey_kg > QZERO else None
    variance_per_kg = (quantize(actual_per_kg - std_per_kg, MONEY_SCALE)
                       if actual_per_kg is not None else None)
    return {
        "order_id": str(order.id), "number": order.number,
        "grey_output_kg": quantize(grey_kg, QTY_SCALE),
        "std_grey_cost_per_kg": std_per_kg,
        "actual_yarn_cost": yarn_actual,
        "actual_knitting_conversion_cost": quantize(_d(conv), MONEY_SCALE) if conv_entered else None,
        "actual_grey_total_cost": quantize(actual_total, MONEY_SCALE),
        "actual_grey_cost_per_kg": actual_per_kg,
        "variance_per_kg": variance_per_kg,
        "grey_inventory_value": quantize(_d(order.grey_inventory_value), MONEY_SCALE),
        "grey_valued": order.grey_valued_at is not None,
        "missing_cost_components": missing,
        "cost_complete": not missing,
    }


def cancel_order(db: Session, *, order_id: uuid.UUID, voucher_date=None,
                 created_by: uuid.UUID | None = None, post_gl: bool = True) -> ProductionOrder:
    """Cancel a PLANNED/IN_PROGRESS order, RETURNING any already-issued material to
    stock and REVERSING its GL — not silently releasing it as if never consumed
    (HIGH-2).

    For each issued consumption:
      * the FIFO-issue GL voucher is reversed (balanced REVERSAL: Dr Yarn Inv /
        Cr WIP), preserving audit history; and
      * the material is returned to inventory at its exact issued cost
        (``PRODUCTION_RETURN``), restoring the sub-ledger.
    A terminal order (COMPLETED/CLOSED/CANCELLED) cannot be cancelled, and an
    order whose grey output has already been valued is refused (the grey fabric
    now exists in inventory — reverse that separately). Idempotent: a second
    cancel is rejected by the status guard, and the REVERSAL voucher's unique
    posting_reference prevents concurrent duplicate reversals.
    """
    order_id = _uid(order_id)
    order = db.get(ProductionOrder, order_id)
    if order is None:
        raise ProductionError("order not found")
    if order.status not in (ProductionOrderStatus.PLANNED, ProductionOrderStatus.IN_PROGRESS):
        raise ProductionError(f"order is {order.status.value}; cannot cancel")
    if order.grey_valued_at is not None:
        raise ProductionError("grey output already valued; cannot cancel (reverse grey separately)")

    from app.models.accounting import VoucherStatus
    from app.services.accounting import posting_engine as pe
    vdate = voucher_date or _today()
    for cons in db.execute(
        select(ProductionConsumption).where(ProductionConsumption.production_order_id == order_id)
    ).scalars().all():
        qty = _d(cons.qty)
        cost = _d(cons.actual_cost)
        if qty <= QZERO:
            continue
        # reverse the issue GL voucher (balanced), if it was posted
        if post_gl:
            iv = pe.find_existing(db, order.company_id, f"PO:{order.number}:issue:{cons.id}")
            if iv is not None and VoucherStatus(iv.status) == VoucherStatus.POSTED:
                pe.reverse_voucher(db, iv.id, reason=f"production order {order.number} cancelled",
                                   created_by=created_by, reversal_date=vdate)
        # return the material to stock at its exact issued cost
        inventory.receive(
            db, company_id=order.company_id, item_type=ItemType(cons.item_type), item_id=cons.item_id,
            qty=qty, unit_cost=quantize(cost / qty, MONEY_SCALE) if qty > QZERO else MZERO, value=cost,
            lot_id=cons.lot_id, warehouse_id=order.warehouse_id, event_type="PRODUCTION_RETURN",
            source_module="PRODUCTION_ORDER", source_id=str(order.id),
            posting_reference=f"PO:{order.number}:return:{cons.id}")

    for r in db.execute(
        select(ProductionReservation).where(ProductionReservation.production_order_id == order_id)
    ).scalars().all():
        if r.status != ReservationStatus.RELEASED:
            r.issued_qty = QZERO     # material returned to stock
            r.status = ReservationStatus.RELEASED
    order.status = ProductionOrderStatus.CANCELLED
    db.flush()
    return order


def _actual_stage_outputs(db: Session, order_id: uuid.UUID) -> dict:
    """Genuine actual output / loss quantities for an order, read from the real
    production actuals linked by ``production_order_id``. Empty when nothing is
    linked yet (these are recorded quantities, never fabricated)."""
    out = {s.value: {"output_kg": MZERO, "wastage_kg": MZERO, "rejection_kg": MZERO,
                     "linked": False} for s in ProductionStage}
    kn = out[ProductionStage.KNITTING.value]
    for k in db.execute(select(KnittingProduction).where(
            KnittingProduction.production_order_id == order_id)).scalars().all():
        kn["output_kg"] += _d(k.produced_kg)
        kn["wastage_kg"] += _d(k.wastage_kg)
        kn["rejection_kg"] += _d(k.rejection_kg)
        kn["linked"] = True
    dy = out[ProductionStage.DYEING.value]
    for b in db.execute(select(DyeBatch).where(
            DyeBatch.production_order_id == order_id)).scalars().all():
        dy["output_kg"] += _d(b.fabric_kg)
        dy["linked"] = True
    fn = out[ProductionStage.FINISHING.value]
    for f in db.execute(select(FinishingTransaction).where(
            FinishingTransaction.production_order_id == order_id)).scalars().all():
        fn["output_kg"] += _d(f.output_weight_kg)
        fn["linked"] = True
    return out


def _actual_conversion_cost(db: Session, order, stage: str, outputs: dict):
    """Genuine actual CONVERSION (machine/labour/overhead) cost for a stage, or
    ``None`` when no such actual is recorded.

    The production actuals (``KnittingProduction``, ``DyeBatch``/``DyeBath``,
    ``FinishingTransaction``) record *quantities and output*, not an attributable
    money conversion cost, and GL cost-centre postings are not attributed to a
    specific production order. So no genuine per-order actual conversion cost
    exists today — this returns ``None`` (unavailable) rather than substituting
    the standard. It is the single extension point for a future actual source.
    """
    return None


def variance_report(db: Session, order_id: uuid.UUID) -> dict:
    """Stage-wise planned (standard) vs actual cost comparison for an order.

    **Material** cost is compared genuinely: planned = Σ reservation standard
    cost; actual = Σ consumption cost drawn from FIFO stock. **Conversion** cost
    (machine/labour/overhead) is reported as a *standard* figure; its actual is
    only populated from a genuine recorded source and is otherwise marked
    ``None`` with ``conversion_actual_available = False`` — the standard value is
    never relabelled as actual. ``actual_output_kg`` comes from the linked
    production actuals.
    """
    order_id = _uid(order_id)
    order = db.get(ProductionOrder, order_id)
    if order is None:
        raise ProductionError("order not found")
    version = db.get(FabricBomVersion, order.bom_version_id)
    planned_qty = _d(order.planned_qty_kg)
    produced_qty = _d(order.produced_qty_kg) or planned_qty
    outputs = _actual_stage_outputs(db, order_id)

    stages = {s.value: {"planned_material": MZERO, "actual_material": MZERO,
                        "std_conversion": MZERO, "wastage_qty": MZERO, "rejection_qty": MZERO}
              for s in ProductionStage}

    for r in db.execute(
        select(ProductionReservation).where(ProductionReservation.production_order_id == order_id)
    ).scalars().all():
        stages[r.stage]["planned_material"] += _d(r.std_cost)

    for c in db.execute(
        select(ProductionConsumption).where(ProductionConsumption.production_order_id == order_id)
    ).scalars().all():
        stages[c.stage]["actual_material"] += _d(c.actual_cost)
        stages[c.stage]["wastage_qty"] += _d(c.wastage_qty)
        stages[c.stage]["rejection_qty"] += _d(c.rejection_qty)

    if version is not None:
        stages[ProductionStage.KNITTING.value]["std_conversion"] = _d(version.std_knitting_cost) * produced_qty
        stages[ProductionStage.DYEING.value]["std_conversion"] = _d(version.std_dyeing_cost) * produced_qty
        stages[ProductionStage.FINISHING.value]["std_conversion"] = _d(version.std_finishing_cost) * produced_qty

    stage_rows = []
    tot_planned_mat = tot_actual_mat = tot_std_conv = MZERO
    conversion_actuals_available = True
    for name, s in stages.items():
        pm, am = s["planned_material"], s["actual_material"]
        std_conv = s["std_conversion"]
        act_conv = _actual_conversion_cost(db, order, name, outputs)
        conv_available = act_conv is not None
        conversion_actuals_available = conversion_actuals_available and conv_available
        mat_var = am - pm
        out_kg = outputs[name]["output_kg"] if outputs[name]["linked"] else None
        tot_planned_mat += pm
        tot_actual_mat += am
        tot_std_conv += std_conv
        stage_rows.append({
            "stage": name,
            # material (genuine standard vs actual)
            "planned_material_cost": quantize(pm, MONEY_SCALE),
            "actual_material_cost": quantize(am, MONEY_SCALE),
            "material_variance": quantize(mat_var, MONEY_SCALE),
            "material_variance_pct": quantize((mat_var / pm * Decimal("100")) if pm else MZERO, 2),
            # conversion (standard; actual only when genuinely recorded)
            "std_conversion_cost": quantize(std_conv, MONEY_SCALE),
            "actual_conversion_cost": None if act_conv is None else quantize(act_conv, MONEY_SCALE),
            "conversion_actual_available": conv_available,
            # genuine actual output + loss from linked actuals
            "actual_output_kg": quantize(out_kg, QTY_SCALE) if out_kg is not None else None,
            "actual_material_cost_per_kg": (
                quantize(am / out_kg, MONEY_SCALE) if out_kg and out_kg > QZERO else None),
            "wastage_qty": quantize(s["wastage_qty"], QTY_SCALE),
            "rejection_qty": quantize(s["rejection_qty"], QTY_SCALE),
        })

    total_mat_var = tot_actual_mat - tot_planned_mat
    # A fully-costed actual total is only reported when every stage's conversion
    # actual is genuinely available; otherwise it stays None (never fabricated).
    total_actual_cost = (quantize(tot_actual_mat, MONEY_SCALE)
                         if conversion_actuals_available else None)
    notes = []
    if not conversion_actuals_available:
        notes.append(
            "Actual conversion cost (machine/labour/overhead) is not recorded per "
            "production order; only standard conversion is shown. Material actuals "
            "are genuine (FIFO stock issues). total_actual_cost is therefore "
            "unavailable and left null rather than substituting standard.")
    return {
        "order_id": str(order.id), "number": order.number,
        "bom_version_id": str(order.bom_version_id),
        "planned_qty_kg": quantize(planned_qty, QTY_SCALE),
        "produced_qty_kg": quantize(_d(order.produced_qty_kg), QTY_SCALE),
        "std_total_cost_per_kg": quantize(_d(version.std_total_cost_per_kg) if version else MZERO, MONEY_SCALE),
        "stages": stage_rows,
        # material totals (genuine)
        "total_planned_material_cost": quantize(tot_planned_mat, MONEY_SCALE),
        "total_actual_material_cost": quantize(tot_actual_mat, MONEY_SCALE),
        "total_material_variance": quantize(total_mat_var, MONEY_SCALE),
        "total_material_variance_pct": quantize(
            (total_mat_var / tot_planned_mat * Decimal("100")) if tot_planned_mat else MZERO, 2),
        # conversion (standard)
        "total_std_conversion_cost": quantize(tot_std_conv, MONEY_SCALE),
        "conversion_actuals_available": conversion_actuals_available,
        "total_actual_cost": total_actual_cost,
        "notes": notes,
    }
