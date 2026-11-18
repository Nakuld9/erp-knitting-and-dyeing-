"""Trading-fabric dispatch services (Stage 4B-3A.1b.1 — draft lifecycle only).

Scope: create / list / get / submit / cancel a trading dispatch DRAFT against an
approved trading sales order. It is a commercial document only.

It performs NO stock movement, NO FIFO consumption (``inventory.consume`` is never
called), NO accounting posting, and it does NOT change the sales order's
dispatched quantity. Dispatch APPROVAL (which consumes stock and posts a balanced
COGS voucher) is a later, separately-authorised stage and is intentionally not
implemented here. No reservation is created or implied.
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
from app.models.masters import Customer, UnitOfMeasure
from app.models.trading import (
    TradingDispatch, TradingDispatchFifoAllocation, TradingDispatchLine, TradingDispatchStatus,
    TradingFabricItem, TradingSalesOrder, TradingSalesOrderLine, TradingSOStatus,
)
from app.services import inventory
from app.services.accounting import posting_engine as pe
from app.services.accounting.posting_engine import AccountNotPostableError, _get_postable_account
from app.services.accounting.posting_rules import TRADING_EVENT_SLOTS, resolve_account
from app.services.trading import assert_trading_warehouse, TradingWarehouseError

QZERO = quantize(0, QTY_SCALE)
MZERO = quantize(0, MONEY_SCALE)
_OPEN_SO = (TradingSOStatus.APPROVED.value, TradingSOStatus.PARTIAL.value)
OVER_DISPATCH_PERMISSION = "trading.over_dispatch"


class TradingDispatchError(ValueError):
    """An invalid trading dispatch request (bad reference, bad state, bad qty)."""


class TradingDispatchPostingBlocked(ValueError):
    """Required TRADING_DELIVERY account mappings are missing/invalid, so approval
    is blocked BEFORE any stock movement (fail-closed)."""


class TradingDispatchOverDispatchError(ValueError):
    """A dispatch exceeds the sales-order outstanding quantity without authorisation."""


class TradingDispatchInconsistent(ValueError):
    """The dispatch is in an inconsistent posting/stock state; refuse to act and
    report rather than silently reconsume or auto-repair."""


def _money(v):
    return quantize(Decimal(str(v or 0)), MONEY_SCALE)


def _qty(v):
    return quantize(Decimal(str(v or 0)), QTY_SCALE)


def _now():
    return datetime.now(timezone.utc)


def _audit(db, actor, action, entity_id, detail):
    db.add(AuditLog(actor_id=actor, action=action, entity="trading_dispatch",
                    entity_id=entity_id, detail=json.dumps(detail, default=str)))


def _so_line_outstanding(line: TradingSalesOrderLine) -> Decimal:
    return quantize(_qty(line.qty_ordered) - _qty(line.qty_dispatched) - _qty(line.qty_cancelled), QTY_SCALE)


# ------------------------------- create -------------------------------------

def create_trading_dispatch(db: Session, company_id: uuid.UUID, *, customer_id: uuid.UUID,
                            sales_order_id: uuid.UUID, dispatch_number: str, dispatch_date: date,
                            lines: list[dict], factory_id: uuid.UUID | None = None,
                            vehicle_number: str | None = None, transporter: str | None = None,
                            customer_delivery_ref: str | None = None, remarks: str | None = None,
                            created_by: uuid.UUID | None = None) -> TradingDispatch:
    """Create a DRAFT dispatch against an approved sales order. Moves no stock and
    does NOT change the sales order's dispatched quantity."""
    if not (dispatch_number or "").strip():
        raise TradingDispatchError("Dispatch number is required")
    if not lines:
        raise TradingDispatchError("A dispatch needs at least one line")

    # Customer (active company) and factory.
    cust = db.get(Customer, customer_id)
    if cust is None or cust.company_id != company_id:
        raise TradingDispatchError("Customer is not in the active company")
    if factory_id is not None:
        b = db.get(Branch, factory_id)
        if b is None or b.company_id != company_id:
            raise TradingDispatchError("Factory is not in the active company")

    # Sales order: company, open status, same customer, factory consistency.
    so = db.get(TradingSalesOrder, sales_order_id)
    if so is None or so.company_id != company_id:
        raise TradingDispatchError("Sales order not found in the active company")
    if so.status not in _OPEN_SO:
        raise TradingDispatchError(f"Sales order is not open for dispatch (is {so.status})")
    if so.customer_id != customer_id:
        raise TradingDispatchError("Dispatch customer does not match the sales order customer")
    if factory_id is not None and so.factory_id is not None and so.factory_id != factory_id:
        raise TradingDispatchError("Dispatch factory does not match the sales order factory")
    so_lines = {l.id: l for l in so.lines}

    dispatch = TradingDispatch(
        company_id=company_id, customer_id=customer_id, factory_id=factory_id,
        sales_order_id=sales_order_id, dispatch_number=dispatch_number.strip(),
        dispatch_date=dispatch_date, status=TradingDispatchStatus.DRAFT.value,
        vehicle_number=vehicle_number, transporter=transporter,
        customer_delivery_ref=customer_delivery_ref, remarks=remarks, created_by=created_by)
    db.add(dispatch)
    db.flush()

    for i, ln in enumerate(lines, start=1):
        so_line = so_lines.get(ln.get("sales_order_line_id"))
        if so_line is None:
            raise TradingDispatchError("Each dispatch line must reference a line of the sales order")
        # SKU must match the SO line's SKU and be an active company SKU.
        item = db.get(TradingFabricItem, ln.get("trading_item_id"))
        if item is None or item.company_id != company_id:
            raise TradingDispatchError("Trading SKU is not in the active company")
        if not item.is_active:
            raise TradingDispatchError(f"Trading SKU '{item.code}' is inactive")
        if so_line.trading_item_id != item.id:
            raise TradingDispatchError("Dispatch line SKU does not match the sales order line SKU")
        # Warehouse must be the SO line's TRADING warehouse (company/factory scoped).
        wh_id = ln.get("warehouse_id")
        if wh_id != so_line.warehouse_id:
            raise TradingDispatchError("Dispatch warehouse must match the sales order line warehouse")
        try:
            assert_trading_warehouse(db, company_id, wh_id, factory_id=factory_id)
        except TradingWarehouseError as exc:
            raise TradingDispatchError(str(exc))
        # UOM ownership (optional).
        if ln.get("uom_id") is not None:
            u = db.get(UnitOfMeasure, ln["uom_id"])
            if u is None or u.company_id != company_id:
                raise TradingDispatchError("Unit of measure is not in the active company")
        qty = _qty(ln.get("quantity"))
        if qty <= QZERO:
            raise TradingDispatchError("Dispatch quantity must be positive")
        # Must not exceed the SO line's current outstanding. Over-dispatch is blocked
        # in this stage (the authorised override is a later stage).
        if qty > _so_line_outstanding(so_line):
            raise TradingDispatchError(
                "Dispatch quantity exceeds the sales order line outstanding quantity")
        dispatch.lines.append(TradingDispatchLine(
            line_no=i, trading_item_id=item.id, sales_order_line_id=so_line.id,
            warehouse_id=wh_id, quantity=qty, uom_id=ln.get("uom_id"),
            description=ln.get("description"), created_by=created_by))
    db.flush()
    _audit(db, created_by, "CREATE", str(dispatch.id),
           {"doc": "trading_dispatch", "number": dispatch.dispatch_number,
            "sales_order_id": str(sales_order_id), "lines": len(dispatch.lines)})
    return dispatch


