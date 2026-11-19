"""Opening-stock services (Stage 6F-P2).

Yarn Store and Trading Fabric opening stock, built on the P1 ``OpeningBatch``
lifecycle and the existing FIFO inventory engine. Responsibilities:

* Turn parsed workbook rows into typed ``OpeningStockLine`` rows under a DRAFT
  batch, resolving master codes to ids and running every mandatory validation.
* Deduplicate within the file, within the batch and against existing inventory.
* Enforce the legacy yarn-lot policy (existing lot / new lot from a legacy lot
  number / an explicitly authorised legacy placeholder) without ever fabricating
  a supplier or supplier lot.
* Compose the P1 submit / approve transitions (maker-checker, company / factory
  scope) and, at approval, create the opening FIFO stock layers via
  ``inventory.receive`` — with a distinct ``OPENING`` event type and NO
  accounting voucher (financial posting stays CA-gated for a later phase).
* Produce a reconciliation preview (proposed vs existing on-hand) before approval.

Accounting safety: this module never calls the posting engine and never
references a GL account. It creates inventory sub-ledger rows only.
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
from app.models.masters import Supplier, UnitOfMeasure, Warehouse, YarnLot, YarnMaster
from app.models.opening import OpeningBatchStatus, OpeningBatchType
from app.models.opening_stock import (
    OpeningImportFile,
    OpeningLineType,
    OpeningLineValidation,
    OpeningLotPolicy,
    OpeningStockLine,
)
from app.models.trading import TradingFabricItem
from app.services import inventory
from app.services import opening as batch_svc
from app.services.audit import record_audit

MZERO = quantize(0, MONEY_SCALE)
QZERO = quantize(0, QTY_SCALE)

_OPENING_EVENT = "OPENING"

# Batch type -> the opening line type it may carry.
_BATCH_LINE_TYPE = {
    OpeningBatchType.YARN.value: OpeningLineType.YARN.value,
    OpeningBatchType.TRADING_FABRIC.value: OpeningLineType.TRADING_FABRIC.value,
}


class OpeningStockError(ValueError):
    """An invalid opening-stock request."""


class OpeningStockStateError(OpeningStockError):
    """The batch is in the wrong state for the requested operation."""


# ------------------------------- helpers ------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_qty(raw) -> Decimal | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return quantize(Decimal(str(raw).strip()), QTY_SCALE)
    except (InvalidOperation, ValueError):
        return None


def _as_money(raw) -> Decimal | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return quantize(Decimal(str(raw).strip()), MONEY_SCALE)
    except (InvalidOperation, ValueError):
        return None


def _as_date(raw) -> date | None:
    if raw is None:
        return None
    if isinstance(raw, date):
        return raw
    s = str(raw).strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _as_bool(raw) -> bool:
    return str(raw).strip().lower() in ("1", "true", "yes", "y") if raw is not None else False


def _clean(s, maxlen):
    if s is None:
        return None
    t = str(s).strip()
    return t[:maxlen] if t else None


def _audit(db, actor, action, line_or_batch, detail):
    record_audit(db, action=action, entity="opening_stock",
                 entity_id=str(line_or_batch.id), actor_id=actor, detail=detail)


# --------------------------- reference resolvers ----------------------------

def _yarn_by_code(db, company_id, code):
    if not code:
        return None
    return db.execute(select(YarnMaster).where(
        YarnMaster.company_id == company_id, YarnMaster.code == code)).scalars().first()


def _trading_by_code(db, company_id, code):
    if not code:
        return None
    return db.execute(select(TradingFabricItem).where(
        TradingFabricItem.company_id == company_id, TradingFabricItem.code == code)).scalars().first()


def _warehouse_by_code(db, company_id, code):
    if not code:
        return None
    return db.execute(select(Warehouse).where(
        Warehouse.company_id == company_id, Warehouse.code == code)).scalars().first()


def _factory_by_code(db, company_id, code):
    if not code:
        return None
    return db.execute(select(Branch).where(
        Branch.company_id == company_id, Branch.code == code)).scalars().first()


def _supplier_by_code(db, company_id, code):
    if not code:
        return None
    return db.execute(select(Supplier).where(
        Supplier.company_id == company_id, Supplier.code == code)).scalars().first()


def _uom_by_code(db, code):
    if not code:
        return None
    return db.execute(select(UnitOfMeasure).where(UnitOfMeasure.code == code)).scalars().first()


def _lot_by_company_number(db, company_id, internal_lot_no):
    """Look up a lot by its company-scoped identity (company_id, internal_lot_no).

    This matches both the ``uq_yarn_lot_company_internal_no`` unique index and the
    normal receipt path's idempotency key (``YARNLOT:<internal_lot_no>`` per
    company), so the internal lot number is treated as unique within a company
    across all yarn SKUs.
    """
    if not internal_lot_no:
        return None
    return db.execute(select(YarnLot).where(
        YarnLot.company_id == company_id,
        YarnLot.internal_lot_no == internal_lot_no)).scalars().first()


# ------------------------- build line from a raw row ------------------------

def _build_yarn_line(db, batch, row) -> OpeningStockLine:
    errors: list[dict] = []
    line = OpeningStockLine(
        company_id=batch.company_id, batch_id=batch.id, line_type=OpeningLineType.YARN.value,
        item_type=ItemType.YARN.value, row_number=row.get("_row"),
        effective_date=_as_date(row.get("effective_date")) or batch.effective_date,
        source_document=_clean(row.get("source_document"), 120),
        source_reference=_clean(row.get("source_reference"), 200),
        legacy_unknown=_as_bool(row.get("legacy_unknown")),
        internal_lot_no=_clean(row.get("internal_lot_no"), 40),
        supplier_lot_no=_clean(row.get("supplier_lot_no"), 40),
    )

    yarn = _yarn_by_code(db, batch.company_id, row.get("yarn_code"))
    if yarn is None:
        errors.append({"field": "yarn_code", "reason": "unknown yarn SKU for this company"})
    elif not yarn.is_active:
        errors.append({"field": "yarn_code", "reason": f"yarn SKU '{yarn.code}' is inactive"})
    else:
        line.item_id = yarn.id

    _resolve_warehouse(db, batch, row, line, errors, WarehouseCategory.YARN_STORE.value)
    _resolve_common_numeric(db, row, line, errors)

    # Supplier (only when a code is supplied; never fabricated).
    sup_code = _clean(row.get("supplier_code"), 20)
    if sup_code:
        sup = _supplier_by_code(db, batch.company_id, sup_code)
        if sup is None:
            errors.append({"field": "supplier_code", "reason": "unknown supplier for this company"})
        else:
            line.supplier_id = sup.id

    # Legacy-lot policy resolution. The lot number is the company-scoped identity,
    # so a number already used by a DIFFERENT yarn SKU in the company is a conflict
    # (consistent with the unique index), never a silent new lot.
    if line.item_id is not None:
        if line.internal_lot_no:
            existing = _lot_by_company_number(db, batch.company_id, line.internal_lot_no)
            if existing is not None:
                if existing.yarn_id != line.item_id:
                    errors.append({"field": "internal_lot_no",
                                   "reason": "internal lot number already used for a different "
                                             "yarn SKU in this company"})
                else:
                    line.yarn_lot_id = existing.id
                    line.lot_policy = OpeningLotPolicy.EXISTING_LOT.value
            else:
                line.lot_policy = OpeningLotPolicy.NEW_LOT.value
        elif line.legacy_unknown:
            line.lot_policy = OpeningLotPolicy.LEGACY_PLACEHOLDER.value
        else:
            errors.append({"field": "internal_lot_no",
                           "reason": "lot identity required; provide internal_lot_no or set "
                                     "legacy_unknown=true for an authorised legacy placeholder"})
    _finalize(line, errors)
    return line


def _build_trading_line(db, batch, row) -> OpeningStockLine:
    errors: list[dict] = []
    line = OpeningStockLine(
        company_id=batch.company_id, batch_id=batch.id,
        line_type=OpeningLineType.TRADING_FABRIC.value, item_type=ItemType.TRADING_FABRIC.value,
        row_number=row.get("_row"),
        effective_date=_as_date(row.get("effective_date")) or batch.effective_date,
        source_document=_clean(row.get("source_document"), 120),
        source_reference=_clean(row.get("source_reference"), 200),
        lot_policy=OpeningLotPolicy.NONE.value,
    )
    item = _trading_by_code(db, batch.company_id, row.get("trading_sku_code"))
    if item is None:
        errors.append({"field": "trading_sku_code", "reason": "unknown trading SKU for this company"})
    else:
        line.item_id = item.id

    _resolve_warehouse(db, batch, row, line, errors, WarehouseCategory.TRADING.value)
    # Trading weight column is named differently in the template.
    line.weight_kg = _as_qty(row.get("total_weight_kg"))
    _resolve_common_numeric(db, row, line, errors, weight_from_row=False)
    _finalize(line, errors)
    return line


def _resolve_warehouse(db, batch, row, line, errors, required_category):
    wh = _warehouse_by_code(db, batch.company_id, row.get("warehouse_code"))
    if wh is None:
        errors.append({"field": "warehouse_code", "reason": "unknown warehouse for this company"})
        return
    if wh.category != required_category:
        errors.append({"field": "warehouse_code",
                       "reason": f"warehouse '{wh.code}' category must be exactly {required_category}"})
        return
    line.warehouse_id = wh.id

    # Factory: explicit column, else the batch factory. Must be in the company and
    # consistent with the batch factory and the warehouse's own factory.
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
        errors.append({"field": "factory_code",
                       "reason": "line factory differs from the batch factory"})
    if factory_id is not None and wh.branch_id is not None and wh.branch_id != factory_id:
        errors.append({"field": "warehouse_code",
                       "reason": f"warehouse '{wh.code}' belongs to a different factory"})
    line.factory_id = factory_id


def _resolve_common_numeric(db, row, line, errors, *, weight_from_row=True):
    qty = _as_qty(row.get("quantity"))
    if qty is None:
        errors.append({"field": "quantity", "reason": "quantity is required and must be numeric"})
    elif qty <= QZERO:
        errors.append({"field": "quantity", "reason": "quantity must be positive"})
    else:
        line.quantity = qty

    uom = _uom_by_code(db, _clean(row.get("uom_code"), 10))
    if row.get("uom_code") and uom is None:
        errors.append({"field": "uom_code", "reason": "unknown unit of measure"})
    elif uom is not None:
        line.uom_id = uom.id

    if weight_from_row:
        line.weight_kg = _as_qty(row.get("weight_kg"))

    unit_cost = _as_money(row.get("unit_cost"))
    total_value = _as_money(row.get("total_value"))
    if unit_cost is not None and unit_cost < MZERO:
        errors.append({"field": "unit_cost", "reason": "unit cost cannot be negative"})
    if total_value is not None and total_value < MZERO:
        errors.append({"field": "total_value", "reason": "total value cannot be negative"})
    if unit_cost is None and total_value is None:
        errors.append({"field": "unit_cost", "reason": "unit cost or total value is required"})
    # Derive the missing side so the FIFO layer value is pinned exactly.
    if line.quantity and line.quantity > QZERO:
        if total_value is None and unit_cost is not None:
            total_value = quantize(line.quantity * unit_cost, MONEY_SCALE)
        if unit_cost is None and total_value is not None:
            unit_cost = quantize(total_value / line.quantity, MONEY_SCALE)
    line.unit_cost = unit_cost if unit_cost is not None else MZERO
    line.total_value = total_value if total_value is not None else MZERO


def _finalize(line, errors):
    # dedup_key is assigned only by _dedup_and_inventory_pass (after a flush has
    # given placeholder lines an id, and so that duplicate valid rows never hit
    # the unique index before the pass nulls the losers).
    line.dedup_key = None
    if errors:
        line.validation_status = OpeningLineValidation.INVALID.value
        line.validation_errors = json.dumps(errors)
    else:
        line.validation_status = OpeningLineValidation.VALID.value
        line.validation_errors = None


def _identity_key(line: OpeningStockLine) -> str:
    if line.line_type == OpeningLineType.YARN.value:
        if line.lot_policy == OpeningLotPolicy.LEGACY_PLACEHOLDER.value:
            # Each authorised placeholder is unique; never a duplicate of another.
            return f"YARN|LEGACY|{line.id}"
        return f"YARN|{line.warehouse_id}|{(line.internal_lot_no or '').upper()}"
    return f"TRADE|{line.warehouse_id}|{line.item_id}"


# --------------------- dedup + existing-inventory pass -----------------------

def _dedup_and_inventory_pass(db: Session, batch) -> None:
    """Second validation pass over a batch: mark duplicate valid lines and lines
    whose identity already holds stock as INVALID. Operates on currently-VALID,
    not-yet-committed lines only."""
    lines = db.execute(select(OpeningStockLine).where(
        OpeningStockLine.batch_id == batch.id)
        .order_by(OpeningStockLine.row_number, OpeningStockLine.created_at)).scalars().all()

    seen: dict[str, OpeningStockLine] = {}
    for line in lines:
        if line.validation_status != OpeningLineValidation.VALID.value:
            continue
        key = _identity_key(line)
        line.dedup_key = key
        if line.lot_policy == OpeningLotPolicy.LEGACY_PLACEHOLDER.value:
            continue  # placeholders are inherently unique
        if key in seen:
            _mark_invalid(line, "dedup", "duplicate opening row within this batch")
            continue
        seen[key] = line

    # Existing-inventory conflict: an EXISTING_LOT yarn line whose lot already has
    # stock, or a trading SKU/warehouse that already carries stock.
    for line in lines:
        if line.validation_status != OpeningLineValidation.VALID.value:
            continue
        if _identity_has_stock(db, line):
            _mark_invalid(line, "duplicate",
                          "stock already exists for this item/lot/warehouse; opening would double-count")


def _identity_has_stock(db, line) -> bool:
    if line.line_type == OpeningLineType.YARN.value:
        if line.lot_policy != OpeningLotPolicy.EXISTING_LOT.value or line.yarn_lot_id is None:
            return False  # a new / placeholder lot cannot have prior stock
        bal = inventory.balance(db, company_id=line.company_id, item_type=ItemType.YARN,
                                item_id=line.item_id, lot_id=line.yarn_lot_id)
        return _as_qty(bal["qty_on_hand"]) and _as_qty(bal["qty_on_hand"]) > QZERO
    # Trading aggregate: same SKU + warehouse already holding stock.
    bal = inventory.balance(db, company_id=line.company_id, item_type=ItemType.TRADING_FABRIC,
                            item_id=line.item_id, warehouse_id=line.warehouse_id)
    return _as_qty(bal["qty_on_hand"]) and _as_qty(bal["qty_on_hand"]) > QZERO


def _mark_invalid(line, field, reason):
    errs = json.loads(line.validation_errors) if line.validation_errors else []
    errs.append({"field": field, "reason": reason})
    line.validation_status = OpeningLineValidation.INVALID.value
    line.validation_errors = json.dumps(errs)
    line.dedup_key = None


# ------------------------------- import -------------------------------------

def import_rows(db: Session, batch, *, line_type: str, rows: list[dict],
                import_file: OpeningImportFile, actor: uuid.UUID | None = None) -> dict:
    """Build + validate lines from parsed rows under a DRAFT batch. Returns a
    summary with counts. Does not commit (the route owns the transaction)."""
    if batch.status != OpeningBatchStatus.DRAFT.value:
        raise OpeningStockStateError("Opening lines can only be imported while the batch is DRAFT")
    expected = _BATCH_LINE_TYPE.get(batch.batch_type)
    if expected != line_type:
        raise OpeningStockError(
            f"Batch type '{batch.batch_type}' does not accept {line_type} opening lines")

    builder = _build_yarn_line if line_type == OpeningLineType.YARN.value else _build_trading_line
    built: list[OpeningStockLine] = []
    for row in rows:
        line = builder(db, batch, row)
        line.import_file_id = import_file.id
        line.created_by = actor
        line.updated_by = actor
        db.add(line)
        built.append(line)
    db.flush()  # assign ids (placeholder identity keys need line.id); keys still NULL
    _dedup_and_inventory_pass(db, batch)
    db.flush()

    valid = sum(1 for l in built if l.validation_status == OpeningLineValidation.VALID.value)
    invalid = len(built) - valid
    import_file.row_count = len(built)
    import_file.valid_count = valid
    import_file.invalid_count = invalid
    db.flush()
    _audit(db, actor, "OPENING_IMPORT_PARSED", import_file,
           {"company_id": str(batch.company_id), "batch_id": str(batch.id),
            "batch_number": batch.batch_number, "line_type": line_type,
            "file_name": import_file.file_name, "file_hash": import_file.file_hash,
            "rows": len(built), "valid": valid, "invalid": invalid})
    return {"import_file_id": str(import_file.id), "rows": len(built),
            "valid": valid, "invalid": invalid,
            "line_ids": [str(l.id) for l in built]}


# ------------------------------ read / preview ------------------------------

def list_lines(db: Session, company_id, batch_id, *, status: str | None = None,
               line_type: str | None = None, limit: int = 100, offset: int = 0):
    batch = batch_svc.get_batch(db, company_id, batch_id)
    stmt = select(OpeningStockLine).where(OpeningStockLine.batch_id == batch.id)
    if status:
        stmt = stmt.where(OpeningStockLine.validation_status == status)
    if line_type:
        stmt = stmt.where(OpeningStockLine.line_type == line_type)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = db.execute(stmt.order_by(OpeningStockLine.row_number, OpeningStockLine.created_at)
                      .limit(max(1, min(limit, 500))).offset(max(0, offset))).scalars().all()
    return batch, rows, total


def rejections(db: Session, company_id, batch_id):
    batch = batch_svc.get_batch(db, company_id, batch_id)
    rows = db.execute(select(OpeningStockLine).where(
        OpeningStockLine.batch_id == batch.id,
        OpeningStockLine.validation_status == OpeningLineValidation.INVALID.value)
        .order_by(OpeningStockLine.row_number)).scalars().all()
    return batch, [{"line_id": str(l.id), "row_number": l.row_number,
                    "line_type": l.line_type,
                    "errors": json.loads(l.validation_errors) if l.validation_errors else []}
                   for l in rows]


def revalidate_batch(db: Session, company_id, batch_id) -> dict:
    """Re-run validation against live master/inventory state. Only DRAFT batches
    may be revalidated (lines are frozen once submitted)."""
    batch = batch_svc.get_batch(db, company_id, batch_id)
    if batch.status != OpeningBatchStatus.DRAFT.value:
        raise OpeningStockStateError("Only a DRAFT batch can be revalidated")
    lines = db.execute(select(OpeningStockLine).where(
        OpeningStockLine.batch_id == batch.id)).scalars().all()
    for line in lines:
        _revalidate_one(db, batch, line)
    db.flush()
    _dedup_and_inventory_pass(db, batch)
    db.flush()
    valid = sum(1 for l in lines if l.validation_status == OpeningLineValidation.VALID.value)
    return {"batch_id": str(batch.id), "total": len(lines), "valid": valid,
            "invalid": len(lines) - valid}


def _revalidate_one(db, batch, line: OpeningStockLine) -> None:
    """Recompute a single line's per-row validity from its stored ids."""
    if line.validation_status == OpeningLineValidation.COMMITTED.value:
        return
    errors: list[dict] = []
    if line.item_id is None:
        errors.append({"field": "item", "reason": "item reference missing"})
    if line.warehouse_id is None:
        errors.append({"field": "warehouse", "reason": "warehouse missing"})
    else:
        wh = db.get(Warehouse, line.warehouse_id)
        required = (WarehouseCategory.YARN_STORE.value if line.line_type == OpeningLineType.YARN.value
                    else WarehouseCategory.TRADING.value)
        if wh is None or wh.company_id != batch.company_id:
            errors.append({"field": "warehouse", "reason": "warehouse not in company"})
        elif wh.category != required:
            errors.append({"field": "warehouse", "reason": f"warehouse category must be {required}"})
    if line.quantity is None or _as_qty(line.quantity) is None or _as_qty(line.quantity) <= QZERO:
        errors.append({"field": "quantity", "reason": "quantity must be positive"})
    if _as_money(line.total_value) is None or _as_money(line.total_value) < MZERO:
        errors.append({"field": "total_value", "reason": "total value invalid"})
    if line.line_type == OpeningLineType.YARN.value:
        if line.lot_policy == OpeningLotPolicy.NONE.value or line.lot_policy is None:
            errors.append({"field": "internal_lot_no", "reason": "lot identity unresolved"})
    # dedup_key is (re)assigned only by _dedup_and_inventory_pass, so two valid
    # rows sharing a key never collide on the unique index before the pass runs.
    line.dedup_key = None
    if errors:
        line.validation_status = OpeningLineValidation.INVALID.value
        line.validation_errors = json.dumps(errors)
    else:
        line.validation_status = OpeningLineValidation.VALID.value
        line.validation_errors = None


