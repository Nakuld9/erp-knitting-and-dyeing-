"""Manufacturing roll genealogy service (Stage 2 traceability).

Records :class:`~app.models.genealogy.RollGenealogyEdge` rows describing real
plant transitions. This layer is **traceability only** — it never touches the
inventory ledger, FIFO layers or the GL (those remain authoritative). It:

* supports the actual workflows — knitting (yarn -> greige), dyeing
  (greige -> dyed) and finishing (semi-finished -> finished), plus partial
  processing, rejection, recorded process loss and correction/reversal;
* keeps weight (kg) and length (m) in separate fields and never converts between
  them;
* never auto-classifies an unexplained quantity difference as process loss —
  loss and rejection must be supplied explicitly from recorded evidence;
* prevents duplicate consumption of the same source quantity within one event;
* preserves original edges on correction — a REVERSAL edge references the
  original via ``correction_of_id`` and the original is never overwritten.

Split and merge are intentionally **not** implemented (excluded from Stage 2).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import QTY_SCALE, quantize
from app.models.genealogy import RollGenealogyEdge
from app.models.manufacturing import FabricRoll

# Process types
KNIT = "KNIT"
DYE = "DYE"
FINISH = "FINISH"
REWORK = "REWORK"
REJECT = "REJECT"
SCRAP = "SCRAP"
CORRECTION = "CORRECTION"
IMPORT = "IMPORT"

# Event types
PRODUCE = "PRODUCE"
CONSUME = "CONSUME"
PARTIAL = "PARTIAL"
LOSS = "LOSS"
REVERSAL = "REVERSAL"

UOM_KG = "KG"
UOM_M = "M"

_TOLERANCE = Decimal("0.001")   # kg/metre reconciliation tolerance


class GenealogyError(ValueError):
    """An invalid genealogy event (isolation breach, negative/duplicate qty, or
    a reconciliation that does not balance)."""


def _d(v) -> Decimal | None:
    if v is None:
        return None
    return Decimal(str(v))


def _q(v):
    d = _d(v)
    return None if d is None else quantize(d, QTY_SCALE)


def _roll(db: Session, company_id: uuid.UUID, roll_id: uuid.UUID | None,
          label: str) -> FabricRoll | None:
    if roll_id is None:
        return None
    roll = db.get(FabricRoll, roll_id)
    if roll is None:
        raise GenealogyError(f"{label} roll {roll_id} not found")
    if roll.company_id != company_id:
        # Hard company-isolation guard: an edge may never span companies.
        raise GenealogyError(f"{label} roll belongs to another company")
    return roll


def record_edge(
    db: Session,
    *,
    company_id: uuid.UUID,
    process_type: str,
    event_type: str = PRODUCE,
    source_roll_id: uuid.UUID | None = None,
    output_roll_id: uuid.UUID | None = None,
    factory_id: uuid.UUID | None = None,
    event_group_id: uuid.UUID | None = None,
    production_ref: str | None = None,
    uom: str | None = None,
    input_qty=None,
    output_qty=None,
    input_weight_kg=None,
    output_weight_kg=None,
    input_length_m=None,
    output_length_m=None,
    process_loss=None,
    rejected_qty=None,
    event_datetime: datetime | None = None,
    recorded_by: uuid.UUID | None = None,
    audit_ref: str | None = None,
    correction_of_id: uuid.UUID | None = None,
    notes: str | None = None,
    _allow_negative: bool = False,
) -> RollGenealogyEdge:
    """Append one genealogy edge after validating isolation and quantities.

    ``source``/``output`` quantities are stored as given — this method never
    derives loss from (input - output); supply ``process_loss``/``rejected_qty``
    explicitly where they are known. ``_allow_negative`` is used only by
    :func:`reverse_edge` to store negated (reversing) quantities.
    """
    if source_roll_id is None and output_roll_id is None:
        raise GenealogyError("A genealogy edge needs a source or an output roll")

    source = _roll(db, company_id, source_roll_id, "Source")
    output = _roll(db, company_id, output_roll_id, "Output")

    # Negatives are never valid for recorded quantities (except reversal edges).
    if not _allow_negative:
        for name, val in (("input_qty", input_qty), ("output_qty", output_qty),
                          ("input_weight_kg", input_weight_kg), ("output_weight_kg", output_weight_kg),
                          ("input_length_m", input_length_m), ("output_length_m", output_length_m),
                          ("process_loss", process_loss), ("rejected_qty", rejected_qty)):
            d = _d(val)
            if d is not None and d < 0:
                raise GenealogyError(f"{name} cannot be negative")

    edge = RollGenealogyEdge(
        company_id=company_id,
        factory_id=factory_id if factory_id is not None else (
            getattr(source, "factory_id", None) or getattr(output, "factory_id", None)),
        event_group_id=event_group_id or uuid.uuid4(),
        source_roll_id=source_roll_id,
        output_roll_id=output_roll_id,
        process_type=process_type,
        event_type=event_type,
        production_ref=production_ref,
        uom=uom,
        input_qty=_q(input_qty), output_qty=_q(output_qty),
        input_weight_kg=_q(input_weight_kg), output_weight_kg=_q(output_weight_kg),
        input_length_m=_q(input_length_m), output_length_m=_q(output_length_m),
        process_loss=_q(process_loss), rejected_qty=_q(rejected_qty),
        event_datetime=event_datetime or datetime.now(timezone.utc),
        recorded_by=recorded_by, audit_ref=audit_ref,
        correction_of_id=correction_of_id, notes=notes,
        created_by=recorded_by,
    )
    db.add(edge)
    db.flush()
    return edge


# ---- workflow helpers -------------------------------------------------------

def record_knitting(db: Session, roll: FabricRoll, *, event_group_id: uuid.UUID | None = None,
                    production_ref: str | None = None, recorded_by: uuid.UUID | None = None,
                    event_datetime: datetime | None = None) -> RollGenealogyEdge:
    """Origin event: yarn store -> greige roll (no source roll)."""
    return record_edge(
        db, company_id=roll.company_id, process_type=KNIT, event_type=PRODUCE,
        output_roll_id=roll.id, factory_id=roll.factory_id, event_group_id=event_group_id,
        production_ref=production_ref, uom=UOM_KG,
        output_weight_kg=roll.weight_kg, output_length_m=roll.length_m,
        output_qty=roll.weight_kg, recorded_by=recorded_by, event_datetime=event_datetime,
    )


def record_dyeing(db: Session, output_roll: FabricRoll, source_roll_ids: list[uuid.UUID], *,
                 event_group_id: uuid.UUID, production_ref: str | None = None,
                 recorded_by: uuid.UUID | None = None,
                 event_datetime: datetime | None = None) -> list[RollGenealogyEdge]:
    """Greige -> dyed. One PRODUCE edge per (source greige roll -> dyed output),
    all sharing ``event_group_id``. When no greige source rolls are known, a
    single origin-style edge (source NULL) is recorded instead so the dyed roll
    still has a traceable production event.
    """
    edges: list[RollGenealogyEdge] = []
    srcs = list(dict.fromkeys(source_roll_ids))   # de-dup, preserve order
    if not srcs:
        edges.append(record_edge(
            db, company_id=output_roll.company_id, process_type=DYE, event_type=PRODUCE,
            output_roll_id=output_roll.id, factory_id=output_roll.factory_id,
            event_group_id=event_group_id, production_ref=production_ref, uom=UOM_KG,
            output_weight_kg=output_roll.weight_kg, output_qty=output_roll.weight_kg,
            recorded_by=recorded_by, event_datetime=event_datetime))
        return edges
    for sid in srcs:
        edges.append(record_edge(
            db, company_id=output_roll.company_id, process_type=DYE, event_type=PRODUCE,
            source_roll_id=sid, output_roll_id=output_roll.id, factory_id=output_roll.factory_id,
            event_group_id=event_group_id, production_ref=production_ref, uom=UOM_KG,
            output_weight_kg=output_roll.weight_kg, output_qty=output_roll.weight_kg,
            recorded_by=recorded_by, event_datetime=event_datetime))
    return edges


def record_finishing(db: Session, roll: FabricRoll, *, input_weight_kg=None, output_weight_kg=None,
                    event_group_id: uuid.UUID | None = None, production_ref: str | None = None,
                    recorded_by: uuid.UUID | None = None,
                    event_datetime: datetime | None = None) -> RollGenealogyEdge:
    """Semi-finished -> finished. The SAME physical roll changes stage and keeps
    its identity (no new Roll ID), so source == output."""
    return record_edge(
        db, company_id=roll.company_id, process_type=FINISH, event_type=PRODUCE,
        source_roll_id=roll.id, output_roll_id=roll.id, factory_id=roll.factory_id,
        event_group_id=event_group_id, production_ref=production_ref, uom=UOM_KG,
        input_weight_kg=input_weight_kg, output_weight_kg=output_weight_kg,
        recorded_by=recorded_by, event_datetime=event_datetime,
    )


# ---- reconciliation & partial processing ------------------------------------

def reconcile(*, input_qty, accepted_output, process_loss=0, rejected_qty=0, remaining=0) -> None:
    """Validate ``input = accepted + loss + rejected + remaining`` (within a
    small tolerance). Raises on a mismatch or any negative component. Never
    computes a component itself — all four outputs are supplied from evidence."""
    i = _d(input_qty) or Decimal(0)
    parts = {"accepted_output": _d(accepted_output) or Decimal(0),
             "process_loss": _d(process_loss) or Decimal(0),
             "rejected_qty": _d(rejected_qty) or Decimal(0),
             "remaining": _d(remaining) or Decimal(0)}
    if i < 0:
        raise GenealogyError("input_qty cannot be negative")
    for name, v in parts.items():
        if v < 0:
            raise GenealogyError(f"{name} cannot be negative")
    diff = i - sum(parts.values())
    if abs(diff) > _TOLERANCE:
        raise GenealogyError(
            f"reconciliation does not balance: input {i} != accepted+loss+rejected+remaining "
            f"({sum(parts.values())}); unexplained {diff}")


def record_partial(db: Session, *, company_id: uuid.UUID, source_roll_id: uuid.UUID,
                  output_roll_id: uuid.UUID | None, input_qty, accepted_output,
                  process_loss=0, rejected_qty=0, remaining=0, uom: str = UOM_KG,
                  factory_id: uuid.UUID | None = None, event_group_id: uuid.UUID | None = None,
                  production_ref: str | None = None, recorded_by: uuid.UUID | None = None,
                  event_datetime: datetime | None = None, notes: str | None = None) -> RollGenealogyEdge:
    """Record a partial-processing event, reconciling the quantities first.

    ``input = accepted + loss + rejected + remaining`` must balance. The caller
    supplies every component from recorded evidence; this method does not infer
    loss. Duplicate consumption of the same source quantity in the same event
    group is rejected.
    """
    reconcile(input_qty=input_qty, accepted_output=accepted_output,
              process_loss=process_loss, rejected_qty=rejected_qty, remaining=remaining)
    grp = event_group_id or uuid.uuid4()
    _guard_no_duplicate_consumption(db, company_id, source_roll_id, grp, input_qty)
    return record_edge(
        db, company_id=company_id, process_type=REWORK, event_type=PARTIAL,
        source_roll_id=source_roll_id, output_roll_id=output_roll_id, factory_id=factory_id,
        event_group_id=grp, production_ref=production_ref, uom=uom,
        input_qty=input_qty, output_qty=accepted_output,
        process_loss=process_loss, rejected_qty=rejected_qty,
        recorded_by=recorded_by, event_datetime=event_datetime, notes=notes,
    )


def _guard_no_duplicate_consumption(db: Session, company_id: uuid.UUID,
                                   source_roll_id: uuid.UUID, event_group_id: uuid.UUID,
                                   input_qty) -> None:
    """Reject recording the same source consumption twice within one event
    group (a common double-post / retry mistake)."""
    existing = db.execute(
        select(RollGenealogyEdge).where(
            RollGenealogyEdge.company_id == company_id,
            RollGenealogyEdge.event_group_id == event_group_id,
            RollGenealogyEdge.source_roll_id == source_roll_id,
            RollGenealogyEdge.event_type != REVERSAL,
        )
    ).scalars().first()
    if existing is not None:
        raise GenealogyError(
            "duplicate consumption: this source roll is already consumed in this event group")


def reverse_edge(db: Session, edge_id: uuid.UUID, *, company_id: uuid.UUID,
                recorded_by: uuid.UUID | None, reason: str,
                event_datetime: datetime | None = None) -> RollGenealogyEdge:
    """Record a REVERSAL/CORRECTION edge for ``edge_id``. The original is
    preserved unchanged; the new edge references it via ``correction_of_id`` and
    carries negated quantities. A reason is mandatory."""
    if not (reason or "").strip():
        raise GenealogyError("a reversal requires a reason")
    original = db.get(RollGenealogyEdge, edge_id)
    if original is None or original.company_id != company_id:
        raise GenealogyError("original genealogy edge not found")
    if original.event_type == REVERSAL:
        raise GenealogyError("cannot reverse a reversal")

    def _neg(v):
        d = _d(v)
        return None if d is None else -d

    return record_edge(
        db, company_id=company_id, process_type=CORRECTION, event_type=REVERSAL,
        source_roll_id=original.source_roll_id, output_roll_id=original.output_roll_id,
        factory_id=original.factory_id, event_group_id=original.event_group_id,
        production_ref=original.production_ref, uom=original.uom,
        input_qty=_neg(original.input_qty), output_qty=_neg(original.output_qty),
        input_weight_kg=_neg(original.input_weight_kg), output_weight_kg=_neg(original.output_weight_kg),
        input_length_m=_neg(original.input_length_m), output_length_m=_neg(original.output_length_m),
        process_loss=_neg(original.process_loss), rejected_qty=_neg(original.rejected_qty),
        recorded_by=recorded_by, correction_of_id=original.id, notes=reason,
        event_datetime=event_datetime,
        _allow_negative=True,
    )
