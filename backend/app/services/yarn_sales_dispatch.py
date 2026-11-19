"""Direct yarn sales dispatch services (Stage 5B).

Draft / submit / cancel are commercial-document steps that move NO stock and post
NO accounting. APPROVAL is the terminal step: it consumes existing yarn FIFO lots
through the shared inventory engine (optionally restricted to a user-selected
supplier lot — never silently substituted), records per-layer lot-level evidence,
and posts a balanced YARN_DELIVERY COGS voucher — atomically and idempotently in
the caller's single transaction.

No FabricRoll, roll ID, knitting WIP, greige stock, genealogy edge or packing
list is created, and no new yarn lot is generated. Yarn stock never uses the
TRADING warehouse or ItemType.TRADING_FABRIC.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, QTY_SCALE, quantize
from app.models.audit import AuditLog
from app.models.core import Branch
from app.models.inventory import ItemType
from app.models.masters import Customer, UnitOfMeasure, YarnLot, YarnMaster
from app.models.yarn_sales import (
    YarnAllocationStatus, YarnDispatchInvoiceAllocation,
    YarnSalesDispatch, YarnSalesDispatchFifoAllocation, YarnSalesDispatchLine, YarnDispatchStatus,
    YarnSalesOrder, YarnSalesOrderLine, YarnSOStatus,
)
from app.services import inventory
from app.services.accounting import posting_engine as pe
from app.services.accounting.posting_engine import AccountNotPostableError, _get_postable_account
from app.services.accounting.posting_rules import YARN_EVENT_SLOTS, resolve_account
from app.services.yarn_sales_order import assert_yarn_warehouse, YarnSalesError

QZERO = quantize(0, QTY_SCALE)
MZERO = quantize(0, MONEY_SCALE)
_OPEN_SO = (YarnSOStatus.APPROVED.value, YarnSOStatus.PARTIAL.value)
OVER_DISPATCH_PERMISSION = "yarn.over_dispatch"


class YarnDispatchError(ValueError):
    """An invalid yarn dispatch request (bad reference, bad state, bad qty)."""


class YarnDispatchPostingBlocked(ValueError):
    """Required YARN_DELIVERY account mappings are missing/invalid, so approval is
    blocked BEFORE any stock movement (fail-closed)."""


class YarnDispatchOverDispatchError(ValueError):
    """A dispatch exceeds the sales-order outstanding quantity without authorisation."""


class YarnDispatchInconsistent(ValueError):
    """The dispatch is in an inconsistent posting/stock state; refuse to act and
    report rather than silently reconsume or auto-repair."""


def _money(v):
    return quantize(Decimal(str(v or 0)), MONEY_SCALE)


def _qty(v):
    return quantize(Decimal(str(v or 0)), QTY_SCALE)


def _now():
    return datetime.now(timezone.utc)


def _audit(db, actor, action, entity_id, detail):
    db.add(AuditLog(actor_id=actor, action=action, entity="yarn_sales_dispatch",
                    entity_id=entity_id, detail=json.dumps(detail, default=str)))


def _so_line_outstanding(line: YarnSalesOrderLine) -> Decimal:
    return quantize(_qty(line.qty_ordered) - _qty(line.qty_dispatched) - _qty(line.qty_cancelled), QTY_SCALE)


def _require_lot(db, company_id, yarn_id, lot_id) -> YarnLot:
    lot = db.get(YarnLot, lot_id)
    if lot is None or lot.company_id != company_id:
        raise YarnDispatchError("Yarn lot not found in the active company")
    if lot.yarn_id != yarn_id:
        raise YarnDispatchError("Selected lot does not belong to the dispatch line's yarn SKU")
    return lot


# ------------------------------- create -------------------------------------

def create_yarn_dispatch(db: Session, company_id: uuid.UUID, *, customer_id: uuid.UUID,
                         sales_order_id: uuid.UUID, dispatch_number: str, dispatch_date: date,
                         lines: list[dict], factory_id: uuid.UUID | None = None,
                         vehicle_number: str | None = None, transporter: str | None = None,
                         customer_delivery_ref: str | None = None, remarks: str | None = None,
                         created_by: uuid.UUID | None = None) -> YarnSalesDispatch:
    """Create a DRAFT dispatch against an approved yarn sales order. Moves no stock
    and does NOT change the sales order's dispatched quantity."""
    if not (dispatch_number or "").strip():
        raise YarnDispatchError("Dispatch number is required")
    if not lines:
        raise YarnDispatchError("A dispatch needs at least one line")

    cust = db.get(Customer, customer_id)
    if cust is None or cust.company_id != company_id:
        raise YarnDispatchError("Customer is not in the active company")
    if factory_id is not None:
        b = db.get(Branch, factory_id)
        if b is None or b.company_id != company_id:
            raise YarnDispatchError("Factory is not in the active company")

    so = db.get(YarnSalesOrder, sales_order_id)
    if so is None or so.company_id != company_id:
        raise YarnDispatchError("Sales order not found in the active company")
    if so.status not in _OPEN_SO:
        raise YarnDispatchError(f"Sales order is not open for dispatch (is {so.status})")
    if so.customer_id != customer_id:
        raise YarnDispatchError("Dispatch customer does not match the sales order customer")
    if factory_id is not None and so.factory_id is not None and so.factory_id != factory_id:
        raise YarnDispatchError("Dispatch factory does not match the sales order factory")
    so_lines = {l.id: l for l in so.lines}

    dup = db.execute(select(YarnSalesDispatch).where(
        YarnSalesDispatch.company_id == company_id,
        YarnSalesDispatch.dispatch_number == dispatch_number.strip())).scalar_one_or_none()
    if dup is not None:
        raise YarnDispatchError(f"Dispatch number '{dispatch_number}' already exists for this company")

    dispatch = YarnSalesDispatch(
        company_id=company_id, customer_id=customer_id, factory_id=factory_id,
        sales_order_id=sales_order_id, dispatch_number=dispatch_number.strip(),
        dispatch_date=dispatch_date, status=YarnDispatchStatus.DRAFT.value,
        vehicle_number=vehicle_number, transporter=transporter,
        customer_delivery_ref=customer_delivery_ref, remarks=remarks, created_by=created_by)
    db.add(dispatch)
    db.flush()

    for i, ln in enumerate(lines, start=1):
        so_line = so_lines.get(ln.get("sales_order_line_id"))
        if so_line is None:
            raise YarnDispatchError("Each dispatch line must reference a line of the sales order")
        yarn = db.get(YarnMaster, ln.get("yarn_id"))
        if yarn is None or yarn.company_id != company_id:
            raise YarnDispatchError("Yarn SKU is not in the active company")
        if not yarn.is_active:
            raise YarnDispatchError(f"Yarn SKU '{yarn.code}' is inactive")
        if so_line.yarn_id != yarn.id:
            raise YarnDispatchError("Dispatch line SKU does not match the sales order line SKU")
        # Warehouse (optional) must match the SO line's warehouse when the SO line
        # specifies one, and must be a valid (non-TRADING) yarn warehouse.
        wh_id = ln.get("warehouse_id", so_line.warehouse_id)
        if so_line.warehouse_id is not None and wh_id != so_line.warehouse_id:
            raise YarnDispatchError("Dispatch warehouse must match the sales order line warehouse")
        assert_yarn_warehouse(db, company_id, wh_id, factory_id=factory_id)
        # Optional specific supplier lot.
        lot_id = ln.get("lot_id")
        if lot_id is not None:
            _require_lot(db, company_id, yarn.id, lot_id)
        if ln.get("uom_id") is not None:
            u = db.get(UnitOfMeasure, ln["uom_id"])
            if u is None or u.company_id != company_id:
                raise YarnDispatchError("Unit of measure is not in the active company")
        qty = _qty(ln.get("quantity"))
        if qty <= QZERO:
            raise YarnDispatchError("Dispatch quantity must be positive")
        if qty > _so_line_outstanding(so_line):
            raise YarnDispatchError("Dispatch quantity exceeds the sales order line outstanding quantity")
        dispatch.lines.append(YarnSalesDispatchLine(
            line_no=i, yarn_id=yarn.id, sales_order_line_id=so_line.id, warehouse_id=wh_id,
            lot_id=lot_id, quantity=qty, uom_id=ln.get("uom_id"),
            description=ln.get("description"), created_by=created_by))
    db.flush()
    _audit(db, created_by, "CREATE", str(dispatch.id),
           {"doc": "yarn_dispatch", "number": dispatch.dispatch_number,
            "sales_order_id": str(sales_order_id), "lines": len(dispatch.lines)})
    return dispatch


