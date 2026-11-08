"""Purchase- and sales-order lifecycle.

Ties the commercial documents together so an order is tracked from placement
through fulfilment:

  Purchase:  PO (draft -> confirmed) -> GRN(s) matched to PO lines
             -> PO auto-rolls to PARTIAL / COMPLETED on received quantity.
  Sales:     SO (draft -> confirmed) -> delivery note(s) dispatch finished rolls
             (FIFO stock-out at actual cost + COGS voucher)
             -> SO auto-rolls to PARTIAL / COMPLETED on delivered quantity;
             the revenue invoice follows the dispatch.

All quantity roll-ups are computed from the child documents, so status can
never drift from the posted receipts / deliveries.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, QTY_SCALE, quantize
from app.models.commercial import (
    DeliveryNote,
    DeliveryNoteLine,
    DocStatus,
    GRNLine,
    PurchaseOrder,
    PurchaseOrderLine,
    SalesOrder,
    SalesOrderLine,
)
from app.models.inventory import ItemType
from app.models.manufacturing import FabricRoll, RollKind
from app.services import inventory
from app.services.accounting import posting_engine as pe
from app.services.accounting.posting_engine import fiscal_year_for
from app.services.accounting.posting_rules import resolve_account
from app.services.sequences import next_document_number

QZERO = quantize(0, QTY_SCALE)
MZERO = quantize(0, MONEY_SCALE)


class LifecycleError(Exception):
    """A lifecycle rule was violated (bad transition, double-dispatch, ...)."""


# ============================ purchase orders ===============================

def create_purchase_order(
    db: Session, *, company_id: uuid.UUID, supplier_id: uuid.UUID,
    number: str, order_date, lines: list[dict], created_by: uuid.UUID | None = None,
) -> PurchaseOrder:
    po = PurchaseOrder(company_id=company_id, supplier_id=supplier_id, number=number,
                       order_date=order_date, status=DocStatus.DRAFT, created_by=created_by)
    for i, ln in enumerate(lines, start=1):
        po.lines.append(PurchaseOrderLine(
            line_no=i, chemical_id=ln.get("chemical_id"), description=ln.get("description"),
            qty=Decimal(str(ln["qty"])), rate=Decimal(str(ln.get("rate", 0))),
            tax_code_id=ln.get("tax_code_id"),
        ))
    db.add(po)
    db.flush()
    return po


def confirm_purchase_order(db: Session, po: PurchaseOrder) -> PurchaseOrder:
    if po.status != DocStatus.DRAFT:
        raise LifecycleError(f"Purchase order {po.number} is {po.status}, not DRAFT")
    po.status = DocStatus.CONFIRMED
    db.flush()
    return po


def po_received_map(db: Session, po: PurchaseOrder) -> dict[uuid.UUID, Decimal]:
    """Received quantity per PO line, summed from all matched GRN lines."""
    line_ids = [ln.id for ln in po.lines]
    received: dict[uuid.UUID, Decimal] = defaultdict(lambda: QZERO)
    if not line_ids:
        return received
    rows = db.execute(
        select(GRNLine.purchase_order_line_id, GRNLine.qty).where(
            GRNLine.purchase_order_line_id.in_(line_ids)
        )
    ).all()
    for pol_id, qty in rows:
        received[pol_id] = quantize(received[pol_id] + Decimal(str(qty)), QTY_SCALE)
    return received


def refresh_po_status(db: Session, po: PurchaseOrder) -> PurchaseOrder:
    """Roll the PO to PARTIAL / COMPLETED from matched receipts."""
    if po.status in (DocStatus.CANCELLED, DocStatus.DRAFT):
        return po
    received = po_received_map(db, po)
    any_received = False
    all_complete = True
    for ln in po.lines:
        ordered = quantize(Decimal(str(ln.qty)), QTY_SCALE)
        got = received.get(ln.id, QZERO)
        if got > QZERO:
            any_received = True
        if got < ordered:
            all_complete = False
    if all_complete and po.lines:
        po.status = DocStatus.COMPLETED
    elif any_received:
        po.status = DocStatus.PARTIAL
    else:
        po.status = DocStatus.CONFIRMED
    db.flush()
    return po


# ============================= sales orders =================================

def create_sales_order(
    db: Session, *, company_id: uuid.UUID, customer_id: uuid.UUID, number: str,
    order_date, delivery_date=None, lines: list[dict], created_by: uuid.UUID | None = None,
) -> SalesOrder:
    so = SalesOrder(company_id=company_id, customer_id=customer_id, number=number,
                    order_date=order_date, delivery_date=delivery_date,
                    status=DocStatus.DRAFT, created_by=created_by)
    for i, ln in enumerate(lines, start=1):
        so.lines.append(SalesOrderLine(
            line_no=i, fabric_type_id=ln.get("fabric_type_id"), shade_id=ln.get("shade_id"),
            description=ln.get("description"), gsm=ln.get("gsm"), width_cm=ln.get("width_cm"),
            qty_kg=Decimal(str(ln.get("qty_kg", 0))), qty_m=ln.get("qty_m"),
            rate=Decimal(str(ln.get("rate", 0))), tax_code_id=ln.get("tax_code_id"),
        ))
    db.add(so)
    db.flush()
    return so


def so_gross_value(db: Session, so: SalesOrder) -> Decimal:
    """Order value including GST, for credit-limit checks."""
    from app.models.core import Company
    from app.models.masters import Customer, TaxCode
    from app.services.tax import compute_gst
    company = db.get(Company, so.company_id)
    customer = db.get(Customer, so.customer_id)
    intra = (company.state_code == customer.state_code) if (company and customer
             and company.state_code and customer.state_code) else True
    gross = quantize(Decimal("0"), MONEY_SCALE)
    for ln in so.lines:
        amount = quantize(Decimal(str(ln.qty_kg)) * Decimal(str(ln.rate)), MONEY_SCALE)
        tc = db.get(TaxCode, ln.tax_code_id) if ln.tax_code_id else None
        g = compute_gst(tc, amount, intra_state=intra)
        gross = quantize(gross + amount + g["cgst"] + g["sgst"] + g["igst"], MONEY_SCALE)
    return gross


def confirm_sales_order(db: Session, so: SalesOrder) -> SalesOrder:
    if so.status != DocStatus.DRAFT:
        raise LifecycleError(f"Sales order {so.number} is {so.status}, not DRAFT")
    # Credit control: block if confirming would push the customer past their limit.
    from app.models.masters import Customer
    from app.services.accounting import statements
    customer = db.get(Customer, so.customer_id)
    limit = quantize(Decimal(str(customer.credit_limit or 0)), MONEY_SCALE) if customer else MZERO
    if limit > 0:
        outstanding = statements.customer_outstanding(db, so.company_id, so.customer_id)
        exposure = quantize(outstanding + so_gross_value(db, so), MONEY_SCALE)
        if exposure > limit:
            raise LifecycleError(
                f"Credit limit exceeded for {customer.name}: outstanding {outstanding} + "
                f"order {so_gross_value(db, so)} = {exposure} > limit {limit}")
    so.status = DocStatus.CONFIRMED
    db.flush()
    return so


def so_ordered_kg(so: SalesOrder) -> Decimal:
    return quantize(sum((Decimal(str(ln.qty_kg)) for ln in so.lines), Decimal("0")), QTY_SCALE)


def so_delivered_kg(db: Session, so: SalesOrder) -> Decimal:
    rows = db.execute(
        select(DeliveryNote.total_qty_kg).where(
            DeliveryNote.sales_order_id == so.id,
            DeliveryNote.status == DocStatus.POSTED,
        )
    ).scalars().all()
    return quantize(sum((Decimal(str(q)) for q in rows), Decimal("0")), QTY_SCALE)


def refresh_so_status(db: Session, so: SalesOrder) -> SalesOrder:
    if so.status in (DocStatus.CANCELLED, DocStatus.DRAFT):
        return so
    ordered = so_ordered_kg(so)
    delivered = so_delivered_kg(db, so)
    if delivered <= QZERO:
        so.status = DocStatus.CONFIRMED
    elif ordered > QZERO and delivered >= ordered:
        so.status = DocStatus.COMPLETED
    else:
        so.status = DocStatus.PARTIAL
    db.flush()
    return so


# ============================= dispatch / COGS ==============================

def _delivery_credit_slot(roll: FabricRoll) -> str:
    kind = roll.kind.value if isinstance(roll.kind, RollKind) else roll.kind
    return "greige" if kind == RollKind.GREIGE.value else "fg"


def create_delivery(
    db: Session, *, company_id: uuid.UUID, customer_id: uuid.UUID,
    sales_order_id: uuid.UUID | None, number: str, delivery_date,
    roll_ids: list[uuid.UUID], created_by: uuid.UUID | None = None,
) -> dict:
    """Dispatch finished rolls: FIFO stock-out at actual cost + COGS voucher.

    Idempotent on the delivery-note number. A roll already linked to a delivery
    note cannot be dispatched again.
    """
    ref = f"DN:{number}"
    existing = pe.find_existing(db, company_id, ref)
    if existing is not None:
        dn = db.execute(select(DeliveryNote).where(
            DeliveryNote.company_id == company_id, DeliveryNote.posting_reference == ref
        )).scalar_one_or_none()
        return {"already_posted": True, "delivery_id": str(dn.id) if dn else None,
                "voucher": existing}

    if not roll_ids:
        raise LifecycleError("A delivery needs at least one roll")

    rolls: list[FabricRoll] = []
    seen: set[uuid.UUID] = set()
    for rid in roll_ids:
        if rid in seen:
            raise LifecycleError(f"Roll {rid} listed twice")
        seen.add(rid)
        roll = db.get(FabricRoll, rid)
        if roll is None or roll.company_id != company_id:
            raise LifecycleError(f"Roll {rid} not found")
        if roll.delivery_note_id is not None:
            raise LifecycleError(f"Roll {roll.roll_no} is already dispatched")
        rolls.append(roll)

    dn = DeliveryNote(company_id=company_id, customer_id=customer_id,
                      sales_order_id=sales_order_id, number=number,
                      delivery_date=delivery_date, status=DocStatus.DRAFT, created_by=created_by)
    db.add(dn)
    db.flush()

    credits_by_account: dict[str, Decimal] = defaultdict(lambda: MZERO)
    total_cogs = MZERO
    total_qty = QZERO
    for i, roll in enumerate(rolls, start=1):
        qty = quantize(Decimal(str(roll.weight_kg)), QTY_SCALE)
        res = inventory.consume(
            db, company_id=company_id, item_type=ItemType.FABRIC, item_id=roll.id,
            qty=qty, event_type="DISPATCH", source_module="delivery",
            source_id=str(dn.id), posting_reference=ref,
        )
        cost = res["total_cost"]
        acct = resolve_account(db, company_id, "FABRIC_DELIVERY", _delivery_credit_slot(roll))
        credits_by_account[acct] = quantize(credits_by_account[acct] + cost, MONEY_SCALE)
        total_cogs = quantize(total_cogs + cost, MONEY_SCALE)
        total_qty = quantize(total_qty + qty, QTY_SCALE)
        roll.delivery_note_id = dn.id
        dn.lines.append(DeliveryNoteLine(line_no=i, fabric_roll_id=roll.id,
                                         qty_kg=qty, cogs_value=cost))
    db.flush()

    lines = [pe.PostingLine(resolve_account(db, company_id, "FABRIC_DELIVERY", "cogs"),
                            debit=total_cogs, narration="Cost of goods sold")]
    for acct, amt in credits_by_account.items():
        lines.append(pe.PostingLine(acct, credit=amt, narration="Fabric dispatched"))

    voucher = pe.post_voucher(db, pe.PostingRequest(
        company_id=company_id, voucher_type_code="SJ", voucher_date=delivery_date,
        posting_reference=ref, narration=f"Delivery {number}",
        source_module="delivery", source_id=str(dn.id), created_by=created_by, lines=lines,
    ))

    dn.total_qty_kg = total_qty
    dn.cogs_value = total_cogs
    dn.posting_reference = ref
    dn.status = DocStatus.POSTED
    db.flush()

    if sales_order_id:
        so = db.get(SalesOrder, sales_order_id)
        if so is not None and so.company_id == company_id:
            refresh_so_status(db, so)

    return {"delivery_id": str(dn.id), "number": dn.number, "voucher": voucher,
            "total_qty_kg": total_qty, "cogs_value": total_cogs, "rolls": len(rolls)}


def auto_delivery_number(db: Session, company_id: uuid.UUID, delivery_date) -> str:
    return next_document_number(
        db, company_id=company_id, doc_type="DELIVERY",
        fiscal_year=fiscal_year_for(delivery_date), prefix="DC-",
    )
