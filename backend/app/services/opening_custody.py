"""Opening vendor-custody & vendor-held-WIP services (Stage 6F-P4).

Registers company-owned goods (a yarn lot or a manufactured fabric roll) that are
physically held by an outside job worker on the cutover date, built on the P1
``OpeningBatch`` lifecycle, the existing vendor-custody model (VENDOR_CUSTODY
warehouse + ``JobWorkIssue``) and the P2/P3 opening pipeline.

Critical safeguard — opening custody is NOT a new physical issue. At approval each
committed line:
  * resolves the vendor's VENDOR_CUSTODY warehouse via the existing
    ``get_or_create_custody_warehouse`` (company-owned storage at the vendor);
  * creates exactly one FIFO ``StockLayer`` there via ``inventory.receive`` with
    ``event_type="OPENING"`` — NO ``transfer_stock``, NO factory-warehouse layer,
    NO accounting voucher;
  * records one opening-origin ``JobWorkIssue`` (status OUTSTANDING,
    ``posting_reference="OPENING:<batch>:<row>"``, ``source_warehouse_id=NULL``) so
    the position appears in the existing vendor-wise outstanding reconciliation
    exactly once — never a fabricated transfer / receipt / production event;
  * for a newly minted roll, records one ``IMPORT`` genealogy origin edge.

Decision (see P4 audit §5): ``JobWorkIssue`` is reused as the opening-origin
custody record because it represents an outstanding custody position honestly;
its semantics are not changed and ``vendor_outstanding`` is not modified. The
``OPENING:`` posting_reference prefix makes opening-origin issues distinguishable
from ordinary challans.
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
from app.models.inventory import ItemType
from app.models.jobwork import JobWorkIssue, JobWorkIssueStatus, JobWorkProcess
from app.models.manufacturing import FabricRoll, RollKind
from app.models.masters import FabricType, ShadeCode, Supplier, UnitOfMeasure, Warehouse, YarnLot, YarnMaster
from app.models.opening import OpeningBatchStatus, OpeningBatchType
from app.models.opening_custody import (
    OpeningCustodyKind,
    OpeningCustodyLine,
    OpeningCustodyRegistration,
    OpeningCustodyType,
)
from app.models.opening_stock import OpeningLineValidation
from app.services import inventory
from app.services import opening as batch_svc
from app.services.audit import record_audit
from app.services.jobwork_custody import get_or_create_custody_warehouse, require_job_worker
from app.services.manufacturing import roll_genealogy, roll_identity

MZERO = quantize(0, MONEY_SCALE)
QZERO = quantize(0, QTY_SCALE)
_OPENING_EVENT = "OPENING"
_SOURCE_MODULE = "opening_custody"
_V = OpeningLineValidation
# Batch types that carry custody lines (VENDOR_CUSTODY and WIP both mean
# vendor-held in P4; in-house WIP is out of scope and rejected at validation).
_CUSTODY_BATCH_TYPES = {OpeningBatchType.VENDOR_CUSTODY.value, OpeningBatchType.WIP.value}
_PROCESSES = {p.value for p in JobWorkProcess}
_ROLL_STREAM = {"GREIGE", "DYED", "FINISHED"}
_KIND = {"GREIGE": RollKind.GREIGE, "DYED": RollKind.DYED, "FINISHED": RollKind.FINISHED}


class OpeningCustodyError(ValueError):
    """An invalid opening-custody request."""


class OpeningCustodyStateError(OpeningCustodyError):
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
    record_audit(db, action=action, entity="opening_custody", entity_id=str(obj.id),
                 actor_id=actor, detail=detail)


# --------------------------- reference resolvers ----------------------------

def _supplier_by_code(db, company_id, code):
    code = _clean(code, 20)
    if not code:
        return None
    return db.execute(select(Supplier).where(
        Supplier.company_id == company_id, Supplier.code == code)).scalars().first()


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


def _yarn_by_code(db, company_id, code):
    code = _clean(code, 30)
    if not code:
        return None
    return db.execute(select(YarnMaster).where(
        YarnMaster.company_id == company_id, YarnMaster.code == code)).scalars().first()


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


def _roll_by_legacy(db, company_id, legacy):
    legacy = _clean(legacy, 80)
    if not legacy:
        return None
    return db.execute(select(FabricRoll).where(
        FabricRoll.company_id == company_id, FabricRoll.legacy_label == legacy)).scalars().first()


def _lot_by_number(db, company_id, internal_lot_no):
    internal_lot_no = _clean(internal_lot_no, 40)
    if not internal_lot_no:
        return None
    return db.execute(select(YarnLot).where(
        YarnLot.company_id == company_id, YarnLot.internal_lot_no == internal_lot_no)).scalars().first()


def _vendor_custody_wh(db, company_id, vendor_id):
    """The vendor's existing custody warehouse (or None); never creates one."""
    return db.execute(select(Warehouse).where(
        Warehouse.company_id == company_id, Warehouse.vendor_id == vendor_id)).scalars().first()