def get_yarn_dispatch(db: Session, company_id: uuid.UUID, dispatch_id: uuid.UUID) -> YarnSalesDispatch:
    d = db.get(YarnSalesDispatch, dispatch_id)
    if d is None or d.company_id != company_id:
        raise YarnDispatchError("Dispatch not found in the active company")
    return d


def list_yarn_dispatches(db: Session, company_id: uuid.UUID, *, status: str | None = None,
                         sales_order_id: uuid.UUID | None = None, limit: int = 100, offset: int = 0):
    stmt = select(YarnSalesDispatch).where(YarnSalesDispatch.company_id == company_id)
    if status:
        stmt = stmt.where(YarnSalesDispatch.status == status)
    if sales_order_id:
        stmt = stmt.where(YarnSalesDispatch.sales_order_id == sales_order_id)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = db.execute(stmt.order_by(YarnSalesDispatch.dispatch_date.desc(), YarnSalesDispatch.id)
                      .limit(max(1, min(limit, 200))).offset(max(0, offset))).scalars().all()
    return rows, total


def submit_yarn_dispatch(db: Session, company_id: uuid.UUID, dispatch_id: uuid.UUID, *,
                         submitted_by: uuid.UUID | None = None) -> YarnSalesDispatch:
    d = get_yarn_dispatch(db, company_id, dispatch_id)
    if d.status == YarnDispatchStatus.SUBMITTED.value:
        return d
    if d.status != YarnDispatchStatus.DRAFT.value:
        raise YarnDispatchError(f"Only a DRAFT dispatch can be submitted (is {d.status})")
    so = db.get(YarnSalesOrder, d.sales_order_id)
    if so is None or so.status not in _OPEN_SO:
        raise YarnDispatchError("Sales order is no longer open for dispatch")
    so_lines = {l.id: l for l in so.lines}
    for line in d.lines:
        so_line = so_lines.get(line.sales_order_line_id)
        if so_line is None or _qty(line.quantity) > _so_line_outstanding(so_line):
            raise YarnDispatchError("Dispatch quantity exceeds the sales order line outstanding quantity")
    d.status = YarnDispatchStatus.SUBMITTED.value
    d.submitted_by = submitted_by
    d.submitted_at = _now()
    db.flush()
    _audit(db, submitted_by, "SUBMIT", str(d.id), {"doc": "yarn_dispatch"})
    return d