def get_trading_dispatch(db: Session, company_id: uuid.UUID, dispatch_id: uuid.UUID) -> TradingDispatch:
    d = db.get(TradingDispatch, dispatch_id)
    if d is None or d.company_id != company_id:
        raise TradingDispatchError("Dispatch not found in the active company")
    return d


def list_trading_dispatches(db: Session, company_id: uuid.UUID, *, status: str | None = None,
                            sales_order_id: uuid.UUID | None = None, limit: int = 100, offset: int = 0):
    stmt = select(TradingDispatch).where(TradingDispatch.company_id == company_id)
    if status:
        stmt = stmt.where(TradingDispatch.status == status)
    if sales_order_id:
        stmt = stmt.where(TradingDispatch.sales_order_id == sales_order_id)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = db.execute(stmt.order_by(TradingDispatch.dispatch_date.desc(), TradingDispatch.id)
                      .limit(max(1, min(limit, 200))).offset(max(0, offset))).scalars().all()
    return rows, total


def submit_trading_dispatch(db: Session, company_id: uuid.UUID, dispatch_id: uuid.UUID, *,
                            submitted_by: uuid.UUID | None = None) -> TradingDispatch:
    """DRAFT -> SUBMITTED. Re-validates the SO is still open and each line is still
    within outstanding. Idempotent. Moves no stock, posts no accounting."""
    d = get_trading_dispatch(db, company_id, dispatch_id)
    if d.status == TradingDispatchStatus.SUBMITTED.value:
        return d                                    # idempotent
    if d.status != TradingDispatchStatus.DRAFT.value:
        raise TradingDispatchError(f"Only a DRAFT dispatch can be submitted (is {d.status})")
    so = db.get(TradingSalesOrder, d.sales_order_id)
    if so is None or so.status not in _OPEN_SO:
        raise TradingDispatchError("Sales order is no longer open for dispatch")
    so_lines = {l.id: l for l in so.lines}
    for line in d.lines:
        so_line = so_lines.get(line.sales_order_line_id)
        if so_line is None or _qty(line.quantity) > _so_line_outstanding(so_line):
            raise TradingDispatchError(
                "Dispatch quantity exceeds the sales order line outstanding quantity")
    d.status = TradingDispatchStatus.SUBMITTED.value
    d.submitted_by = submitted_by
    d.submitted_at = _now()
    db.flush()
    _audit(db, submitted_by, "SUBMIT", str(d.id), {"doc": "trading_dispatch"})
    return d


