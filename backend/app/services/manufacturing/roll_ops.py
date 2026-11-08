"""Fabric-roll split and merge.

Both reorganise the fabric sub-ledger without touching the GL (the fabric stays
in the same inventory account; only the roll it belongs to changes), so no
voucher is posted and total stock value is preserved.

* Split — carve a roll into child rolls; each child inherits the parent's
  lineage and gets its share of the parent's stock at the same cost. The parent
  keeps any remainder, or is closed (status SPLIT) when fully split.
* Merge — combine several active rolls into one; each source's stock moves into
  the new roll and the source is closed (status MERGED) and points to it.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, QTY_SCALE, quantize
from app.models.inventory import ItemType
from app.models.manufacturing import FabricRoll
from app.services import inventory

QZERO = quantize(0, QTY_SCALE)


class RollOpError(Exception):
    """An invalid split/merge request."""


def _D(v) -> Decimal:
    return Decimal(str(v or 0))


def _usable(roll: FabricRoll) -> bool:
    status = roll.status or "ACTIVE"
    return status == "ACTIVE" and roll.delivery_note_id is None


def _child(parent: FabricRoll, *, roll_no: str, weight) -> FabricRoll:
    return FabricRoll(
        company_id=parent.company_id, roll_no=roll_no, barcode=roll_no, kind=parent.kind,
        knitting_production_id=parent.knitting_production_id, fabric_type_id=parent.fabric_type_id,
        machine_id=parent.machine_id, yarn_id=parent.yarn_id, yarn_lot_id=parent.yarn_lot_id,
        sales_order_id=parent.sales_order_id, weight_kg=weight, gsm=parent.gsm,
        width_cm=parent.width_cm, warehouse_id=parent.warehouse_id,
        quality_status=parent.quality_status, status="ACTIVE", parent_roll_id=parent.id,
    )


def split_roll(db: Session, *, company_id: uuid.UUID, roll_id: uuid.UUID,
               parts: list[dict], created_by: uuid.UUID | None = None) -> dict:
    """``parts``: [{weight_kg, roll_no?}]. Their total must not exceed the
    parent's remaining weight."""
    parent = db.get(FabricRoll, roll_id)
    if parent is None or parent.company_id != company_id:
        raise RollOpError("Roll not found")
    if not _usable(parent):
        raise RollOpError(f"Roll {parent.roll_no} is {parent.status or 'ACTIVE'}/dispatched and cannot be split")
    if not parts:
        raise RollOpError("Provide at least one part")

    weights = [quantize(_D(p["weight_kg"]), QTY_SCALE) for p in parts]
    if any(w <= 0 for w in weights):
        raise RollOpError("Each part weight must be positive")
    total = quantize(sum(weights, QZERO), QTY_SCALE)
    if total > quantize(_D(parent.weight_kg), QTY_SCALE):
        raise RollOpError("Split parts exceed the roll weight")

    children = []
    for i, (p, w) in enumerate(zip(parts, weights), start=1):
        res = inventory.consume(db, company_id=company_id, item_type=ItemType.FABRIC,
                                item_id=parent.id, qty=w, event_type="SPLIT",
                                source_module="roll_split", source_id=str(parent.id))
        cost = res["total_cost"]
        roll_no = (p.get("roll_no") or "").strip() or f"{parent.roll_no}-S{i}"
        child = _child(parent, roll_no=roll_no, weight=w)
        db.add(child)
        db.flush()
        unit = quantize(cost / w, MONEY_SCALE) if w > 0 else MONEY_SCALE
        inventory.receive(db, company_id=company_id, item_type=ItemType.FABRIC, item_id=child.id,
                          qty=w, unit_cost=unit, warehouse_id=child.warehouse_id,
                          event_type="SPLIT", source_module="roll_split", source_id=str(parent.id))
        children.append(child)

    parent.weight_kg = quantize(_D(parent.weight_kg) - total, QTY_SCALE)
    if parent.weight_kg <= 0:
        parent.status = "SPLIT"
    db.flush()
    return {"parent_id": str(parent.id), "parent_remaining_kg": parent.weight_kg,
            "parent_status": parent.status,
            "children": [{"id": str(c.id), "roll_no": c.roll_no, "weight_kg": c.weight_kg} for c in children]}


def merge_rolls(db: Session, *, company_id: uuid.UUID, roll_ids: list[uuid.UUID],
                new_roll_no: str | None = None, created_by: uuid.UUID | None = None) -> dict:
    if len(roll_ids) < 2:
        raise RollOpError("Merging needs at least two rolls")
    rolls: list[FabricRoll] = []
    seen: set[uuid.UUID] = set()
    for rid in roll_ids:
        if rid in seen:
            raise RollOpError("A roll is listed twice")
        seen.add(rid)
        r = db.get(FabricRoll, rid)
        if r is None or r.company_id != company_id:
            raise RollOpError(f"Roll {rid} not found")
        if not _usable(r):
            raise RollOpError(f"Roll {r.roll_no} is {r.status or 'ACTIVE'}/dispatched and cannot be merged")
        rolls.append(r)

    first = rolls[0]
    total_weight = quantize(sum((_D(r.weight_kg) for r in rolls), QZERO), QTY_SCALE)
    roll_no = (new_roll_no or "").strip() or f"{first.roll_no}-MRG"
    merged = _child(first, roll_no=roll_no, weight=total_weight)
    merged.parent_roll_id = None
    db.add(merged)
    db.flush()

    total_cost = quantize(0, MONEY_SCALE)
    moved_qty = QZERO
    for r in rolls:
        bal = inventory.balance(db, company_id=company_id, item_type=ItemType.FABRIC, item_id=r.id)
        q = bal["qty_on_hand"]
        if q > 0:
            res = inventory.consume(db, company_id=company_id, item_type=ItemType.FABRIC,
                                    item_id=r.id, qty=q, event_type="MERGE",
                                    source_module="roll_merge", source_id=str(merged.id))
            total_cost = quantize(total_cost + res["total_cost"], MONEY_SCALE)
            moved_qty = quantize(moved_qty + q, QTY_SCALE)
        r.status = "MERGED"
        r.weight_kg = QZERO
        r.parent_roll_id = merged.id

    if moved_qty > 0:
        unit = quantize(total_cost / moved_qty, MONEY_SCALE)
        inventory.receive(db, company_id=company_id, item_type=ItemType.FABRIC, item_id=merged.id,
                          qty=moved_qty, unit_cost=unit, warehouse_id=merged.warehouse_id,
                          event_type="MERGE", source_module="roll_merge", source_id=str(merged.id))
    db.flush()
    return {"merged_id": str(merged.id), "roll_no": merged.roll_no,
            "weight_kg": merged.weight_kg, "cost_value": total_cost, "sources": len(rolls)}