def cancel_yarn_dispatch(db: Session, company_id: uuid.UUID, dispatch_id: uuid.UUID, *,
                         cancelled_by: uuid.UUID | None = None) -> YarnSalesDispatch:
    """Cancel a DRAFT/SUBMITTED dispatch. No stock is restored because no stock
    movement has occurred. Locks the dispatch row and re-reads status under the
    lock so a cancel racing an APPROVAL cannot overwrite an approved dispatch."""
    d = get_yarn_dispatch(db, company_id, dispatch_id)
    _lock_row(db, YarnSalesDispatch, d.id)
    db.refresh(d)
    if d.status not in (YarnDispatchStatus.DRAFT.value, YarnDispatchStatus.SUBMITTED.value):
        raise YarnDispatchError(f"Dispatch cannot be cancelled (is {d.status})")
    d.status = YarnDispatchStatus.CANCELLED.value
    d.cancelled_by = cancelled_by
    d.cancelled_at = _now()
    db.flush()
    _audit(db, cancelled_by, "CANCEL", str(d.id), {"doc": "yarn_dispatch"})
    return d


def dispatch_lines_view(db: Session, d: YarnSalesDispatch) -> list[dict]:
    """Dispatch lines with invoiced / uninvoiced reconciliation (Stage 5G).

    Invoiced quantity is the authoritative sum of ACTIVE YarnDispatchInvoiceAllocation
    rows (RELEASED allocations from cancelled draft invoices are excluded), read in a
    SINGLE grouped query (no N+1). ``uninvoiced_quantity = quantity - invoiced``. The
    persisted per-line ``qty_invoiced`` counter is cross-checked: if it disagrees with
    the active-allocation sum, or invoiced exceeds the dispatched quantity, ``reconciled``
    is false so the inconsistency is reported rather than silently shown as zero.
    Company scope is already enforced by the caller (dispatch fetched/filtered by
    company_id); the grouped query also filters by the dispatch's company_id."""
    active_rows = db.execute(
        select(YarnDispatchInvoiceAllocation.dispatch_line_id,
               func.coalesce(func.sum(YarnDispatchInvoiceAllocation.quantity_allocated), 0))
        .where(YarnDispatchInvoiceAllocation.company_id == d.company_id,
               YarnDispatchInvoiceAllocation.dispatch_id == d.id,
               YarnDispatchInvoiceAllocation.status == YarnAllocationStatus.ACTIVE.value)
        .group_by(YarnDispatchInvoiceAllocation.dispatch_line_id)).all()
    active = {r[0]: _qty(r[1]) for r in active_rows}
    out = []
    for l in d.lines:
        qty = _qty(l.quantity)
        invoiced = active.get(l.id, QZERO)
        counter = _qty(l.qty_invoiced)
        uninvoiced = quantize(qty - invoiced, QTY_SCALE)
        reconciled = (counter == invoiced) and (invoiced <= qty)
        out.append({"id": str(l.id), "line_no": l.line_no, "yarn_id": str(l.yarn_id),
                    "sales_order_line_id": str(l.sales_order_line_id),
                    "warehouse_id": str(l.warehouse_id) if l.warehouse_id else None,
                    "lot_id": str(l.lot_id) if l.lot_id else None,
                    "quantity": str(l.quantity), "uom_id": str(l.uom_id) if l.uom_id else None,
                    "cogs_value": str(l.cogs_value), "description": l.description,
                    "invoiced_quantity": str(invoiced), "uninvoiced_quantity": str(uninvoiced),
                    "qty_invoiced_counter": str(counter), "reconciled": reconciled})
    return out