def _fabric_qty(db, company_id, roll_id):
    bal = inventory.balance(db, company_id=company_id, item_type=ItemType.FABRIC, item_id=roll_id)
    return _as_qty(bal["qty_on_hand"]) or QZERO


def _yarn_qty_at(db, company_id, yarn_id, lot_id, warehouse_id):
    bal = inventory.balance(db, company_id=company_id, item_type=ItemType.YARN,
                            item_id=yarn_id, lot_id=lot_id, warehouse_id=warehouse_id)
    return _as_qty(bal["qty_on_hand"]) or QZERO


# ------------------------- build line from a raw row ------------------------

def _build_line(db, batch, row) -> OpeningCustodyLine:
    errors: list[dict] = []
    line = OpeningCustodyLine(
        company_id=batch.company_id, batch_id=batch.id, row_number=row.get("_row"),
        effective_date=_as_date(row.get("effective_date")) or batch.effective_date,
        source_jobwork_ref=_clean(row.get("jobwork_ref"), 60),
        source_document=_clean(row.get("source_document"), 120),
        source_reference=_clean(row.get("source_reference"), 200),
        valuation_source=_clean(row.get("valuation_source"), 120),
        disputed_unknown=_as_bool(row.get("disputed_unknown")),
        notes=_clean(row.get("notes"), 2000),
    )

    # line_kind / custody_type
    lk = (_clean(row.get("line_kind"), 20) or "").upper()
    if lk not in (OpeningCustodyKind.VENDOR_CUSTODY.value, OpeningCustodyKind.VENDOR_HELD_WIP.value):
        errors.append({"field": "line_kind",
                       "reason": "line_kind must be VENDOR_CUSTODY or VENDOR_HELD_WIP "
                                 "(in-house WIP / in-transit / awaiting-acceptance are out of scope)"})
        lk = None
    line.line_kind = lk or OpeningCustodyKind.VENDOR_CUSTODY.value
    ct = (_clean(row.get("custody_type"), 12) or "").upper()
    if ct not in (OpeningCustodyType.YARN.value, OpeningCustodyType.FABRIC_ROLL.value):
        errors.append({"field": "custody_type",
                       "reason": "custody_type must be YARN or FABRIC_ROLL "
                                 "(aggregate/non-roll WIP is out of scope)"})
        ct = None
    line.custody_type = ct or OpeningCustodyType.YARN.value

    # vendor (must be an authorised job worker in this company)
    vendor = _supplier_by_code(db, batch.company_id, row.get("vendor_code"))
    if vendor is None:
        errors.append({"field": "vendor_code", "reason": "unknown vendor for this company"})
    elif not vendor.is_job_worker:
        errors.append({"field": "vendor_code",
                       "reason": f"supplier '{vendor.code}' is not authorised as a job worker"})
    else:
        line.job_worker_id = vendor.id

    # prior vendor (vendor-to-vendor provenance; optional)
    pv_code = _clean(row.get("prior_vendor_code"), 20)
    if pv_code:
        pv = _supplier_by_code(db, batch.company_id, pv_code)
        if pv is None:
            errors.append({"field": "prior_vendor_code", "reason": "unknown prior vendor"})
        else:
            line.prior_vendor_id = pv.id

    # process
    proc = (_clean(row.get("process"), 12) or "").upper()
    if proc and proc not in _PROCESSES:
        errors.append({"field": "process", "reason": f"process must be one of {sorted(_PROCESSES)}"})
    line.process = proc or JobWorkProcess.OTHER.value

    # factory
    fac_code = _clean(row.get("factory_code"), 20)
    factory_id = None
    if fac_code:
        fac = db.execute(select(Branch).where(
            Branch.company_id == batch.company_id, Branch.code == fac_code)).scalars().first()
        if fac is None:
            errors.append({"field": "factory_code", "reason": "unknown factory for this company"})
        else:
            factory_id = fac.id
    if factory_id is None:
        factory_id = batch.factory_id
    if batch.factory_id is not None and factory_id is not None and factory_id != batch.factory_id:
        errors.append({"field": "factory_code", "reason": "line factory differs from the batch factory"})
    line.factory_id = factory_id

    _resolve_numeric(db, row, line, errors)

    if line.custody_type == OpeningCustodyType.FABRIC_ROLL.value:
        _resolve_roll(db, batch, row, line, errors)
    else:
        _resolve_yarn(db, batch, row, line, errors)

    _finalize(line, errors)
    return line


