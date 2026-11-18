"""Trading-fabric sales order services (Stage 4B-3A.1a).

Scope is strictly the sales ORDER: create, approve, cancel, read, outstanding,
and an INFORMATIONAL stock-availability read. It records commercial intent only.

It performs NO stock movement, NO FIFO consumption, NO reservation and NO
accounting posting. Dispatch, COGS, invoicing and returns are later,
separately-approved stages. Availability is informational and is never a
guaranteed allocation.
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
from app.models.masters import Customer, TaxCode, UnitOfMeasure
from app.models.trading import (
    TradingFabricItem, TradingSalesOrder, TradingSalesOrderLine, TradingSOStatus,
)
from app.services import inventory
from app.services.trading import assert_trading_warehouse, TradingWarehouseError

MZERO = quantize(0, MONEY_SCALE)
QZERO = quantize(0, QTY_SCALE)


class TradingSalesError(ValueError):
    """An invalid trading sales-order request (bad reference, bad state, bad qty)."""


def _money(v):
    return quantize(Decimal(str(v or 0)), MONEY_SCALE)


def _qty(v):
    return quantize(Decimal(str(v or 0)), QTY_SCALE)


def _now():
    return datetime.now(timezone.utc)


def _audit(db, actor, action, entity_id, detail):
    db.add(AuditLog(actor_id=actor, action=action, entity="trading_sales_order",
                    entity_id=entity_id, detail=json.dumps(detail, default=str)))


# --------------------------- reference validation ---------------------------

def _require_customer(db, company_id, customer_id) -> Customer:
    c = db.get(Customer, customer_id)
    if c is None or c.company_id != company_id:
        raise TradingSalesError("Customer is not in the active company")
    return c


def _require_factory(db, company_id, factory_id) -> None:
    if factory_id is None:
        return
    b = db.get(Branch, factory_id)
    if b is None or b.company_id != company_id:
        raise TradingSalesError("Factory is not in the active company")


def _require_item(db, company_id, item_id) -> TradingFabricItem:
    it = db.get(TradingFabricItem, item_id)
    if it is None or it.company_id != company_id:
        raise TradingSalesError("Trading SKU is not in the active company")
    if not it.is_active:
        raise TradingSalesError(f"Trading SKU '{it.code}' is inactive")
    return it


def _require_refs(db, company_id, *, uom_id, tax_code_id) -> None:
    if uom_id is not None:
        u = db.get(UnitOfMeasure, uom_id)
        if u is None or u.company_id != company_id:
            raise TradingSalesError("Unit of measure is not in the active company")
    if tax_code_id is not None:
        t = db.get(TaxCode, tax_code_id)
        if t is None or t.company_id != company_id:
            raise TradingSalesError("Tax code is not in the active company")


def _validate_lines(db, company_id, factory_id, lines: list[dict]) -> None:
    """Validate every line's SKU (active, company), warehouse (strict TRADING of
    this company/factory), UOM/tax ownership and a positive ordered quantity."""
    for ln in lines:
        _require_item(db, company_id, ln["trading_item_id"])
        _require_refs(db, company_id, uom_id=ln.get("uom_id"), tax_code_id=ln.get("tax_code_id"))
        try:
            assert_trading_warehouse(db, company_id, ln.get("warehouse_id"), factory_id=factory_id)
        except TradingWarehouseError as exc:
            raise TradingSalesError(str(exc))
        if _qty(ln.get("qty_ordered")) <= QZERO:
            raise TradingSalesError("Ordered quantity must be positive")
        if _money(ln.get("discount")) < MZERO:
            raise TradingSalesError("Discount cannot be negative")


# ------------------------------- lifecycle ----------------------------------

def create_trading_so(db: Session, company_id: uuid.UUID, *, customer_id: uuid.UUID, order_number: str,
                      order_date: date, lines: list[dict], factory_id: uuid.UUID | None = None,
                      expected_dispatch_date: date | None = None, customer_po_ref: str | None = None,
                      remarks: str | None = None, created_by: uuid.UUID | None = None) -> TradingSalesOrder:
    if not (order_number or "").strip():
        raise TradingSalesError("Order number is required")
    if not lines:
        raise TradingSalesError("A sales order needs at least one line")
    _require_customer(db, company_id, customer_id)
    _require_factory(db, company_id, factory_id)
    _validate_lines(db, company_id, factory_id, lines)

    so = TradingSalesOrder(
        company_id=company_id, customer_id=customer_id, factory_id=factory_id,
        order_number=order_number.strip(), order_date=order_date,
        expected_dispatch_date=expected_dispatch_date, customer_po_ref=customer_po_ref,
        status=TradingSOStatus.DRAFT.value, remarks=remarks, created_by=created_by)
    db.add(so)
    db.flush()
    for i, ln in enumerate(lines, start=1):
        so.lines.append(TradingSalesOrderLine(
            line_no=i, trading_item_id=ln["trading_item_id"], warehouse_id=ln["warehouse_id"],
            qty_ordered=_qty(ln.get("qty_ordered")), qty_dispatched=QZERO, qty_cancelled=QZERO,
            uom_id=ln.get("uom_id"), rate=_money(ln.get("rate")), discount=_money(ln.get("discount")),
            tax_code_id=ln.get("tax_code_id"), description=ln.get("description"), created_by=created_by))
    db.flush()
    _audit(db, created_by, "CREATE", str(so.id),
           {"doc": "trading_so", "number": so.order_number, "lines": len(so.lines)})
    return so


def get_trading_so(db: Session, company_id: uuid.UUID, so_id: uuid.UUID) -> TradingSalesOrder:
    so = db.get(TradingSalesOrder, so_id)
    if so is None or so.company_id != company_id:
        raise TradingSalesError("Sales order not found in the active company")
    return so


def list_trading_sos(db: Session, company_id: uuid.UUID, *, status: str | None = None,
                     customer_id: uuid.UUID | None = None, limit: int = 100, offset: int = 0):
    stmt = select(TradingSalesOrder).where(TradingSalesOrder.company_id == company_id)
    if status:
        stmt = stmt.where(TradingSalesOrder.status == status)
    if customer_id:
        stmt = stmt.where(TradingSalesOrder.customer_id == customer_id)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = db.execute(stmt.order_by(TradingSalesOrder.order_date.desc(), TradingSalesOrder.id)
                      .limit(max(1, min(limit, 200))).offset(max(0, offset))).scalars().all()
    return rows, total


def approve_trading_so(db: Session, company_id: uuid.UUID, so_id: uuid.UUID, *,
                       approved_by: uuid.UUID | None = None) -> TradingSalesOrder:
    """DRAFT -> APPROVED. Re-validates references. Idempotent. Moves no stock and
    posts no accounting."""
    so = get_trading_so(db, company_id, so_id)
    if so.status == TradingSOStatus.APPROVED.value:
        return so                                   # idempotent
    if so.status != TradingSOStatus.DRAFT.value:
        raise TradingSalesError(f"Only a DRAFT sales order can be approved (is {so.status})")
    _require_customer(db, company_id, so.customer_id)
    _require_factory(db, company_id, so.factory_id)
    _validate_lines(db, company_id, so.factory_id, [{
        "trading_item_id": l.trading_item_id, "warehouse_id": l.warehouse_id,
        "uom_id": l.uom_id, "tax_code_id": l.tax_code_id, "qty_ordered": l.qty_ordered,
        "discount": l.discount} for l in so.lines])
    so.status = TradingSOStatus.APPROVED.value
    so.approved_by = approved_by
    so.approved_at = _now()
    db.flush()
    _audit(db, approved_by, "APPROVE", str(so.id), {"doc": "trading_so"})
    return so


def cancel_trading_so(db: Session, company_id: uuid.UUID, so_id: uuid.UUID, *,
                      cancelled_by: uuid.UUID | None = None) -> TradingSalesOrder:
    so = get_trading_so(db, company_id, so_id)
    if so.status not in (TradingSOStatus.DRAFT.value, TradingSOStatus.APPROVED.value):
        raise TradingSalesError(f"Sales order cannot be cancelled (is {so.status})")
    if any(_qty(l.qty_dispatched) > QZERO for l in so.lines):
        raise TradingSalesError("A sales order with dispatched quantity cannot be cancelled")
    so.status = TradingSOStatus.CANCELLED.value
    so.cancelled_by = cancelled_by
    so.cancelled_at = _now()
    db.flush()
    _audit(db, cancelled_by, "CANCEL", str(so.id), {"doc": "trading_so"})
    return so


def so_outstanding(so: TradingSalesOrder) -> list[dict]:
    rows = []
    for l in so.lines:
        ordered, dispatched, cancelled = _qty(l.qty_ordered), _qty(l.qty_dispatched), _qty(l.qty_cancelled)
        outstanding = quantize(ordered - dispatched - cancelled, QTY_SCALE)
        rows.append({"line_id": str(l.id), "line_no": l.line_no,
                     "trading_item_id": str(l.trading_item_id), "warehouse_id": str(l.warehouse_id),
                     "ordered": ordered, "dispatched": dispatched, "cancelled": cancelled,
                     "outstanding": outstanding if outstanding > QZERO else QZERO})
    return rows


def availability(db: Session, company_id: uuid.UUID, item_id: uuid.UUID,
                 warehouse_id: uuid.UUID | None = None) -> dict:
    """INFORMATIONAL on-hand trading stock for a SKU (optionally one TRADING
    warehouse), read from the existing FIFO balance. This is NOT a reservation or
    a guaranteed allocation: another order may consume this stock before dispatch.
    """
    _require_item(db, company_id, item_id)
    bal = inventory.balance(db, company_id=company_id, item_type=ItemType.TRADING_FABRIC,
                            item_id=item_id, warehouse_id=warehouse_id)
    return {"trading_item_id": str(item_id),
            "warehouse_id": str(warehouse_id) if warehouse_id else None,
            "on_hand_qty": bal["qty_on_hand"], "on_hand_value": bal["value"],
            "informational_only": True, "is_reservation": False, "is_guaranteed_allocation": False}