def cancel_trading_dispatch(db: Session, company_id: uuid.UUID, dispatch_id: uuid.UUID, *,
                            cancelled_by: uuid.UUID | None = None) -> TradingDispatch:
    """Cancel a DRAFT/SUBMITTED dispatch. No stock is restored because no stock
    movement has occurred in this stage.

    Locks the dispatch row and re-reads its status under the lock, so a cancel that
    races a concurrent APPROVAL can never overwrite an approved (stock-consuming)
    dispatch: the cancel blocks on the approval's row lock, then sees APPROVED and
    is rejected.
    """
    d = get_trading_dispatch(db, company_id, dispatch_id)
    _lock_row(db, TradingDispatch, d.id)
    db.refresh(d)
    if d.status not in (TradingDispatchStatus.DRAFT.value, TradingDispatchStatus.SUBMITTED.value):
        raise TradingDispatchError(f"Dispatch cannot be cancelled (is {d.status})")
    d.status = TradingDispatchStatus.CANCELLED.value
    d.cancelled_by = cancelled_by
    d.cancelled_at = _now()
    db.flush()
    _audit(db, cancelled_by, "CANCEL", str(d.id), {"doc": "trading_dispatch"})
    return d


def dispatch_lines_view(d: TradingDispatch) -> list[dict]:
    return [{"id": str(l.id), "line_no": l.line_no, "trading_item_id": str(l.trading_item_id),
             "sales_order_line_id": str(l.sales_order_line_id), "warehouse_id": str(l.warehouse_id),
             "quantity": str(l.quantity), "uom_id": str(l.uom_id) if l.uom_id else None,
             "description": l.description} for l in d.lines]


# ===========================================================================
# Dispatch APPROVAL (Stage 4B-3A.1b.2) — fail-closed FIFO consume + COGS voucher
# ===========================================================================

