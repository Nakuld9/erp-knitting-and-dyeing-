"""Direct yarn sales-order services (Stage 5B).

Scope is the sales ORDER plus an INFORMATIONAL lot-availability read. It records
commercial intent only: NO stock movement, NO FIFO consumption, NO reservation
and NO accounting posting. Dispatch (which consumes existing yarn FIFO lots and
posts COGS) and invoicing are separate services.

Availability is informational and is NEVER a reservation or guaranteed
allocation — another order (or a production issue) may consume the same yarn
stock before dispatch. Production and direct yarn sales draw from the one shared
``ItemType.YARN`` FIFO pool; no second stock or reservation ledger is created.
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
from app.models.hybrid import WarehouseCategory
from app.models.inventory import ItemType
from app.models.masters import Customer, TaxCode, UnitOfMeasure, Warehouse, YarnLot, YarnMaster
from app.models.yarn_sales import YarnSalesOrder, YarnSalesOrderLine, YarnSOStatus
from app.services import inventory

MZERO = quantize(0, MONEY_SCALE)
QZERO = quantize(0, QTY_SCALE)


class YarnSalesError(ValueError):
    """An invalid yarn sales-order request (bad reference, bad state, bad qty)."""


def _money(v):
    return quantize(Decimal(str(v or 0)), MONEY_SCALE)


def _qty(v):
    return quantize(Decimal(str(v or 0)), QTY_SCALE)


def _now():
    return datetime.now(timezone.utc)


def _audit(db, actor, action, entity_id, detail):
    db.add(AuditLog(actor_id=actor, action=action, entity="yarn_sales_order",
                    entity_id=entity_id, detail=json.dumps(detail, default=str)))


# --------------------------- reference validation ---------------------------

def _require_customer(db, company_id, customer_id) -> Customer:
    c = db.get(Customer, customer_id)
    if c is None or c.company_id != company_id:
        raise YarnSalesError("Customer is not in the active company")
    if getattr(c, "is_active", True) is False:
        raise YarnSalesError("Customer is inactive")
    return c


def _require_factory(db, company_id, factory_id) -> None:
    if factory_id is None:
        return
    b = db.get(Branch, factory_id)
    if b is None or b.company_id != company_id:
        raise YarnSalesError("Factory is not in the active company")


def _require_yarn(db, company_id, yarn_id) -> YarnMaster:
    y = db.get(YarnMaster, yarn_id)
    if y is None or y.company_id != company_id:
        raise YarnSalesError("Yarn SKU is not in the active company")
    if not y.is_active:
        raise YarnSalesError(f"Yarn SKU '{y.code}' is inactive")
    return y


def assert_yarn_warehouse(db: Session, company_id: uuid.UUID, warehouse_id: uuid.UUID | None, *,
                          factory_id: uuid.UUID | None = None) -> Warehouse | None:
    """Validate a warehouse for yarn sales. Yarn may be held in an uncategorised /
    warehouse-less lot historically, so ``None`` is allowed. When a warehouse IS
    given it must belong to the company (and factory, if the warehouse is
    factory-scoped) and must NOT be a TRADING warehouse (rule: yarn sales never use
    the Trading stream)."""
    if warehouse_id is None:
        return None
    wh = db.get(Warehouse, warehouse_id)
    if wh is None or wh.company_id != company_id:
        raise YarnSalesError("Warehouse not found in the active company")
    if factory_id is not None and wh.branch_id is not None and wh.branch_id != factory_id:
        raise YarnSalesError(f"Warehouse '{wh.code}' belongs to a different factory")
    if wh.category == WarehouseCategory.TRADING.value:
        raise YarnSalesError(
            f"Warehouse '{wh.code}' is a TRADING warehouse; yarn sales draw from the Yarn Store, "
            "never the Trading stream.")
    return wh


def _require_refs(db, company_id, *, uom_id, tax_code_id) -> None:
    if uom_id is not None:
        u = db.get(UnitOfMeasure, uom_id)
        if u is None or u.company_id != company_id:
            raise YarnSalesError("Unit of measure is not in the active company")
    if tax_code_id is not None:
        t = db.get(TaxCode, tax_code_id)
        if t is None or t.company_id != company_id:
            raise YarnSalesError("Tax code is not in the active company")


def _validate_lines(db, company_id, factory_id, lines: list[dict]) -> None:
    for ln in lines:
        _require_yarn(db, company_id, ln["yarn_id"])
        _require_refs(db, company_id, uom_id=ln.get("uom_id"), tax_code_id=ln.get("tax_code_id"))
        assert_yarn_warehouse(db, company_id, ln.get("warehouse_id"), factory_id=factory_id)
        if _qty(ln.get("qty_ordered")) <= QZERO:
            raise YarnSalesError("Ordered quantity must be positive")
        if _money(ln.get("discount")) < MZERO:
            raise YarnSalesError("Discount cannot be negative")


# ------------------------------- lifecycle ----------------------------------

def create_yarn_so(db: Session, company_id: uuid.UUID, *, customer_id: uuid.UUID, order_number: str,
                   order_date: date, lines: list[dict], factory_id: uuid.UUID | None = None,
                   expected_dispatch_date: date | None = None, customer_po_ref: str | None = None,
                   remarks: str | None = None, created_by: uuid.UUID | None = None) -> YarnSalesOrder:
    if not (order_number or "").strip():
        raise YarnSalesError("Order number is required")
    if not lines:
        raise YarnSalesError("A sales order needs at least one line")
    _require_customer(db, company_id, customer_id)
    _require_factory(db, company_id, factory_id)
    _validate_lines(db, company_id, factory_id, lines)

    dup = db.execute(select(YarnSalesOrder).where(
        YarnSalesOrder.company_id == company_id,
        YarnSalesOrder.order_number == order_number.strip())).scalar_one_or_none()
    if dup is not None:
        raise YarnSalesError(f"Yarn sales order '{order_number}' already exists for this company")

    so = YarnSalesOrder(
        company_id=company_id, customer_id=customer_id, factory_id=factory_id,
        order_number=order_number.strip(), order_date=order_date,
        expected_dispatch_date=expected_dispatch_date, customer_po_ref=customer_po_ref,
        status=YarnSOStatus.DRAFT.value, remarks=remarks, created_by=created_by)
    db.add(so)
    db.flush()
    for i, ln in enumerate(lines, start=1):
        so.lines.append(YarnSalesOrderLine(
            line_no=i, yarn_id=ln["yarn_id"], warehouse_id=ln.get("warehouse_id"),
            qty_ordered=_qty(ln.get("qty_ordered")), qty_dispatched=QZERO, qty_cancelled=QZERO,
            uom_id=ln.get("uom_id"), rate=_money(ln.get("rate")), discount=_money(ln.get("discount")),
            tax_code_id=ln.get("tax_code_id"), description=ln.get("description"), created_by=created_by))
    db.flush()
    _audit(db, created_by, "CREATE", str(so.id),
           {"doc": "yarn_so", "number": so.order_number, "lines": len(so.lines)})
    return so


def get_yarn_so(db: Session, company_id: uuid.UUID, so_id: uuid.UUID) -> YarnSalesOrder:
    so = db.get(YarnSalesOrder, so_id)
    if so is None or so.company_id != company_id:
        raise YarnSalesError("Sales order not found in the active company")
    return so


def list_yarn_sos(db: Session, company_id: uuid.UUID, *, status: str | None = None,
                  customer_id: uuid.UUID | None = None, limit: int = 100, offset: int = 0):
    stmt = select(YarnSalesOrder).where(YarnSalesOrder.company_id == company_id)
    if status:
        stmt = stmt.where(YarnSalesOrder.status == status)
    if customer_id:
        stmt = stmt.where(YarnSalesOrder.customer_id == customer_id)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = db.execute(stmt.order_by(YarnSalesOrder.order_date.desc(), YarnSalesOrder.id)
                      .limit(max(1, min(limit, 200))).offset(max(0, offset))).scalars().all()
    return rows, total


def approve_yarn_so(db: Session, company_id: uuid.UUID, so_id: uuid.UUID, *,
                    approved_by: uuid.UUID | None = None) -> YarnSalesOrder:
    """DRAFT -> APPROVED. Re-validates references. Idempotent. No stock, no accounting,
    NO reservation (availability is only guaranteed by FIFO at dispatch time)."""
    so = get_yarn_so(db, company_id, so_id)
    if so.status == YarnSOStatus.APPROVED.value:
        return so
    if so.status != YarnSOStatus.DRAFT.value:
        raise YarnSalesError(f"Only a DRAFT sales order can be approved (is {so.status})")
    _require_customer(db, company_id, so.customer_id)
    _require_factory(db, company_id, so.factory_id)
    _validate_lines(db, company_id, so.factory_id, [{
        "yarn_id": l.yarn_id, "warehouse_id": l.warehouse_id, "uom_id": l.uom_id,
        "tax_code_id": l.tax_code_id, "qty_ordered": l.qty_ordered, "discount": l.discount}
        for l in so.lines])
    so.status = YarnSOStatus.APPROVED.value
    so.approved_by = approved_by
    so.approved_at = _now()
    db.flush()
    _audit(db, approved_by, "APPROVE", str(so.id), {"doc": "yarn_so"})
    return so


def cancel_yarn_so(db: Session, company_id: uuid.UUID, so_id: uuid.UUID, *,
                   cancelled_by: uuid.UUID | None = None) -> YarnSalesOrder:
    so = get_yarn_so(db, company_id, so_id)
    if so.status not in (YarnSOStatus.DRAFT.value, YarnSOStatus.APPROVED.value):
        raise YarnSalesError(f"Sales order cannot be cancelled (is {so.status})")
    if any(_qty(l.qty_dispatched) > QZERO for l in so.lines):
        raise YarnSalesError("A sales order with dispatched quantity cannot be cancelled")
    so.status = YarnSOStatus.CANCELLED.value
    so.cancelled_by = cancelled_by
    so.cancelled_at = _now()
    db.flush()
    _audit(db, cancelled_by, "CANCEL", str(so.id), {"doc": "yarn_so"})
    return so


def so_outstanding(so: YarnSalesOrder) -> list[dict]:
    rows = []
    for l in so.lines:
        ordered, dispatched, cancelled = _qty(l.qty_ordered), _qty(l.qty_dispatched), _qty(l.qty_cancelled)
        outstanding = quantize(ordered - dispatched - cancelled, QTY_SCALE)
        rows.append({"line_id": str(l.id), "line_no": l.line_no, "yarn_id": str(l.yarn_id),
                     "warehouse_id": str(l.warehouse_id) if l.warehouse_id else None,
                     "ordered": ordered, "dispatched": dispatched, "cancelled": cancelled,
                     "outstanding": outstanding if outstanding > QZERO else QZERO})
    return rows


def lot_availability(db: Session, company_id: uuid.UUID, yarn_id: uuid.UUID,
                     warehouse_id: uuid.UUID | None = None) -> dict:
    """INFORMATIONAL per-lot on-hand yarn stock for a SKU, from the existing FIFO
    balance (shared with production). Preserves and displays the ORIGINAL supplier
    lot reference. NOT a reservation and NOT a guaranteed allocation."""
    _require_yarn(db, company_id, yarn_id)
    lots = db.execute(select(YarnLot).where(
        YarnLot.company_id == company_id, YarnLot.yarn_id == yarn_id)).scalars().all()
    out = []
    total_qty = QZERO
    total_value = MZERO
    for lot in lots:
        bal = inventory.balance(db, company_id=company_id, item_type=ItemType.YARN,
                                item_id=yarn_id, lot_id=lot.id, warehouse_id=warehouse_id)
        if _qty(bal["qty_on_hand"]) <= QZERO:
            continue
        total_qty = quantize(total_qty + _qty(bal["qty_on_hand"]), QTY_SCALE)
        total_value = quantize(total_value + _money(bal["value"]), MONEY_SCALE)
        out.append({"lot_id": str(lot.id), "internal_lot_no": lot.internal_lot_no,
                    "supplier_lot_no": lot.supplier_lot_no,
                    "warehouse_id": str(lot.warehouse_id) if lot.warehouse_id else None,
                    "on_hand_qty": bal["qty_on_hand"], "on_hand_value": bal["value"],
                    "unit_cost": lot.unit_cost})
    return {"yarn_id": str(yarn_id),
            "warehouse_id": str(warehouse_id) if warehouse_id else None,
            "total_on_hand_qty": total_qty, "total_on_hand_value": total_value,
            "lots": out, "informational_only": True, "is_reservation": False,
            "is_guaranteed_allocation": False}