def reconciliation_preview(db: Session, company_id, batch_id) -> dict:
    """Compare proposed opening quantity / value against existing on-hand stock,
    per identity. Purely informational; references NO accounting value and posts
    nothing. Financial posting remains CA-gated."""
    batch = batch_svc.get_batch(db, company_id, batch_id)
    lines = db.execute(select(OpeningStockLine).where(
        OpeningStockLine.batch_id == batch.id,
        OpeningStockLine.validation_status.in_(
            [OpeningLineValidation.VALID.value, OpeningLineValidation.COMMITTED.value]))
        .order_by(OpeningStockLine.row_number)).scalars().all()
    items = []
    total_qty = QZERO
    total_value = MZERO
    conflicts = 0
    for line in lines:
        if line.line_type == OpeningLineType.YARN.value:
            bal = inventory.balance(db, company_id=batch.company_id, item_type=ItemType.YARN,
                                    item_id=line.item_id, lot_id=line.yarn_lot_id)
        else:
            bal = inventory.balance(db, company_id=batch.company_id, item_type=ItemType.TRADING_FABRIC,
                                    item_id=line.item_id, warehouse_id=line.warehouse_id)
        existing_qty = _as_qty(bal["qty_on_hand"]) or QZERO
        proposed_qty = _as_qty(line.quantity) or QZERO
        proposed_value = _as_money(line.total_value) or MZERO
        in_conflict = existing_qty > QZERO and line.validation_status == OpeningLineValidation.VALID.value
        if in_conflict:
            conflicts += 1
        total_qty = quantize(total_qty + proposed_qty, QTY_SCALE)
        total_value = quantize(total_value + proposed_value, MONEY_SCALE)
        items.append({
            "line_id": str(line.id), "line_type": line.line_type,
            "item_id": str(line.item_id) if line.item_id else None,
            "yarn_lot_id": str(line.yarn_lot_id) if line.yarn_lot_id else None,
            "internal_lot_no": line.internal_lot_no,
            "warehouse_id": str(line.warehouse_id) if line.warehouse_id else None,
            "proposed_qty": str(proposed_qty), "proposed_value": str(proposed_value),
            "existing_on_hand_qty": str(existing_qty),
            "existing_on_hand_value": str(_as_money(bal["value"]) or MZERO),
            "status": line.validation_status, "conflict": bool(in_conflict),
        })
    return {"batch_id": str(batch.id), "batch_status": batch.status,
            "line_count": len(items), "conflicts": conflicts,
            "proposed_total_qty": str(total_qty), "proposed_total_value": str(total_value),
            "accounting_posting": "BLOCKED_PENDING_CA_APPROVAL", "items": items}