def _validate_delivery_mappings(db: Session, company_id: uuid.UUID) -> dict:
    """Fail-closed CA gate. Resolve and validate BOTH TRADING_DELIVERY accounts
    (cogs, inventory) before any stock movement. No fallback to generic/
    manufactured/chemical accounts: an unconfigured or non-postable account raises
    TradingDispatchPostingBlocked (-> HTTP 409)."""
    resolved = {}
    for slot in TRADING_EVENT_SLOTS["TRADING_DELIVERY"]:   # ("cogs", "inventory")
        try:
            code = resolve_account(db, company_id, "TRADING_DELIVERY", slot)
        except KeyError:
            raise TradingDispatchPostingBlocked(
                f"TRADING_DELIVERY '{slot}' account mapping is not configured for this company")
        try:
            _get_postable_account(db, company_id, code)   # exists + company + not group + postable + active
        except AccountNotPostableError as exc:
            raise TradingDispatchPostingBlocked(
                f"TRADING_DELIVERY '{slot}' account is not valid for posting: {exc}")
        resolved[slot] = code
    return resolved


def _lock_row(db: Session, model, obj_id):
    stmt = select(model).where(model.id == obj_id)
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update()
    return db.execute(stmt).scalar_one_or_none()


def _refresh_so_status(db: Session, so: TradingSalesOrder) -> None:
    if so.status in (TradingSOStatus.CANCELLED.value, TradingSOStatus.DRAFT.value):
        return
    total_ordered = sum((_qty(l.qty_ordered) for l in so.lines), QZERO)
    settled = sum((_qty(l.qty_dispatched) + _qty(l.qty_cancelled) for l in so.lines), QZERO)
    dispatched = sum((_qty(l.qty_dispatched) for l in so.lines), QZERO)
    if dispatched <= QZERO:
        so.status = TradingSOStatus.APPROVED.value
    elif settled >= total_ordered:
        so.status = TradingSOStatus.COMPLETED.value
    else:
        so.status = TradingSOStatus.PARTIAL.value
    db.flush()


