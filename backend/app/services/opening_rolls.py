"""Opening manufactured-roll services (Stage 6F-P3).

Registers physical manufactured fabric rolls at ERP cutover, built on the P1
``OpeningBatch`` lifecycle, the existing ``FabricRoll`` model, the authorised
Roll-ID generator (``roll_identity``), the FIFO inventory engine and the roll
genealogy layer. Each line is EITHER a reference to an existing ``FabricRoll``
(no second roll) OR a genuinely new roll whose ID is minted — only at approval —
by the existing greige/dyed generator.

Guarantees:

* Strict warehouse-stage mapping: GREIGE->GREIGE, DYED->SEMI_FINISHED,
  FINISHED->FINISHED_GOODS (an uncategorised/mismatched warehouse is rejected).
* The existing Roll-ID generator is reused unchanged; no ID is minted during
  import / validation / preview — only in the atomic approval commit.
* At approval each committed line yields exactly one ``FabricRoll`` (existing or
  new), one FIFO ``StockLayer`` (``ItemType.FABRIC``, ``item_id=roll.id``,
  ``event_type="OPENING"``), one ``InventoryTransaction`` and — for a new roll —
  one ``IMPORT`` genealogy origin edge. No accounting voucher is posted.
* Duplicate physical rolls are blocked by ERP Roll ID, legacy label, in-file /
  in-batch dedup, existing stock and the company-scoped DB indexes
  (``uq_fabric_rolls_company_roll_no`` / ``uq_fabric_rolls_company_legacy_label``);
  ambiguous conflicts are flagged, never silently merged.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, QTY_SCALE, quantize
from app.models.core import Branch
from app.models.hybrid import WarehouseCategory
from app.models.inventory import ItemType
from app.models.manufacturing import FabricRoll, RollKind
from app.models.masters import FabricType, ShadeCode, UnitOfMeasure, Warehouse
from app.models.opening import OpeningBatchStatus, OpeningBatchType
from app.models.opening_roll import (
    OpeningRollKind,
    OpeningRollLine,
    OpeningRollRegistration,
)
from app.models.opening_stock import OpeningLineValidation
from app.services import inventory
from app.services import opening as batch_svc
from app.services.audit import record_audit
from app.services.manufacturing import roll_genealogy, roll_identity

MZERO = quantize(0, MONEY_SCALE)
QZERO = quantize(0, QTY_SCALE)

_OPENING_EVENT = "OPENING"
_SOURCE_MODULE = "opening_rolls"
_ROLL_BATCH_TYPE = OpeningBatchType.MANUFACTURED_ROLLS.value

# Strict stage mapping: a roll of this kind must sit in this warehouse category.
_STAGE = {
    OpeningRollKind.GREIGE.value: WarehouseCategory.GREIGE.value,
    OpeningRollKind.DYED.value: WarehouseCategory.SEMI_FINISHED.value,
    OpeningRollKind.FINISHED.value: WarehouseCategory.FINISHED_GOODS.value,
}
_KIND = {
    OpeningRollKind.GREIGE.value: RollKind.GREIGE,
    OpeningRollKind.DYED.value: RollKind.DYED,
    OpeningRollKind.FINISHED.value: RollKind.FINISHED,
}
_V = OpeningLineValidation


class OpeningRollError(ValueError):
    """An invalid opening-roll request."""


class OpeningRollStateError(OpeningRollError):
    """The batch is in the wrong state for the operation."""


# ------------------------------- helpers ------------------------------------

def _now():
    return datetime.now(timezone.utc)


def _clean(v, n):
    if v is None:
        return None
    t = str(v).strip()
    return t[:n] if t else None


def _norm(v):
    """Normalise a legacy label for duplicate comparison only (display value is
    preserved separately)."""
    return " ".join(str(v).strip().upper().split()) if v else None


def _as_qty(raw):
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return quantize(Decimal(str(raw).strip()), QTY_SCALE)
    except (InvalidOperation, ValueError):
        return None


def _as_money(raw):
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return quantize(Decimal(str(raw).strip()), MONEY_SCALE)
    except (InvalidOperation, ValueError):
        return None


def _as_date(raw):
    if raw is None:
        return None
    if isinstance(raw, date):
        return raw
    s = str(raw).strip()
    try:
        return date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None


def _as_bool(raw):
    return str(raw).strip().lower() in ("1", "true", "yes", "y") if raw is not None else False


def _audit(db, actor, action, obj, detail):
    record_audit(db, action=action, entity="opening_roll", entity_id=str(obj.id),
                 actor_id=actor, detail=detail)


# --------------------------- reference resolvers ----------------------------

def _warehouse_by_code(db, company_id, code):
    code = _clean(code, 20)
    if not code:
        return None
    return db.execute(select(Warehouse).where(
        Warehouse.company_id == company_id, Warehouse.code == code)).scalars().first()


def _factory_by_code(db, company_id, code):
    code = _clean(code, 20)
    if not code:
        return None
    return db.execute(select(Branch).where(
        Branch.company_id == company_id, Branch.code == code)).scalars().first()


def _fabric_by_code(db, company_id, code):
    code = _clean(code, 30)
    if not code:
        return None
    return db.execute(select(FabricType).where(
        FabricType.company_id == company_id, FabricType.code == code)).scalars().first()


def _shade_by_code(db, company_id, code):
    code = _clean(code, 40)
    if not code:
        return None
    return db.execute(select(ShadeCode).where(
        ShadeCode.company_id == company_id, ShadeCode.code == code)).scalars().first()


def _uom_by_code(db, code):
    code = _clean(code, 10)
    if not code:
        return None
    return db.execute(select(UnitOfMeasure).where(UnitOfMeasure.code == code)).scalars().first()


def _roll_by_no(db, company_id, roll_no):
    roll_no = _clean(roll_no, 40)
    if not roll_no:
        return None
    return db.execute(select(FabricRoll).where(
        FabricRoll.company_id == company_id, FabricRoll.roll_no == roll_no)).scalars().first()


def _roll_by_legacy(db, company_id, legacy_label):
    """Existing roll carrying this legacy label (exact match; normalised dup
    detection is handled in the dedup pass)."""
    legacy_label = _clean(legacy_label, 80)
    if not legacy_label:
        return None
    return db.execute(select(FabricRoll).where(
        FabricRoll.company_id == company_id, FabricRoll.legacy_label == legacy_label)).scalars().first()


def _fabric_balance_qty(db, company_id, roll_id):
    bal = inventory.balance(db, company_id=company_id, item_type=ItemType.FABRIC, item_id=roll_id)
    return _as_qty(bal["qty_on_hand"]) or QZERO


# ------------------------- build line from a raw row ------------------------

def _build_line(db, batch, row) -> OpeningRollLine:
    errors: list[dict] = []
    line = OpeningRollLine(
        company_id=batch.company_id, batch_id=batch.id, row_number=row.get("_row"),
        erp_roll_no=_clean(row.get("erp_roll_no"), 40),
        legacy_label=_clean(row.get("legacy_label"), 80),
        effective_date=_as_date(row.get("effective_date")) or batch.effective_date,
        structure=_clean(row.get("fabric_sku_or_structure"), 120),
        description=_clean(row.get("description"), 200),
        gsm=_as_qty(row.get("gsm")), width_cm=_as_qty(row.get("width_cm")),
        colour=_clean(row.get("colour"), 60), dye_lot=_clean(row.get("dye_lot"), 40),
        length_m=_as_qty(row.get("length_m")),
        quality_status=_clean(row.get("quality_status"), 20) or "OK",
        job_ref=_clean(row.get("job_ref"), 60), processing_ref=_clean(row.get("processing_ref"), 120),
        source_document=_clean(row.get("source_document"), 120),
        source_reference=_clean(row.get("source_reference"), 200),
        valuation_source=_clean(row.get("valuation_source"), 120),
        physical_verification_status=_clean(row.get("physical_verification_status"), 30),
        disputed_unknown=_as_bool(row.get("disputed_unknown")),
        notes=_clean(row.get("notes"), 2000),
        origin_stream=(_clean(row.get("origin_stream"), 10) or None),
    )

    # roll kind
    rk = (_clean(row.get("roll_kind"), 10) or "").upper()
    if rk not in _STAGE:
        errors.append({"field": "roll_kind", "reason": "roll_kind must be GREIGE, DYED or FINISHED"})
        rk = None
    line.roll_kind = rk

    # strict warehouse stage + factory
    if rk:
        _resolve_warehouse_strict(db, batch, row, line, errors, _STAGE[rk])

    # fabric spec: resolve a known SKU code, else keep as free-text structure.
    if not line.structure:
        errors.append({"field": "fabric_sku_or_structure",
                       "reason": "fabric SKU or structure is required"})
    else:
        ft = _fabric_by_code(db, batch.company_id, line.structure)
        if ft is not None:
            line.fabric_type_id = ft.id

    sh = _shade_by_code(db, batch.company_id, row.get("shade_code"))
    if row.get("shade_code") and sh is None:
        errors.append({"field": "shade_code", "reason": "unknown shade code for this company"})
    elif sh is not None:
        line.shade_id = sh.id

    _resolve_numeric(db, row, line, errors)

    # registration mode + identity resolution
    if line.erp_roll_no:
        _resolve_reference(db, batch, line, errors)
    else:
        _resolve_mint(db, batch, line, errors, rk)

    _finalize(line, errors)
    return line


def _resolve_warehouse_strict(db, batch, row, line, errors, required_cat):
    wh = _warehouse_by_code(db, batch.company_id, row.get("warehouse_code"))
    if wh is None:
        errors.append({"field": "warehouse_code", "reason": "unknown warehouse for this company"})
        return
    if wh.category != required_cat:
        errors.append({"field": "warehouse_code",
                       "reason": f"warehouse '{wh.code}' category must be exactly {required_cat} "
                                 f"for a {line.roll_kind} roll (uncategorised not allowed)"})
        return
    line.warehouse_id = wh.id
    line.warehouse_category = wh.category
    fac_code = _clean(row.get("factory_code"), 20)
    factory_id = None
    if fac_code:
        fac = _factory_by_code(db, batch.company_id, fac_code)
        if fac is None:
            errors.append({"field": "factory_code", "reason": "unknown factory for this company"})
        else:
            factory_id = fac.id
    if factory_id is None:
        factory_id = batch.factory_id
    if batch.factory_id is not None and factory_id is not None and factory_id != batch.factory_id:
        errors.append({"field": "factory_code", "reason": "line factory differs from the batch factory"})
    if factory_id is not None and wh.branch_id is not None and wh.branch_id != factory_id:
        errors.append({"field": "warehouse_code",
                       "reason": f"warehouse '{wh.code}' belongs to a different factory"})
    line.factory_id = factory_id


def _resolve_numeric(db, row, line, errors):
    qty = _as_qty(row.get("net_weight_kg"))
    if qty is None:
        errors.append({"field": "net_weight_kg", "reason": "net weight (kg) is required and numeric"})
    elif qty <= QZERO:
        errors.append({"field": "net_weight_kg", "reason": "net weight must be positive"})
    else:
        line.net_weight_kg = qty

    uom = _uom_by_code(db, row.get("uom_code"))
    if row.get("uom_code") and uom is None:
        errors.append({"field": "uom_code", "reason": "unknown unit of measure"})
    elif uom is not None:
        line.uom_id = uom.id

    unit_cost = _as_money(row.get("unit_cost"))
    total_value = _as_money(row.get("total_value"))
    if unit_cost is not None and unit_cost < MZERO:
        errors.append({"field": "unit_cost", "reason": "unit cost cannot be negative"})
    if total_value is not None and total_value < MZERO:
        errors.append({"field": "total_value", "reason": "total value cannot be negative"})
    if unit_cost is None and total_value is None:
        errors.append({"field": "unit_cost", "reason": "unit cost or total value is required"})
    if line.net_weight_kg and line.net_weight_kg > QZERO:
        if total_value is None and unit_cost is not None:
            total_value = quantize(line.net_weight_kg * unit_cost, MONEY_SCALE)
        if unit_cost is None and total_value is not None:
            unit_cost = quantize(total_value / line.net_weight_kg, MONEY_SCALE)
    line.unit_cost = unit_cost if unit_cost is not None else MZERO
    line.total_value = total_value if total_value is not None else MZERO


def _resolve_reference(db, batch, line, errors):
    line.registration_mode = OpeningRollRegistration.REFERENCE_EXISTING.value
    roll = _roll_by_no(db, batch.company_id, line.erp_roll_no)
    if roll is None:
        errors.append({"field": "erp_roll_no",
                       "reason": "ERP Roll ID not found in this company"})
        return
    line.fabric_roll_id = roll.id
    # Kind compatibility.
    if line.roll_kind and _KIND.get(line.roll_kind) and roll.kind not in (
            _KIND[line.roll_kind], _KIND[line.roll_kind].value):
        errors.append({"field": "roll_kind",
                       "reason": f"existing roll kind {roll.kind} conflicts with import roll_kind "
                                 f"{line.roll_kind} — resolve manually (DISPUTED)"})
    # Warehouse / factory consistency with the existing roll.
    if roll.warehouse_id is not None and line.warehouse_id is not None \
            and roll.warehouse_id != line.warehouse_id:
        errors.append({"field": "warehouse_code",
                       "reason": "existing roll is in a different warehouse — resolve manually (DISPUTED)"})
    if roll.factory_id is not None and line.factory_id is not None \
            and roll.factory_id != line.factory_id:
        errors.append({"field": "factory_code",
                       "reason": "existing roll belongs to a different factory — resolve manually (DISPUTED)"})
    # Must not already carry stock (double-count guard).
    if _fabric_balance_qty(db, batch.company_id, roll.id) > QZERO:
        errors.append({"field": "erp_roll_no",
                       "reason": "existing roll already holds stock; opening would double-count"})


def _resolve_mint(db, batch, line, errors, rk):
    line.registration_mode = OpeningRollRegistration.MINT_NEW.value
    # A genuinely unregistered roll needs a legacy label OR explicit physical
    # verification, so it is never invented silently.
    if not line.legacy_label and not line.physical_verification_status:
        errors.append({"field": "legacy_label",
                       "reason": "a new (unregistered) roll requires a legacy label or a "
                                 "physical_verification_status"})
    # FINISHED rolls need a verified identity stream (never guessed).
    if rk == OpeningRollKind.FINISHED.value:
        os_ = (line.origin_stream or "").upper()
        if os_ not in (OpeningRollKind.GREIGE.value, OpeningRollKind.DYED.value):
            errors.append({"field": "origin_stream",
                           "reason": "finished roll with no ERP Roll ID must set origin_stream "
                                     "GREIGE or DYED (or supply the existing ERP Roll ID)"})
        else:
            line.origin_stream = os_
    # Legacy-label collision with an EXISTING roll.
    if line.legacy_label and _roll_by_legacy(db, batch.company_id, line.legacy_label) is not None:
        errors.append({"field": "legacy_label",
                       "reason": "legacy label already registered on an existing roll"})


def _finalize(line, errors):
    # dedup_key is assigned only by _dedup_pass (after flush gives ids), so two
    # valid rows sharing a key never collide on the unique index before the pass.
    line.dedup_key = None
    if errors:
        line.validation_status = _V.INVALID.value
        line.validation_errors = json.dumps(errors)
    else:
        line.validation_status = _V.VALID.value
        line.validation_errors = None


def _identity_key(line: OpeningRollLine) -> str:
    if line.registration_mode == OpeningRollRegistration.REFERENCE_EXISTING.value:
        return f"ROLL|REF|{(line.erp_roll_no or '').upper()}"
    if line.legacy_label:
        return f"ROLL|LEGACY|{_norm(line.legacy_label)}"
    return f"ROLL|PV|{line.id}"   # physical-verification-only rolls are each unique


def _mark_invalid(line, field, reason):
    errs = json.loads(line.validation_errors) if line.validation_errors else []
    errs.append({"field": field, "reason": reason})
    line.validation_status = _V.INVALID.value
    line.validation_errors = json.dumps(errs)
    line.dedup_key = None


def _dedup_pass(db, batch):
    lines = db.execute(select(OpeningRollLine).where(
        OpeningRollLine.batch_id == batch.id)
        .order_by(OpeningRollLine.row_number, OpeningRollLine.created_at)).scalars().all()
    seen: dict[str, OpeningRollLine] = {}
    for line in lines:
        if line.validation_status != _V.VALID.value:
            continue
        key = _identity_key(line)
        line.dedup_key = key
        if key.startswith("ROLL|PV|"):
            continue  # physical-verification-only rows are inherently unique
        if key in seen:
            _mark_invalid(line, "dedup", "duplicate physical roll within this batch")
            continue
        seen[key] = line
    # Existing-stock conflict for reference lines handled at build; nothing else
    # to re-check here (new rolls cannot have prior stock).


# ------------------------------- import -------------------------------------

def import_rows(db, batch, *, rows, import_file, actor=None) -> dict:
    if batch.status != OpeningBatchStatus.DRAFT.value:
        raise OpeningRollStateError("Opening roll lines can only be imported while the batch is DRAFT")
    if batch.batch_type != _ROLL_BATCH_TYPE:
        raise OpeningRollError(
            f"Batch type '{batch.batch_type}' does not accept manufactured-roll lines")
    built = []
    for row in rows:
        line = _build_line(db, batch, row)
        line.import_file_id = import_file.id
        line.created_by = actor
        line.updated_by = actor
        db.add(line)
        built.append(line)
    db.flush()  # assign ids; dedup keys still NULL
    _dedup_pass(db, batch)
    db.flush()
    valid = sum(1 for l in built if l.validation_status == _V.VALID.value)
    invalid = len(built) - valid
    import_file.row_count = len(built)
    import_file.valid_count = valid
    import_file.invalid_count = invalid
    db.flush()
    _audit(db, actor, "OPENING_ROLL_IMPORT_PARSED", import_file,
           {"company_id": str(batch.company_id), "batch_id": str(batch.id),
            "batch_number": batch.batch_number, "file_name": import_file.file_name,
            "file_hash": import_file.file_hash, "rows": len(built),
            "valid": valid, "invalid": invalid})
    return {"import_file_id": str(import_file.id), "rows": len(built),
            "valid": valid, "invalid": invalid, "line_ids": [str(l.id) for l in built]}


# ------------------------------ read / preview ------------------------------

def list_lines(db, company_id, batch_id, *, status=None, limit=100, offset=0):
    batch = batch_svc.get_batch(db, company_id, batch_id)
    stmt = select(OpeningRollLine).where(OpeningRollLine.batch_id == batch.id)
    if status:
        stmt = stmt.where(OpeningRollLine.validation_status == status)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = db.execute(stmt.order_by(OpeningRollLine.row_number, OpeningRollLine.created_at)
                      .limit(max(1, min(limit, 500))).offset(max(0, offset))).scalars().all()
    return batch, rows, total


def rejections(db, company_id, batch_id):
    batch = batch_svc.get_batch(db, company_id, batch_id)
    rows = db.execute(select(OpeningRollLine).where(
        OpeningRollLine.batch_id == batch.id,
        OpeningRollLine.validation_status == _V.INVALID.value)
        .order_by(OpeningRollLine.row_number)).scalars().all()
    return batch, [{"line_id": str(l.id), "row_number": l.row_number,
                    "erp_roll_no": l.erp_roll_no, "legacy_label": l.legacy_label,
                    "errors": json.loads(l.validation_errors) if l.validation_errors else []}
                   for l in rows]


def reconciliation_preview(db, company_id, batch_id) -> dict:
    batch = batch_svc.get_batch(db, company_id, batch_id)
    lines = db.execute(select(OpeningRollLine).where(
        OpeningRollLine.batch_id == batch.id,
        OpeningRollLine.validation_status.in_([_V.VALID.value, _V.COMMITTED.value]))
        .order_by(OpeningRollLine.row_number)).scalars().all()
    items = []
    total_qty = QZERO
    total_value = MZERO
    conflicts = 0
    for line in lines:
        existing_qty = QZERO
        if line.fabric_roll_id is not None:
            existing_qty = _fabric_balance_qty(db, batch.company_id, line.fabric_roll_id)
        proposed_qty = _as_qty(line.net_weight_kg) or QZERO
        proposed_value = _as_money(line.total_value) or MZERO
        in_conflict = existing_qty > QZERO and line.validation_status == _V.VALID.value
        if in_conflict:
            conflicts += 1
        total_qty = quantize(total_qty + proposed_qty, QTY_SCALE)
        total_value = quantize(total_value + proposed_value, MONEY_SCALE)
        items.append({
            "line_id": str(line.id), "registration_mode": line.registration_mode,
            "roll_kind": line.roll_kind, "erp_roll_no": line.erp_roll_no,
            "legacy_label": line.legacy_label,
            "fabric_roll_id": str(line.fabric_roll_id) if line.fabric_roll_id else None,
            "warehouse_id": str(line.warehouse_id) if line.warehouse_id else None,
            "proposed_qty": str(proposed_qty), "proposed_value": str(proposed_value),
            "existing_on_hand_qty": str(existing_qty),
            "status": line.validation_status, "conflict": bool(in_conflict),
        })
    return {"batch_id": str(batch.id), "batch_status": batch.status,
            "line_count": len(items), "conflicts": conflicts,
            "proposed_total_qty": str(total_qty), "proposed_total_value": str(total_value),
            "accounting_posting": "BLOCKED_PENDING_CA_APPROVAL", "items": items}


# ------------------------- submit / approve / commit ------------------------

def revalidate_batch(db, company_id, batch_id) -> dict:
    batch = batch_svc.get_batch(db, company_id, batch_id)
    if batch.status != OpeningBatchStatus.DRAFT.value:
        raise OpeningRollStateError("Only a DRAFT batch can be revalidated")
    _revalidate(db, batch)
    valid = db.execute(select(func.count()).select_from(OpeningRollLine).where(
        OpeningRollLine.batch_id == batch.id,
        OpeningRollLine.validation_status == _V.VALID.value)).scalar()
    total = db.execute(select(func.count()).select_from(OpeningRollLine).where(
        OpeningRollLine.batch_id == batch.id)).scalar()
    return {"batch_id": str(batch.id), "total": total, "valid": valid, "invalid": total - valid}


def _revalidate(db, batch):
    """Re-run per-line validation from stored fields against live state."""
    lines = db.execute(select(OpeningRollLine).where(
        OpeningRollLine.batch_id == batch.id)).scalars().all()
    for line in lines:
        if line.validation_status == _V.COMMITTED.value:
            continue
        errors: list[dict] = []
        if not line.roll_kind or line.roll_kind not in _STAGE:
            errors.append({"field": "roll_kind", "reason": "roll_kind invalid"})
        if line.warehouse_id is None:
            errors.append({"field": "warehouse_id", "reason": "warehouse unresolved"})
        else:
            wh = db.get(Warehouse, line.warehouse_id)
            req = _STAGE.get(line.roll_kind)
            if wh is None or wh.company_id != batch.company_id:
                errors.append({"field": "warehouse_id", "reason": "warehouse not in company"})
            elif wh.category != req:
                errors.append({"field": "warehouse_id", "reason": f"warehouse must be {req}"})
        if (_as_qty(line.net_weight_kg) or QZERO) <= QZERO:
            errors.append({"field": "net_weight_kg", "reason": "net weight must be positive"})
        if line.registration_mode == OpeningRollRegistration.REFERENCE_EXISTING.value:
            roll = db.get(FabricRoll, line.fabric_roll_id) if line.fabric_roll_id else None
            if roll is None or roll.company_id != batch.company_id:
                errors.append({"field": "erp_roll_no", "reason": "referenced roll missing"})
            elif _fabric_balance_qty(db, batch.company_id, roll.id) > QZERO:
                errors.append({"field": "erp_roll_no", "reason": "referenced roll already holds stock"})
        else:
            if line.legacy_label and _roll_by_legacy(db, batch.company_id, line.legacy_label) is not None:
                errors.append({"field": "legacy_label", "reason": "legacy label already registered"})
            if line.roll_kind == OpeningRollKind.FINISHED.value and \
                    (line.origin_stream or "") not in (OpeningRollKind.GREIGE.value, OpeningRollKind.DYED.value):
                errors.append({"field": "origin_stream", "reason": "finished-roll origin stream unresolved"})
        line.dedup_key = None
        if errors:
            line.validation_status = _V.INVALID.value
            line.validation_errors = json.dumps(errors)
        else:
            line.validation_status = _V.VALID.value
            line.validation_errors = None
    db.flush()
    _dedup_pass(db, batch)
    db.flush()


def _assert_ready(db, batch):
    rows = db.execute(select(OpeningRollLine).where(
        OpeningRollLine.batch_id == batch.id)).scalars().all()
    if not rows:
        raise OpeningRollStateError("The batch has no opening roll lines")
    bad = [l for l in rows if l.validation_status not in (_V.VALID.value, _V.COMMITTED.value)]
    if bad:
        raise OpeningRollStateError(
            f"{len(bad)} roll line(s) are invalid or unvalidated; correct them before proceeding")


def submit_batch(db, company_id, batch_id, *, submitted_by, actor_name=None):
    batch = batch_svc.get_batch(db, company_id, batch_id)
    if batch.status == OpeningBatchStatus.DRAFT.value:
        _assert_ready(db, batch)
    return batch_svc.submit_batch(db, company_id, batch_id,
                                  submitted_by=submitted_by, actor_name=actor_name)


def approve_batch(db, company_id, batch_id, *, approved_by, actor_name=None):
    batch = batch_svc.get_batch(db, company_id, batch_id)
    if batch.status == OpeningBatchStatus.SUBMITTED.value:
        _revalidate(db, batch)
        _assert_ready(db, batch)
    batch = batch_svc.approve_batch(db, company_id, batch_id,
                                    approved_by=approved_by, actor_name=actor_name)
    lines = db.execute(select(OpeningRollLine).where(
        OpeningRollLine.batch_id == batch.id).order_by(OpeningRollLine.row_number)).scalars().all()
    committed = 0
    for line in lines:
        if line.validation_status == _V.COMMITTED.value:
            continue
        if line.validation_status != _V.VALID.value:
            raise OpeningRollError(f"Cannot approve: roll line {line.row_number or line.id} is not valid")
        _commit_line(db, batch, line, actor=approved_by)
        committed += 1
    db.flush()
    _audit(db, approved_by, "OPENING_ROLL_COMMIT", batch,
           {"company_id": str(batch.company_id), "batch_number": batch.batch_number,
            "rolls_committed": committed})
    return batch, committed


def _mint_roll_no(db, batch, line) -> str:
    """Mint a new Roll ID using the EXISTING authorised generator, chosen by the
    roll's identity stream. Never a new/second generator."""
    when = line.effective_date or batch.effective_date or date.today()
    kind = line.roll_kind
    if kind == OpeningRollKind.GREIGE.value:
        return roll_identity.new_greige_roll_id(db, company_id=batch.company_id, when=when)
    if kind == OpeningRollKind.DYED.value:
        return roll_identity.new_dyed_roll_id(db, company_id=batch.company_id, when=when)
    # FINISHED: use the verified origin stream.
    if line.origin_stream == OpeningRollKind.GREIGE.value:
        return roll_identity.new_greige_roll_id(db, company_id=batch.company_id, when=when)
    if line.origin_stream == OpeningRollKind.DYED.value:
        return roll_identity.new_dyed_roll_id(db, company_id=batch.company_id, when=when)
    raise OpeningRollError("Finished-roll origin stream unresolved; cannot mint Roll ID")


