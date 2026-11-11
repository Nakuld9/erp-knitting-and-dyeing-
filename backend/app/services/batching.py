"""Batching: assign greige rolls to a dye batch (the dyeing lot).

Links each selected roll to the batch, stamps its ``dye_batch_id``, and
recomputes the batch fabric weight from the assigned rolls — so the roll ->
batch -> bath -> dispensing -> lot traceability is established at batching time,
not just in code.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.types import QTY_SCALE, quantize
from app.models.manufacturing import DyeBatch, FabricRoll, RollKind


class BatchingError(Exception):
    pass


def assign_rolls(db: Session, company_id: uuid.UUID, batch: DyeBatch,
                 roll_ids: list[uuid.UUID]) -> DyeBatch:
    for rid in roll_ids:
        roll = db.get(FabricRoll, rid)
        if roll is None or roll.company_id != company_id:
            raise BatchingError(f"Roll {rid} not found")
        if roll.kind != RollKind.GREIGE:
            raise BatchingError(f"Roll {roll.roll_no} is not a greige roll")
        if roll.dye_batch_id and roll.dye_batch_id != batch.id:
            raise BatchingError(f"Roll {roll.roll_no} is already assigned to another batch")
        roll.dye_batch_id = batch.id
        if roll not in batch.rolls:
            batch.rolls.append(roll)

    total = sum((Decimal(str(r.weight_kg or 0)) for r in batch.rolls), Decimal("0"))
    batch.fabric_kg = quantize(total, QTY_SCALE)
    db.flush()
    return batch