# ------------------------- submit / approve / commit ------------------------

def submit_batch(db: Session, company_id, batch_id, *, submitted_by, actor_name=None):
    """Validate every line, then run the P1 DRAFT->SUBMITTED transition. Blocks
    submission unless there is at least one line and every line is VALID."""
    batch = batch_svc.get_batch(db, company_id, batch_id)
    if batch.status == OpeningBatchStatus.DRAFT.value:
        _assert_all_valid(db, batch)
    return batch_svc.submit_batch(db, company_id, batch_id,
                                  submitted_by=submitted_by, actor_name=actor_name)


def approve_batch(db: Session, company_id, batch_id, *, approved_by, actor_name=None):
    """Revalidate, run the P1 maker-checker SUBMITTED->APPROVED transition, then
    create the opening FIFO stock layers (no accounting voucher). Idempotent:
    already-committed lines are skipped."""
    batch = batch_svc.get_batch(db, company_id, batch_id)
    if batch.status == OpeningBatchStatus.SUBMITTED.value:
        _assert_all_valid(db, batch, revalidate=True)
    # P1 owns the maker-checker rule and the status transition.
    batch = batch_svc.approve_batch(db, company_id, batch_id,
                                    approved_by=approved_by, actor_name=actor_name)
    # Create opening stock for every valid, not-yet-committed line.
    lines = db.execute(select(OpeningStockLine).where(
        OpeningStockLine.batch_id == batch.id).order_by(OpeningStockLine.row_number)).scalars().all()
    committed = 0
    for line in lines:
        if line.validation_status == OpeningLineValidation.COMMITTED.value:
            continue
        if line.validation_status != OpeningLineValidation.VALID.value:
            # Should not happen (revalidated above); fail closed rather than skip.
            raise OpeningStockError(
                f"Cannot approve: line {line.row_number or line.id} is not valid")
        _commit_line_stock(db, batch, line, actor=approved_by)
        committed += 1
    db.flush()
    _audit(db, approved_by, "OPENING_STOCK_COMMIT", batch,
           {"company_id": str(batch.company_id), "batch_number": batch.batch_number,
            "lines_committed": committed})
    return batch, committed