def _resolve_numeric(db, row, line, errors):
    qty = _as_qty(row.get("net_weight_kg")) or _as_qty(row.get("quantity"))
    if qty is None:
        errors.append({"field": "net_weight_kg", "reason": "net weight / quantity is required"})
    elif qty <= QZERO:
        errors.append({"field": "net_weight_kg", "reason": "quantity must be positive"})
    else:
        line.net_weight_kg = qty
        line.quantity = qty
    line.length_m = _as_qty(row.get("length_m"))
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
    # Valuation must be explicit; disputed/unknown rows are quarantined.
    if line.disputed_unknown:
        errors.append({"field": "disputed_unknown",
                       "reason": "valuation is flagged disputed/unknown; resolve before approval"})
    if unit_cost is None and total_value is None:
        errors.append({"field": "total_value", "reason": "unit cost or total value is required"})
    if not line.valuation_source:
        errors.append({"field": "valuation_source", "reason": "valuation_source is required"})
    q = line.net_weight_kg
    if q and q > QZERO:
        if total_value is None and unit_cost is not None:
            total_value = quantize(q * unit_cost, MONEY_SCALE)
        if unit_cost is None and total_value is not None:
            unit_cost = quantize(total_value / q, MONEY_SCALE)
    line.unit_cost = unit_cost if unit_cost is not None else MZERO
    line.total_value = total_value if total_value is not None else MZERO


def _resolve_roll(db, batch, row, line, errors):
    line.erp_roll_no = _clean(row.get("erp_roll_no"), 40)
    line.legacy_label = _clean(row.get("legacy_label"), 80)
    line.roll_kind = (_clean(row.get("roll_kind"), 10) or "").upper() or None
    line.origin_stream = (_clean(row.get("origin_stream"), 10) or "").upper() or None
    line.structure = _clean(row.get("fabric_sku_or_structure"), 120)
    line.gsm = _as_qty(row.get("gsm"))
    line.width_cm = _as_qty(row.get("width_cm"))
    line.colour = _clean(row.get("colour"), 60)
    line.dye_lot = _clean(row.get("dye_lot"), 40)
    if line.structure:
        ft = _fabric_by_code(db, batch.company_id, line.structure)
        if ft is not None:
            line.fabric_type_id = ft.id
    sh = _shade_by_code(db, batch.company_id, row.get("shade_code"))
    if row.get("shade_code") and sh is None:
        errors.append({"field": "shade_code", "reason": "unknown shade code"})
    elif sh is not None:
        line.shade_id = sh.id
    if line.roll_kind and line.roll_kind not in _ROLL_STREAM:
        errors.append({"field": "roll_kind", "reason": "roll_kind must be GREIGE, DYED or FINISHED"})

    if line.erp_roll_no:
        line.registration_mode = OpeningCustodyRegistration.REFERENCE_EXISTING.value
        roll = _roll_by_no(db, batch.company_id, line.erp_roll_no)
        if roll is None:
            errors.append({"field": "erp_roll_no", "reason": "ERP Roll ID not found in this company"})
        else:
            line.fabric_roll_id = roll.id
            if _fabric_qty(db, batch.company_id, roll.id) > QZERO:
                errors.append({"field": "erp_roll_no",
                               "reason": "existing roll already holds stock; opening would double-count"})
    else:
        line.registration_mode = OpeningCustodyRegistration.MINT_NEW.value
        if not line.roll_kind:
            errors.append({"field": "roll_kind", "reason": "roll_kind required to mint a new roll"})
        if not line.legacy_label:
            errors.append({"field": "legacy_label",
                           "reason": "a new (unregistered) roll requires a legacy label"})
        elif _roll_by_legacy(db, batch.company_id, line.legacy_label) is not None:
            errors.append({"field": "legacy_label",
                           "reason": "legacy label already registered on an existing roll"})
        if line.roll_kind == "FINISHED" and line.origin_stream not in ("GREIGE", "DYED"):
            errors.append({"field": "origin_stream",
                           "reason": "finished roll with no ERP Roll ID must set origin_stream GREIGE or DYED"})