# ===========================================================================
# Dispatch APPROVAL — fail-closed FIFO consume (lot-aware) + COGS voucher
# ===========================================================================

def _validate_delivery_mappings(db: Session, company_id: uuid.UUID) -> dict:
    """Fail-closed CA gate. Resolve + validate BOTH YARN_DELIVERY accounts
    (cogs, inventory) before any stock movement. No fallback to manufactured
    (5150) or trading (5155) COGS, or any other account."""
    resolved = {}
    for slot in YARN_EVENT_SLOTS["YARN_DELIVERY"]:   # ("cogs", "inventory")
        try:
            code = resolve_account(db, company_id, "YARN_DELIVERY", slot)
        except KeyError:
            raise YarnDispatchPostingBlocked(
                f"YARN_DELIVERY '{slot}' account mapping is not configured for this company")
        try:
            _get_postable_account(db, company_id, code)
        except AccountNotPostableError as exc:
            raise YarnDispatchPostingBlocked(
                f"YARN_DELIVERY '{slot}' account is not valid for posting: {exc}")
        resolved[slot] = code
    return resolved


def _lock_row(db: Session, model, obj_id):
    stmt = select(model).where(model.id == obj_id)
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update()
    return db.execute(stmt).scalar_one_or_none()


def _refresh_so_status(db: Session, so: YarnSalesOrder) -> None:
    if so.status in (YarnSOStatus.CANCELLED.value, YarnSOStatus.DRAFT.value):
        return
    total_ordered = sum((_qty(l.qty_ordered) for l in so.lines), QZERO)
    settled = sum((_qty(l.qty_dispatched) + _qty(l.qty_cancelled) for l in so.lines), QZERO)
    dispatched = sum((_qty(l.qty_dispatched) for l in so.lines), QZERO)
    if dispatched <= QZERO:
        so.status = YarnSOStatus.APPROVED.value
    elif settled >= total_ordered:
        so.status = YarnSOStatus.COMPLETED.value
    else:
        so.status = YarnSOStatus.PARTIAL.value
    db.flush()