def _assert_all_valid(db, batch, *, revalidate=False):
    if revalidate:
        for line in db.execute(select(OpeningStockLine).where(
                OpeningStockLine.batch_id == batch.id)).scalars().all():
            _revalidate_one(db, batch, line)
        db.flush()
        _dedup_and_inventory_pass(db, batch)
        db.flush()
    rows = db.execute(select(OpeningStockLine).where(
        OpeningStockLine.batch_id == batch.id)).scalars().all()
    if not rows:
        raise OpeningStockStateError("The batch has no opening lines to submit")
    bad = [l for l in rows if l.validation_status not in
           (OpeningLineValidation.VALID.value, OpeningLineValidation.COMMITTED.value)]
    if bad:
        raise OpeningStockStateError(
            f"{len(bad)} line(s) are invalid or unvalidated; correct them before proceeding")


def _commit_line_stock(db, batch, line: OpeningStockLine, *, actor) -> None:
    """Create the FIFO stock layer for one approved opening line. Opens a YarnLot
    for yarn lines that need one. Posts NO accounting voucher."""
    value = _as_money(line.total_value) or MZERO
    qty = _as_qty(line.quantity)
    if qty is None or qty <= QZERO:
        raise OpeningStockError("Cannot commit a line with non-positive quantity")

    if line.line_type == OpeningLineType.YARN.value:
        lot_id = _ensure_yarn_lot(db, batch, line, actor=actor)
        layer = inventory.receive(
            db, company_id=batch.company_id, item_type=ItemType.YARN, item_id=line.item_id,
            qty=qty, unit_cost=line.unit_cost, value=value, lot_id=lot_id,
            warehouse_id=line.warehouse_id, event_type=_OPENING_EVENT,
            source_module="opening_yarn", source_id=str(line.id))
    else:
        layer = inventory.receive(
            db, company_id=batch.company_id, item_type=ItemType.TRADING_FABRIC, item_id=line.item_id,
            qty=qty, unit_cost=line.unit_cost, value=value, warehouse_id=line.warehouse_id,
            event_type=_OPENING_EVENT, source_module="opening_trading", source_id=str(line.id))

    line.stock_layer_id = layer.id
    line.committed_at = _now()
    line.validation_status = OpeningLineValidation.COMMITTED.value
    line.updated_by = actor
    _audit(db, actor, "OPENING_STOCK_LINE_COMMITTED", line,
           {"company_id": str(batch.company_id), "batch_number": batch.batch_number,
            "line_type": line.line_type, "item_id": str(line.item_id),
            "yarn_lot_id": str(line.yarn_lot_id) if line.yarn_lot_id else None,
            "warehouse_id": str(line.warehouse_id) if line.warehouse_id else None,
            "qty": str(qty), "value": str(value), "stock_layer_id": str(layer.id),
            "event_type": _OPENING_EVENT})