def _resolve_yarn(db, batch, row, line, errors):
    line.internal_lot_no = _clean(row.get("internal_lot_no"), 40)
    yarn = _yarn_by_code(db, batch.company_id, row.get("yarn_code"))
    if yarn is None:
        errors.append({"field": "yarn_code", "reason": "unknown yarn SKU for this company"})
    elif not yarn.is_active:
        errors.append({"field": "yarn_code", "reason": f"yarn SKU '{yarn.code}' is inactive"})
    else:
        line.yarn_id = yarn.id
    if not line.internal_lot_no:
        errors.append({"field": "internal_lot_no",
                       "reason": "internal_lot_no is required (ambiguous lot identity is quarantined)"})
    elif line.yarn_id is not None:
        existing = _lot_by_number(db, batch.company_id, line.internal_lot_no)
        if existing is not None:
            if existing.yarn_id != line.yarn_id:
                errors.append({"field": "internal_lot_no",
                               "reason": "internal lot number already used for a different yarn SKU"})
            else:
                line.registration_mode = OpeningCustodyRegistration.REFERENCE_EXISTING.value
                line.yarn_lot_id = existing.id
        else:
            line.registration_mode = OpeningCustodyRegistration.MINT_NEW.value


def _finalize(line, errors):
    line.dedup_key = None
    if errors:
        line.validation_status = _V.INVALID.value
        line.validation_errors = json.dumps(errors)
    else:
        line.validation_status = _V.VALID.value
        line.validation_errors = None


def _identity_key(line: OpeningCustodyLine) -> str:
    v = line.job_worker_id
    if line.custody_type == OpeningCustodyType.FABRIC_ROLL.value:
        if line.registration_mode == OpeningCustodyRegistration.REFERENCE_EXISTING.value:
            return f"CUST|{v}|ROLL|REF|{(line.erp_roll_no or '').upper()}"
        if line.legacy_label:
            return f"CUST|{v}|ROLL|LEGACY|{_norm(line.legacy_label)}"
        return f"CUST|{v}|ROLL|PV|{line.id}"
    return f"CUST|{v}|YARN|{line.yarn_id}|{(line.internal_lot_no or '').upper()}"


def _mark_invalid(line, field, reason):
    errs = json.loads(line.validation_errors) if line.validation_errors else []
    errs.append({"field": field, "reason": reason})
    line.validation_status = _V.INVALID.value
    line.validation_errors = json.dumps(errs)
    line.dedup_key = None


def _dedup_pass(db, batch):
    lines = db.execute(select(OpeningCustodyLine).where(
        OpeningCustodyLine.batch_id == batch.id)
        .order_by(OpeningCustodyLine.row_number, OpeningCustodyLine.created_at)).scalars().all()
    seen: dict[str, OpeningCustodyLine] = {}
    for line in lines:
        if line.validation_status != _V.VALID.value:
            continue
        key = _identity_key(line)
        line.dedup_key = key
        if key.split("|")[-2:-1] == ["PV"] or "|PV|" in key:
            continue
        if key in seen:
            _mark_invalid(line, "dedup", "duplicate physical item within this batch")
            continue
        seen[key] = line
    # Existing custody-stock conflict (yarn already opened at this vendor's custody).
    for line in lines:
        if line.validation_status != _V.VALID.value:
            continue
        if line.custody_type == OpeningCustodyType.YARN.value and line.yarn_lot_id and line.job_worker_id:
            cust = _vendor_custody_wh(db, batch.company_id, line.job_worker_id)
            if cust is not None and _yarn_qty_at(db, batch.company_id, line.yarn_id,
                                                 line.yarn_lot_id, cust.id) > QZERO:
                _mark_invalid(line, "internal_lot_no",
                              "yarn lot already holds stock at this vendor's custody; would double-count")