def _commit_line(db, batch, line: OpeningRollLine, *, actor):
    qty = _as_qty(line.net_weight_kg)
    if qty is None or qty <= QZERO:
        raise OpeningRollError("Cannot commit a roll line with non-positive weight")
    value = _as_money(line.total_value) or MZERO

    if line.registration_mode == OpeningRollRegistration.REFERENCE_EXISTING.value:
        roll = db.get(FabricRoll, line.fabric_roll_id)
        if roll is None or roll.company_id != batch.company_id:
            raise OpeningRollError("Referenced roll not found at commit")
        if _fabric_balance_qty(db, batch.company_id, roll.id) > QZERO:
            raise OpeningRollError("Referenced roll already holds stock; opening would double-count")
        wh_id = roll.warehouse_id or line.warehouse_id
        if roll.warehouse_id is None and line.warehouse_id is not None:
            roll.warehouse_id = line.warehouse_id
        # Preserve the legacy label on the existing roll if it has none (never overwrite).
        if line.legacy_label and not roll.legacy_label:
            roll.legacy_label = line.legacy_label
        new_roll = False
    else:
        roll_identity.assert_factory_in_company(
            db, company_id=batch.company_id, factory_id=line.factory_id)
        roll_no = _mint_roll_no(db, batch, line)
        roll = FabricRoll(
            company_id=batch.company_id, factory_id=line.factory_id, roll_no=roll_no,
            kind=_KIND[line.roll_kind], legacy_label=line.legacy_label, barcode=roll_no,
            fabric_type_id=line.fabric_type_id, gsm=line.gsm, width_cm=line.width_cm,
            length_m=line.length_m, weight_kg=qty, warehouse_id=line.warehouse_id,
            quality_status=line.quality_status or "OK", status="ACTIVE",
            created_by=actor, updated_by=actor)
        db.add(roll)
        db.flush()
        new_roll = True
        line.fabric_roll_id = roll.id

    layer = inventory.receive(
        db, company_id=batch.company_id, item_type=ItemType.FABRIC, item_id=roll.id,
        qty=qty, unit_cost=line.unit_cost, value=value, warehouse_id=wh_id if not new_roll else line.warehouse_id,
        event_type=_OPENING_EVENT, source_module=_SOURCE_MODULE, source_id=str(line.id))

    if new_roll:
        # Explicit opening origin — an IMPORT edge, never fabricated production.
        roll_genealogy.record_edge(
            db, company_id=batch.company_id, process_type=roll_genealogy.IMPORT,
            event_type=roll_genealogy.PRODUCE, output_roll_id=roll.id, source_roll_id=None,
            factory_id=line.factory_id, event_group_id=uuid.uuid4(),
            production_ref=batch.batch_number, uom=roll_genealogy.UOM_KG,
            output_weight_kg=qty, output_qty=qty,
            event_datetime=datetime.now(timezone.utc), recorded_by=actor)

    line.stock_layer_id = layer.id
    line.committed_at = _now()
    line.validation_status = _V.COMMITTED.value
    line.updated_by = actor
    _audit(db, actor, "OPENING_ROLL_LINE_COMMITTED", line,
           {"company_id": str(batch.company_id), "batch_number": batch.batch_number,
            "registration_mode": line.registration_mode, "roll_id": str(roll.id),
            "roll_no": roll.roll_no, "new_roll": new_roll, "legacy_label": line.legacy_label,
            "warehouse_id": str(line.warehouse_id) if line.warehouse_id else None,
            "qty": str(qty), "value": str(value), "stock_layer_id": str(layer.id),
            "event_type": _OPENING_EVENT})