def _ensure_yarn_lot(db, batch, line, *, actor) -> uuid.UUID:
    """Return the YarnLot id for a yarn opening line, creating it when needed.
    Never renumbers or overwrites an existing lot; never fabricates a supplier."""
    if line.lot_policy == OpeningLotPolicy.EXISTING_LOT.value and line.yarn_lot_id:
        return line.yarn_lot_id

    if line.lot_policy == OpeningLotPolicy.NEW_LOT.value:
        internal = line.internal_lot_no
        # Approval-time safety net for the SEQUENTIAL / already-committed case. The
        # database unique index (uq_yarn_lot_company_internal_no) is the authority
        # for the genuinely-concurrent case: a losing INSERT raises IntegrityError,
        # the transaction rolls back and no lot or stock is created.
        existing = _lot_by_company_number(db, batch.company_id, internal)
        if existing is not None:
            bal = inventory.balance(db, company_id=batch.company_id, item_type=ItemType.YARN,
                                    item_id=line.item_id, lot_id=existing.id)
            if existing.yarn_id != line.item_id or (_as_qty(bal["qty_on_hand"]) or QZERO) > QZERO:
                raise OpeningStockError(
                    f"Yarn lot '{internal}' already exists; opening would double-count")
            line.yarn_lot_id = existing.id
            return existing.id
    else:  # LEGACY_PLACEHOLDER
        internal = f"LEGACY-{batch.batch_number}-{line.row_number or str(line.id)[:8]}"

    lot = YarnLot(
        company_id=batch.company_id, yarn_id=line.item_id, supplier_id=line.supplier_id,
        internal_lot_no=internal, supplier_lot_no=line.supplier_lot_no,
        warehouse_id=line.warehouse_id,
        received_date=line.effective_date.isoformat() if line.effective_date else None,
        unit_cost=line.unit_cost, created_by=actor, updated_by=actor)
    db.add(lot)
    db.flush()
    line.yarn_lot_id = lot.id
    line.internal_lot_no = internal
    return lot.id