# ------------------------------- import -------------------------------------

def import_rows(db, batch, *, rows, import_file, actor=None) -> dict:
    if batch.status != OpeningBatchStatus.DRAFT.value:
        raise OpeningCustodyStateError("Opening custody lines can only be imported while the batch is DRAFT")
    if batch.batch_type not in _CUSTODY_BATCH_TYPES:
        raise OpeningCustodyError(
            f"Batch type '{batch.batch_type}' does not accept vendor-custody lines")
    built = []
    for row in rows:
        line = _build_line(db, batch, row)
        line.import_file_id = import_file.id
        line.created_by = actor
        line.updated_by = actor
        db.add(line)
        built.append(line)
    db.flush()
    _dedup_pass(db, batch)
    db.flush()
    valid = sum(1 for l in built if l.validation_status == _V.VALID.value)
    invalid = len(built) - valid
    import_file.row_count = len(built)
    import_file.valid_count = valid
    import_file.invalid_count = invalid
    db.flush()
    _audit(db, actor, "OPENING_CUSTODY_IMPORT_PARSED", import_file,
           {"company_id": str(batch.company_id), "batch_id": str(batch.id),
            "batch_number": batch.batch_number, "file_name": import_file.file_name,
            "file_hash": import_file.file_hash, "rows": len(built), "valid": valid, "invalid": invalid})
    return {"import_file_id": str(import_file.id), "rows": len(built),
            "valid": valid, "invalid": invalid, "line_ids": [str(l.id) for l in built]}


# ------------------------------ read / preview ------------------------------

def list_lines(db, company_id, batch_id, *, status=None, limit=100, offset=0):
    batch = batch_svc.get_batch(db, company_id, batch_id)
    stmt = select(OpeningCustodyLine).where(OpeningCustodyLine.batch_id == batch.id)
    if status:
        stmt = stmt.where(OpeningCustodyLine.validation_status == status)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = db.execute(stmt.order_by(OpeningCustodyLine.row_number, OpeningCustodyLine.created_at)
                      .limit(max(1, min(limit, 500))).offset(max(0, offset))).scalars().all()
    return batch, rows, total


def rejections(db, company_id, batch_id):
    batch = batch_svc.get_batch(db, company_id, batch_id)
    rows = db.execute(select(OpeningCustodyLine).where(
        OpeningCustodyLine.batch_id == batch.id,
        OpeningCustodyLine.validation_status == _V.INVALID.value)
        .order_by(OpeningCustodyLine.row_number)).scalars().all()
    return batch, [{"line_id": str(l.id), "row_number": l.row_number,
                    "custody_type": l.custody_type, "erp_roll_no": l.erp_roll_no,
                    "legacy_label": l.legacy_label, "internal_lot_no": l.internal_lot_no,
                    "errors": json.loads(l.validation_errors) if l.validation_errors else []}
                   for l in rows]