def approve_yarn_dispatch(db: Session, company_id: uuid.UUID, dispatch_id: uuid.UUID, *,
                          approved_by: uuid.UUID | None = None,
                          over_dispatch_authorized: bool = False,
                          over_dispatch_reason: str | None = None) -> dict:
    """Approve a SUBMITTED dispatch: consume yarn FIFO stock (lot-restricted when a
    line names a lot) AND post a balanced YARN_DELIVERY COGS voucher, atomically and
    idempotently, in the caller's single transaction. Blocks (no stock movement)
    when mappings are missing. Revalidates availability at transaction time.

    Lock order (deterministic): dispatch -> sales order -> sales-order lines.
    ``inventory.consume`` adds its own per-(company,item) advisory lock on PostgreSQL.
    """
    d = get_yarn_dispatch(db, company_id, dispatch_id)
    ref = d.posting_reference or f"YDISP:{d.dispatch_number}"
    existing_voucher = pe.find_existing(db, company_id, ref)

    # --- idempotency / inconsistency gate ---
    if d.status == YarnDispatchStatus.APPROVED.value:
        if existing_voucher is None:
            raise YarnDispatchInconsistent(
                "Dispatch is APPROVED but its COGS voucher is missing; manual reconciliation required")
        return {"already_approved": True, "dispatch_id": str(d.id), "voucher": existing_voucher,
                "cogs_value": _money(d.cogs_value)}
    if d.status != YarnDispatchStatus.SUBMITTED.value:
        raise YarnDispatchError(f"Only a SUBMITTED dispatch can be approved (is {d.status})")
    if existing_voucher is not None:
        raise YarnDispatchInconsistent(
            "A COGS voucher already exists for this dispatch reference; refusing to consume again")
    if db.execute(select(func.count()).select_from(YarnSalesDispatchFifoAllocation).where(
            YarnSalesDispatchFifoAllocation.dispatch_id == d.id)).scalar():
        raise YarnDispatchInconsistent(
            "FIFO allocation evidence already exists for a non-approved dispatch; reconciliation required")

    # --- fail-closed CA gate BEFORE any stock movement ---
    accts = _validate_delivery_mappings(db, company_id)

    # --- lock dispatch, sales order and lines (deterministic order) ---
    _lock_row(db, YarnSalesDispatch, d.id)
    so = _lock_row(db, YarnSalesOrder, d.sales_order_id)
    if so is None or so.company_id != company_id:
        raise YarnDispatchError("Sales order not found in the active company")
    if so.status not in _OPEN_SO:
        raise YarnDispatchError(f"Sales order is not open for dispatch (is {so.status})")
    if so.customer_id != d.customer_id:
        raise YarnDispatchError("Dispatch customer does not match the sales order customer")
    so_lines = {}
    for l in sorted(so.lines, key=lambda x: str(x.id)):
        _lock_row(db, YarnSalesOrderLine, l.id)
        so_lines[l.id] = l

    # --- revalidate outstanding under lock; resolve over-dispatch ---
    needs_over = False
    for line in d.lines:
        so_line = so_lines.get(line.sales_order_line_id)
        if so_line is None:
            raise YarnDispatchError("Dispatch line no longer matches a sales order line")
        if _qty(line.quantity) > _so_line_outstanding(so_line):
            needs_over = True
    if needs_over:
        if not over_dispatch_authorized:
            raise YarnDispatchOverDispatchError(
                "Dispatch exceeds the sales-order outstanding quantity; over-dispatch requires the "
                "'yarn.over_dispatch' permission and a reason")
        if not (over_dispatch_reason or "").strip():
            raise YarnDispatchOverDispatchError("Over-dispatch requires a reason")

    # --- consume FIFO per line (lot-restricted when set), persist evidence ---
    total_cogs = MZERO
    for line in d.lines:
        res = inventory.consume(
            db, company_id=company_id, item_type=ItemType.YARN, item_id=line.yarn_id,
            qty=line.quantity, lot_id=line.lot_id, warehouse_id=line.warehouse_id,
            method="FIFO", event_type="YARN_SALE_DISPATCH",
            source_module="yarn_sales_dispatch", source_id=str(d.id), posting_reference=ref)
        line.cogs_value = res["total_cost"]
        total_cogs = quantize(total_cogs + res["total_cost"], MONEY_SCALE)
        for mv in res["movements"]:
            db.add(YarnSalesDispatchFifoAllocation(
                company_id=company_id, dispatch_id=d.id, dispatch_line_id=line.id,
                stock_layer_id=uuid.UUID(mv["layer_id"]),
                source_lot_id=uuid.UUID(mv["lot_id"]) if mv["lot_id"] else None,
                quantity_consumed=mv["qty"], unit_cost=mv["unit_cost"], total_cost=mv["total_cost"],
                created_by=approved_by))

    # --- balanced COGS voucher: Dr yarn COGS / Cr yarn inventory = FIFO cost ---
    voucher = pe.post_voucher(db, pe.PostingRequest(
        company_id=company_id, voucher_type_code="SJ", voucher_date=d.dispatch_date,
        posting_reference=ref, narration=f"Yarn dispatch {d.dispatch_number} COGS",
        source_module="yarn_sales_dispatch", source_id=str(d.id), created_by=approved_by,
        lines=[pe.PostingLine(accts["cogs"], debit=total_cogs, narration="Yarn COGS"),
               pe.PostingLine(accts["inventory"], credit=total_cogs, narration="Yarn inventory")]))

    # --- roll up sales-order dispatched quantities + status ---
    for line in d.lines:
        so_line = so_lines[line.sales_order_line_id]
        so_line.qty_dispatched = quantize(_qty(so_line.qty_dispatched) + _qty(line.quantity), QTY_SCALE)
    _refresh_so_status(db, so)

    d.status = YarnDispatchStatus.APPROVED.value
    d.cogs_value = total_cogs
    d.posting_reference = ref
    d.approved_by = approved_by
    d.approved_at = _now()
    if needs_over:
        d.is_over_dispatched = True
        d.over_dispatch_reason = over_dispatch_reason.strip()
        d.over_dispatched_by = approved_by
        d.over_dispatched_at = _now()
    db.flush()
    _audit(db, approved_by, "APPROVE", str(d.id),
           {"doc": "yarn_dispatch", "cogs_value": str(total_cogs),
            "voucher": getattr(voucher, "number", None), "over_dispatched": d.is_over_dispatched})
    return {"already_approved": False, "dispatch_id": str(d.id), "voucher": voucher,
            "cogs_value": total_cogs}


def dispatch_allocations_view(db: Session, company_id: uuid.UUID, dispatch_id: uuid.UUID) -> list[dict]:
    rows = db.execute(select(YarnSalesDispatchFifoAllocation).where(
        YarnSalesDispatchFifoAllocation.company_id == company_id,
        YarnSalesDispatchFifoAllocation.dispatch_id == dispatch_id)).scalars().all()
    # Join supplier lot references for display (preserve original lot numbers).
    out = []
    for a in rows:
        lot = db.get(YarnLot, a.source_lot_id) if a.source_lot_id else None
        out.append({"id": str(a.id), "dispatch_line_id": str(a.dispatch_line_id),
                    "stock_layer_id": str(a.stock_layer_id),
                    "source_lot_id": str(a.source_lot_id) if a.source_lot_id else None,
                    "internal_lot_no": lot.internal_lot_no if lot else None,
                    "supplier_lot_no": lot.supplier_lot_no if lot else None,
                    "quantity_consumed": str(a.quantity_consumed), "unit_cost": str(a.unit_cost),
                    "total_cost": str(a.total_cost)})
    return out