def approve_trading_dispatch(db: Session, company_id: uuid.UUID, dispatch_id: uuid.UUID, *,
                             approved_by: uuid.UUID | None = None,
                             over_dispatch_authorized: bool = False,
                             over_dispatch_reason: str | None = None) -> dict:
    """Approve a SUBMITTED dispatch: consume trading FIFO stock AND post a balanced
    TRADING_DELIVERY COGS voucher, atomically and idempotently, in the caller's
    single transaction. Blocks (no stock movement) when mappings are missing.

    Lock order (deterministic, documented): dispatch row -> sales order row ->
    sales-order lines (by id). ``inventory.consume`` adds its own per-(company,item)
    advisory lock on PostgreSQL.
    """
    d = get_trading_dispatch(db, company_id, dispatch_id)
    ref = d.posting_reference or f"TDISP:{d.dispatch_number}"
    existing_voucher = pe.find_existing(db, company_id, ref)

    # --- idempotency / inconsistency gate (never blindly reconsume) ---
    if d.status == TradingDispatchStatus.APPROVED.value:
        if existing_voucher is None:
            raise TradingDispatchInconsistent(
                "Dispatch is APPROVED but its COGS voucher is missing; manual reconciliation required")
        return {"already_approved": True, "dispatch_id": str(d.id), "voucher": existing_voucher,
                "cogs_value": _money(d.cogs_value)}
    if d.status != TradingDispatchStatus.SUBMITTED.value:
        raise TradingDispatchError(f"Only a SUBMITTED dispatch can be approved (is {d.status})")
    if existing_voucher is not None:
        raise TradingDispatchInconsistent(
            "A COGS voucher already exists for this dispatch reference; refusing to consume again")
    if db.execute(select(func.count()).select_from(TradingDispatchFifoAllocation).where(
            TradingDispatchFifoAllocation.dispatch_id == d.id)).scalar():
        raise TradingDispatchInconsistent(
            "FIFO allocation evidence already exists for a non-approved dispatch; reconciliation required")

    # --- fail-closed CA gate BEFORE any stock movement ---
    accts = _validate_delivery_mappings(db, company_id)

    # --- lock dispatch, sales order and its lines (deterministic order) ---
    _lock_row(db, TradingDispatch, d.id)
    so = _lock_row(db, TradingSalesOrder, d.sales_order_id)
    if so is None or so.company_id != company_id:
        raise TradingDispatchError("Sales order not found in the active company")
    if so.status not in _OPEN_SO:
        raise TradingDispatchError(f"Sales order is not open for dispatch (is {so.status})")
    if so.customer_id != d.customer_id:
        raise TradingDispatchError("Dispatch customer does not match the sales order customer")
    so_lines = {}
    for l in sorted(so.lines, key=lambda x: str(x.id)):
        _lock_row(db, TradingSalesOrderLine, l.id)
        so_lines[l.id] = l

    # --- revalidate outstanding under lock; resolve over-dispatch ---
    needs_over = False
    for line in d.lines:
        so_line = so_lines.get(line.sales_order_line_id)
        if so_line is None:
            raise TradingDispatchError("Dispatch line no longer matches a sales order line")
        if _qty(line.quantity) > _so_line_outstanding(so_line):
            needs_over = True
    if needs_over:
        if not over_dispatch_authorized:
            raise TradingDispatchOverDispatchError(
                "Dispatch exceeds the sales-order outstanding quantity; over-dispatch requires the "
                "'trading.over_dispatch' permission and a reason")
        if not (over_dispatch_reason or "").strip():
            raise TradingDispatchOverDispatchError("Over-dispatch requires a reason")

    # --- consume FIFO per line, persist per-layer evidence, accumulate COGS ---
    total_cogs = MZERO
    for line in d.lines:
        res = inventory.consume(
            db, company_id=company_id, item_type=ItemType.TRADING_FABRIC, item_id=line.trading_item_id,
            qty=line.quantity, warehouse_id=line.warehouse_id, method="FIFO", event_type="DISPATCH",
            source_module="trading_dispatch", source_id=str(d.id), posting_reference=ref)
        line.cogs_value = res["total_cost"]
        total_cogs = quantize(total_cogs + res["total_cost"], MONEY_SCALE)
        for mv in res["movements"]:
            db.add(TradingDispatchFifoAllocation(
                company_id=company_id, dispatch_id=d.id, dispatch_line_id=line.id,
                stock_layer_id=uuid.UUID(mv["layer_id"]),
                source_lot_id=uuid.UUID(mv["lot_id"]) if mv["lot_id"] else None,
                quantity_consumed=mv["qty"], unit_cost=mv["unit_cost"], total_cost=mv["total_cost"],
                created_by=approved_by))

    # --- balanced COGS voucher: Dr trading COGS / Cr trading inventory = FIFO cost ---
    voucher = pe.post_voucher(db, pe.PostingRequest(
        company_id=company_id, voucher_type_code="SJ", voucher_date=d.dispatch_date,
        posting_reference=ref, narration=f"Trading dispatch {d.dispatch_number} COGS",
        source_module="trading_dispatch", source_id=str(d.id), created_by=approved_by,
        lines=[pe.PostingLine(accts["cogs"], debit=total_cogs, narration="Trading COGS"),
               pe.PostingLine(accts["inventory"], credit=total_cogs, narration="Trading inventory")]))

    # --- roll up sales-order dispatched quantities + status (only now) ---
    for line in d.lines:
        so_line = so_lines[line.sales_order_line_id]
        so_line.qty_dispatched = quantize(_qty(so_line.qty_dispatched) + _qty(line.quantity), QTY_SCALE)
    _refresh_so_status(db, so)

    d.status = TradingDispatchStatus.APPROVED.value
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
           {"doc": "trading_dispatch", "cogs_value": str(total_cogs),
            "voucher": getattr(voucher, "number", None), "over_dispatched": d.is_over_dispatched})
    return {"already_approved": False, "dispatch_id": str(d.id), "voucher": voucher,
            "cogs_value": total_cogs}


def dispatch_allocations_view(db: Session, company_id: uuid.UUID, dispatch_id: uuid.UUID) -> list[dict]:
    rows = db.execute(select(TradingDispatchFifoAllocation).where(
        TradingDispatchFifoAllocation.company_id == company_id,
        TradingDispatchFifoAllocation.dispatch_id == dispatch_id)).scalars().all()
    return [{"id": str(a.id), "dispatch_line_id": str(a.dispatch_line_id),
             "stock_layer_id": str(a.stock_layer_id),
             "source_lot_id": str(a.source_lot_id) if a.source_lot_id else None,
             "quantity_consumed": str(a.quantity_consumed), "unit_cost": str(a.unit_cost),
             "total_cost": str(a.total_cost)} for a in rows]