def reconciliation_preview(db, company_id, batch_id) -> dict:
    batch = batch_svc.get_batch(db, company_id, batch_id)
    lines = db.execute(select(OpeningCustodyLine).where(
        OpeningCustodyLine.batch_id == batch.id,
        OpeningCustodyLine.validation_status.in_([_V.VALID.value, _V.COMMITTED.value]))
        .order_by(OpeningCustodyLine.row_number)).scalars().all()
    by_vendor: dict = {}
    by_process: dict = {}
    total_qty = QZERO
    total_value = MZERO
    items = []
    for line in lines:
        q = _as_qty(line.net_weight_kg) or QZERO
        v = _as_money(line.total_value) or MZERO
        total_qty = quantize(total_qty + q, QTY_SCALE)
        total_value = quantize(total_value + v, MONEY_SCALE)
        vk = str(line.job_worker_id)
        by_vendor.setdefault(vk, {"qty": QZERO, "value": MZERO, "lines": 0})
        by_vendor[vk]["qty"] = quantize(by_vendor[vk]["qty"] + q, QTY_SCALE)
        by_vendor[vk]["value"] = quantize(by_vendor[vk]["value"] + v, MONEY_SCALE)
        by_vendor[vk]["lines"] += 1
        pk = line.process or "OTHER"
        by_process.setdefault(pk, {"qty": QZERO, "value": MZERO, "lines": 0})
        by_process[pk]["qty"] = quantize(by_process[pk]["qty"] + q, QTY_SCALE)
        by_process[pk]["value"] = quantize(by_process[pk]["value"] + v, MONEY_SCALE)
        by_process[pk]["lines"] += 1
        items.append({
            "line_id": str(line.id), "line_kind": line.line_kind, "custody_type": line.custody_type,
            "registration_mode": line.registration_mode, "job_worker_id": vk, "process": pk,
            "erp_roll_no": line.erp_roll_no, "legacy_label": line.legacy_label,
            "yarn_id": str(line.yarn_id) if line.yarn_id else None,
            "internal_lot_no": line.internal_lot_no, "proposed_qty": str(q),
            "proposed_value": str(v), "status": line.validation_status,
            "prior_vendor_id": str(line.prior_vendor_id) if line.prior_vendor_id else None,
        })
    return {"batch_id": str(batch.id), "batch_status": batch.status, "line_count": len(items),
            "proposed_total_qty": str(total_qty), "proposed_total_value": str(total_value),
            "by_vendor": {k: {"qty": str(x["qty"]), "value": str(x["value"]), "lines": x["lines"]}
                          for k, x in by_vendor.items()},
            "by_process": {k: {"qty": str(x["qty"]), "value": str(x["value"]), "lines": x["lines"]}
                           for k, x in by_process.items()},
            "origin": "OPENING",
            "accounting_posting": "BLOCKED_PENDING_CA_APPROVAL", "items": items}


# ------------------------- submit / approve / commit ------------------------

def revalidate_batch(db, company_id, batch_id) -> dict:
    batch = batch_svc.get_batch(db, company_id, batch_id)
    if batch.status != OpeningBatchStatus.DRAFT.value:
        raise OpeningCustodyStateError("Only a DRAFT batch can be revalidated")
    _revalidate(db, batch)
    total = db.execute(select(func.count()).select_from(OpeningCustodyLine).where(
        OpeningCustodyLine.batch_id == batch.id)).scalar()
    valid = db.execute(select(func.count()).select_from(OpeningCustodyLine).where(
        OpeningCustodyLine.batch_id == batch.id,
        OpeningCustodyLine.validation_status == _V.VALID.value)).scalar()
    return {"batch_id": str(batch.id), "total": total, "valid": valid, "invalid": total - valid}


def _revalidate(db, batch):
    lines = db.execute(select(OpeningCustodyLine).where(
        OpeningCustodyLine.batch_id == batch.id)).scalars().all()
    for line in lines:
        if line.validation_status == _V.COMMITTED.value:
            continue
        errors: list[dict] = []
        if line.job_worker_id is None:
            errors.append({"field": "vendor", "reason": "vendor unresolved"})
        else:
            v = db.get(Supplier, line.job_worker_id)
            if v is None or v.company_id != batch.company_id or not v.is_job_worker:
                errors.append({"field": "vendor", "reason": "vendor not a job worker in company"})
        if (_as_qty(line.net_weight_kg) or QZERO) <= QZERO:
            errors.append({"field": "net_weight_kg", "reason": "quantity must be positive"})
        if line.disputed_unknown or not line.valuation_source:
            errors.append({"field": "valuation", "reason": "valuation disputed/unknown or source missing"})
        if line.custody_type == OpeningCustodyType.FABRIC_ROLL.value:
            if line.registration_mode == OpeningCustodyRegistration.REFERENCE_EXISTING.value:
                roll = db.get(FabricRoll, line.fabric_roll_id) if line.fabric_roll_id else None
                if roll is None or roll.company_id != batch.company_id:
                    errors.append({"field": "erp_roll_no", "reason": "referenced roll missing"})
                elif _fabric_qty(db, batch.company_id, roll.id) > QZERO:
                    errors.append({"field": "erp_roll_no", "reason": "referenced roll already holds stock"})
            else:
                if line.legacy_label and _roll_by_legacy(db, batch.company_id, line.legacy_label) is not None:
                    errors.append({"field": "legacy_label", "reason": "legacy label already registered"})
                if line.roll_kind == "FINISHED" and line.origin_stream not in ("GREIGE", "DYED"):
                    errors.append({"field": "origin_stream", "reason": "finished origin stream unresolved"})
        else:
            if line.yarn_id is None or not line.internal_lot_no:
                errors.append({"field": "yarn", "reason": "yarn/lot identity unresolved"})
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
    rows = db.execute(select(OpeningCustodyLine).where(
        OpeningCustodyLine.batch_id == batch.id)).scalars().all()
    if not rows:
        raise OpeningCustodyStateError("The batch has no opening custody lines")
    bad = [l for l in rows if l.validation_status not in (_V.VALID.value, _V.COMMITTED.value)]
    if bad:
        raise OpeningCustodyStateError(
            f"{len(bad)} custody line(s) are invalid or unvalidated; correct them before proceeding")


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
    lines = db.execute(select(OpeningCustodyLine).where(
        OpeningCustodyLine.batch_id == batch.id).order_by(OpeningCustodyLine.row_number)).scalars().all()
    committed = 0
    for line in lines:
        if line.validation_status == _V.COMMITTED.value:
            continue
        if line.validation_status != _V.VALID.value:
            raise OpeningCustodyError(f"Cannot approve: custody line {line.row_number or line.id} invalid")
        _commit_line(db, batch, line, actor=approved_by)
        committed += 1
    db.flush()
    _audit(db, approved_by, "OPENING_CUSTODY_COMMIT", batch,
           {"company_id": str(batch.company_id), "batch_number": batch.batch_number,
            "lines_committed": committed})
    return batch, committed


def _mint_roll_no(db, batch, line) -> str:
    when = line.effective_date or batch.effective_date or date.today()
    if line.roll_kind == "GREIGE":
        return roll_identity.new_greige_roll_id(db, company_id=batch.company_id, when=when)
    if line.roll_kind == "DYED":
        return roll_identity.new_dyed_roll_id(db, company_id=batch.company_id, when=when)
    if line.origin_stream == "GREIGE":
        return roll_identity.new_greige_roll_id(db, company_id=batch.company_id, when=when)
    if line.origin_stream == "DYED":
        return roll_identity.new_dyed_roll_id(db, company_id=batch.company_id, when=when)
    raise OpeningCustodyError("Finished-roll origin stream unresolved; cannot mint Roll ID")


def _commit_line(db, batch, line: OpeningCustodyLine, *, actor):
    qty = _as_qty(line.net_weight_kg)
    if qty is None or qty <= QZERO:
        raise OpeningCustodyError("Cannot commit a custody line with non-positive quantity")
    value = _as_money(line.total_value) or MZERO

    # Resolve the vendor's custody warehouse (company-owned storage at the vendor).
    vendor = require_job_worker(db, batch.company_id, line.job_worker_id)
    custody = get_or_create_custody_warehouse(db, batch.company_id, vendor, created_by=actor)
    line.custody_warehouse_id = custody.id

    if line.custody_type == OpeningCustodyType.FABRIC_ROLL.value:
        item_type = ItemType.FABRIC
        if line.registration_mode == OpeningCustodyRegistration.REFERENCE_EXISTING.value:
            roll = db.get(FabricRoll, line.fabric_roll_id)
            if roll is None or roll.company_id != batch.company_id:
                raise OpeningCustodyError("Referenced roll not found at commit")
            if _fabric_qty(db, batch.company_id, roll.id) > QZERO:
                raise OpeningCustodyError("Referenced roll already holds stock; would double-count")
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
                length_m=line.length_m, weight_kg=qty, warehouse_id=custody.id,
                quality_status="OK", status="ACTIVE", created_by=actor, updated_by=actor)
            db.add(roll)
            db.flush()
            new_roll = True
            line.fabric_roll_id = roll.id
        # A roll is a single physical object — its authoritative location is the
        # vendor custody warehouse (so it is protected from sale/transfer).
        roll.warehouse_id = custody.id
        item_id = roll.id
        layer = inventory.receive(
            db, company_id=batch.company_id, item_type=ItemType.FABRIC, item_id=roll.id,
            qty=qty, unit_cost=line.unit_cost, value=value, warehouse_id=custody.id,
            event_type=_OPENING_EVENT, source_module=_SOURCE_MODULE, source_id=str(line.id))
        if new_roll:
            roll_genealogy.record_edge(
                db, company_id=batch.company_id, process_type=roll_genealogy.IMPORT,
                event_type=roll_genealogy.PRODUCE, output_roll_id=roll.id, source_roll_id=None,
                factory_id=line.factory_id, event_group_id=uuid.uuid4(),
                production_ref=batch.batch_number, uom=roll_genealogy.UOM_KG,
                output_weight_kg=qty, output_qty=qty, event_datetime=_now(), recorded_by=actor)
        fabric_roll_id = roll.id
    else:
        item_type = ItemType.YARN
        lot = _lot_by_number(db, batch.company_id, line.internal_lot_no)
        if lot is not None:
            if lot.yarn_id != line.yarn_id:
                raise OpeningCustodyError("Lot number belongs to a different yarn SKU")
            if _yarn_qty_at(db, batch.company_id, line.yarn_id, lot.id, custody.id) > QZERO:
                raise OpeningCustodyError("Yarn lot already holds stock at this custody; would double-count")
        else:
            lot = YarnLot(company_id=batch.company_id, yarn_id=line.yarn_id,
                          internal_lot_no=line.internal_lot_no, warehouse_id=custody.id,
                          unit_cost=line.unit_cost, created_by=actor, updated_by=actor)
            db.add(lot)
            db.flush()
        line.yarn_lot_id = lot.id
        item_id = line.yarn_id
        fabric_roll_id = None
        layer = inventory.receive(
            db, company_id=batch.company_id, item_type=ItemType.YARN, item_id=line.yarn_id,
            qty=qty, unit_cost=line.unit_cost, value=value, lot_id=lot.id, warehouse_id=custody.id,
            event_type=_OPENING_EVENT, source_module=_SOURCE_MODULE, source_id=str(line.id))

    # Opening-origin custody record so the position shows in vendor-wise
    # outstanding reconciliation exactly once. NOT a new physical issue:
    # source_warehouse_id is NULL and no transfer/receipt is created.
    issue = JobWorkIssue(
        company_id=batch.company_id, factory_id=line.factory_id, job_work_id=None,
        job_worker_id=vendor.id, process=(line.process or JobWorkProcess.OTHER.value),
        source_warehouse_id=None, custody_warehouse_id=custody.id,
        item_type=item_type.value, item_id=item_id, fabric_roll_id=fabric_roll_id,
        qty=qty, uom="KG", weight_kg=qty, length_m=line.length_m,
        issue_date=line.effective_date or batch.effective_date or date.today(),
        challan_no=f"OPEN-{batch.batch_number}-{line.row_number or str(line.id)[:8]}"[:40],
        status=JobWorkIssueStatus.OUTSTANDING,
        posting_reference=f"OPENING:{batch.batch_number}:{line.row_number or ''}",
        created_by=actor)
    db.add(issue)
    db.flush()

    line.stock_layer_id = layer.id
    line.job_work_issue_id = issue.id
    line.committed_at = _now()
    line.validation_status = _V.COMMITTED.value
    line.updated_by = actor
    _audit(db, actor, "OPENING_CUSTODY_LINE_COMMITTED", line,
           {"company_id": str(batch.company_id), "batch_number": batch.batch_number,
            "custody_type": line.custody_type, "line_kind": line.line_kind,
            "job_worker_id": str(vendor.id), "custody_warehouse_id": str(custody.id),
            "item_id": str(item_id), "fabric_roll_id": str(fabric_roll_id) if fabric_roll_id else None,
            "yarn_lot_id": str(line.yarn_lot_id) if line.yarn_lot_id else None,
            "qty": str(qty), "value": str(value), "stock_layer_id": str(layer.id),
            "job_work_issue_id": str(issue.id), "event_type": _OPENING_EVENT})
