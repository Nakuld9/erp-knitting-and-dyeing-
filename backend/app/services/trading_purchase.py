"""Trading-fabric purchase order + goods receipt services (Stage 4B-2).

Two purchase routes share one GRN receipt + approval + FIFO + accounting path:
  * Route A — PO-based: Supplier -> Trading PO -> Trading GRN -> stock.
  * Route B — direct:   Supplier -> Supplier invoice / direct GRN -> stock.

Safety rules enforced here:
  * Only an APPROVED GRN creates trading stock (DRAFT/SUBMITTED/REJECTED/
    CANCELLED never do).
  * Approval is atomic and idempotent: it creates the FIFO layers AND posts the
    purchase voucher together, keyed on one posting_reference, so a retry or a
    double-approve never creates a second layer or voucher.
  * Posting is CA-gated: if the company's TRADING_GRN account slots are not
    configured, approval is BLOCKED (no stock, no voucher) — never a silent
    fall-back to chemical or manufactured-fabric accounts.
  * Strict trading-warehouse validation, company/factory isolation, PO
    outstanding-quantity control with authorised over-receipt, and supplier
    invoice duplicate protection.

No sales, dispatch, COGS, returns, adjustments, rolls, lots, batches, genealogy
or packing lists are implemented here.
"""
from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, QTY_SCALE, quantize
from app.models.audit import AuditLog
from app.models.core import Branch, Company
from app.models.masters import Supplier, TaxCode, UnitOfMeasure
from app.models.inventory import ItemType
from app.models.trading import (
    TradingFabricItem, TradingGRN, TradingGRNLine, TradingGRNSource, TradingGRNStatus,
    TradingPOStatus, TradingPurchaseOrder, TradingPurchaseOrderLine,
)
from app.services import inventory
from app.services.accounting import posting_engine as pe
from app.services.accounting.posting_rules import (
    TRADING_EVENT_SLOTS, resolve_account, trading_rules_configured,
)
from app.services.tax import compute_gst, resolve_intra_state

OVER_RECEIVE_PERMISSION = "trading.over_receive"
MZERO = quantize(0, MONEY_SCALE)
QZERO = quantize(0, QTY_SCALE)


class TradingPurchaseError(ValueError):
    """Invalid trading PO/GRN request (bad reference, missing field, bad state)."""


class TradingDuplicateInvoice(ValueError):
    """The supplier invoice has already been received for this company/supplier."""


class TradingOverReceiptError(ValueError):
    """A receipt would exceed the PO outstanding quantity without authorisation."""


class TradingPostingBlocked(ValueError):
    """The company's trading GRN account mappings are not configured, so the GRN
    cannot be approved/posted yet. The GRN stays safely submitted for retry."""


def _money(v):
    return quantize(Decimal(str(v or 0)), MONEY_SCALE)


def _qty(v):
    return quantize(Decimal(str(v or 0)), QTY_SCALE)


def _norm_invoice(s: str | None) -> str:
    return (s or "").strip().upper()


def _now():
    return datetime.now(timezone.utc)


def _audit(db: Session, actor, action: str, entity_id: str, detail: dict) -> None:
    db.add(AuditLog(actor_id=actor, action=action, entity="trading_purchase",
                    entity_id=entity_id, detail=json.dumps(detail, default=str)))


# --------------------------- reference validation ---------------------------

def _require_supplier(db, company_id, supplier_id) -> Supplier:
    s = db.get(Supplier, supplier_id)
    if s is None or s.company_id != company_id:
        raise TradingPurchaseError("Supplier is not in the active company")
    return s


def _require_factory(db, company_id, factory_id) -> None:
    if factory_id is None:
        return
    b = db.get(Branch, factory_id)
    if b is None or b.company_id != company_id:
        raise TradingPurchaseError("Factory is not in the active company")


def _require_item(db, company_id, item_id, *, must_be_active: bool) -> TradingFabricItem:
    it = db.get(TradingFabricItem, item_id)
    if it is None or it.company_id != company_id:
        raise TradingPurchaseError("Trading SKU is not in the active company")
    if must_be_active and not it.is_active:
        raise TradingPurchaseError(f"Trading SKU '{it.code}' is inactive")
    return it


def _require_refs(db, company_id, *, uom_id, tax_code_id) -> None:
    if uom_id is not None:
        u = db.get(UnitOfMeasure, uom_id)
        if u is None or u.company_id != company_id:
            raise TradingPurchaseError("Unit of measure is not in the active company")
    if tax_code_id is not None:
        t = db.get(TaxCode, tax_code_id)
        if t is None or t.company_id != company_id:
            raise TradingPurchaseError("Tax code is not in the active company")


# ============================ purchase orders ===============================

def create_trading_po(db: Session, company_id: uuid.UUID, *, supplier_id: uuid.UUID, number: str,
                      order_date: date, lines: list[dict], expected_date: date | None = None,
                      factory_id: uuid.UUID | None = None, remarks: str | None = None,
                      created_by: uuid.UUID | None = None) -> TradingPurchaseOrder:
    if not (number or "").strip():
        raise TradingPurchaseError("PO number is required")
    if not lines:
        raise TradingPurchaseError("A purchase order needs at least one line")
    _require_supplier(db, company_id, supplier_id)
    _require_factory(db, company_id, factory_id)

    po = TradingPurchaseOrder(
        company_id=company_id, supplier_id=supplier_id, factory_id=factory_id,
        number=number.strip(), order_date=order_date, expected_date=expected_date,
        status=TradingPOStatus.DRAFT.value, remarks=remarks, created_by=created_by)
    db.add(po)
    db.flush()
    for i, ln in enumerate(lines, start=1):
        _require_item(db, company_id, ln["trading_item_id"], must_be_active=True)
        _require_refs(db, company_id, uom_id=ln.get("uom_id"), tax_code_id=ln.get("tax_code_id"))
        qty = _qty(ln.get("qty_ordered"))
        if qty <= QZERO:
            raise TradingPurchaseError("Ordered quantity must be positive")
        po.lines.append(TradingPurchaseOrderLine(
            line_no=i, trading_item_id=ln["trading_item_id"], qty_ordered=qty,
            qty_received=QZERO, qty_cancelled=QZERO, uom_id=ln.get("uom_id"),
            rate=_money(ln.get("rate")), tax_code_id=ln.get("tax_code_id"), created_by=created_by))
    db.flush()
    _audit(db, created_by, "CREATE", str(po.id), {"doc": "trading_po", "number": po.number})
    return po


def get_trading_po(db: Session, company_id: uuid.UUID, po_id: uuid.UUID) -> TradingPurchaseOrder:
    po = db.get(TradingPurchaseOrder, po_id)
    if po is None or po.company_id != company_id:
        raise TradingPurchaseError("Purchase order not found in the active company")
    return po


def approve_trading_po(db: Session, company_id: uuid.UUID, po_id: uuid.UUID, *,
                       approved_by: uuid.UUID | None = None) -> TradingPurchaseOrder:
    po = get_trading_po(db, company_id, po_id)
    if po.status != TradingPOStatus.DRAFT.value:
        raise TradingPurchaseError(f"Only a DRAFT purchase order can be approved (is {po.status})")
    po.status = TradingPOStatus.APPROVED.value
    po.approved_by = approved_by
    po.approved_at = _now()
    db.flush()
    _audit(db, approved_by, "APPROVE", str(po.id), {"doc": "trading_po"})
    return po


def cancel_trading_po(db: Session, company_id: uuid.UUID, po_id: uuid.UUID, *,
                      cancelled_by: uuid.UUID | None = None) -> TradingPurchaseOrder:
    po = get_trading_po(db, company_id, po_id)
    if po.status in (TradingPOStatus.CANCELLED.value, TradingPOStatus.COMPLETED.value):
        raise TradingPurchaseError(f"Purchase order cannot be cancelled (is {po.status})")
    if any(_qty(l.qty_received) > QZERO for l in po.lines):
        raise TradingPurchaseError("A purchase order with receipts cannot be cancelled")
    po.status = TradingPOStatus.CANCELLED.value
    po.cancelled_by = cancelled_by
    po.cancelled_at = _now()
    db.flush()
    _audit(db, cancelled_by, "CANCEL", str(po.id), {"doc": "trading_po"})
    return po


def po_outstanding(po: TradingPurchaseOrder) -> list[dict]:
    rows = []
    for l in po.lines:
        ordered = _qty(l.qty_ordered)
        received = _qty(l.qty_received)
        cancelled = _qty(l.qty_cancelled)
        outstanding = quantize(ordered - received - cancelled, QTY_SCALE)
        rows.append({"line_id": str(l.id), "line_no": l.line_no,
                     "trading_item_id": str(l.trading_item_id), "ordered": ordered,
                     "received": received, "cancelled": cancelled,
                     "outstanding": outstanding if outstanding > QZERO else QZERO})
    return rows


def _refresh_po_status(db: Session, po: TradingPurchaseOrder) -> None:
    if po.status in (TradingPOStatus.CANCELLED.value, TradingPOStatus.DRAFT.value):
        return
    total_ordered = sum((_qty(l.qty_ordered) for l in po.lines), QZERO)
    total_received = sum((_qty(l.qty_received) for l in po.lines), QZERO)
    if total_received <= QZERO:
        po.status = TradingPOStatus.APPROVED.value
    elif total_received >= total_ordered:
        po.status = TradingPOStatus.COMPLETED.value
    else:
        po.status = TradingPOStatus.PARTIAL.value
    db.flush()


# ============================ goods receipts ================================

def _intra(db, company_id, supplier: Supplier) -> bool:
    company = db.get(Company, company_id)
    return resolve_intra_state(company.state_code, supplier.state_code if supplier else None,
                               context="Trading purchase")


def create_trading_grn(db: Session, company_id: uuid.UUID, *, supplier_id: uuid.UUID, number: str,
                       grn_date: date, supplier_invoice_no: str, lines: list[dict],
                       supplier_invoice_date: date | None = None,
                       purchase_order_id: uuid.UUID | None = None, factory_id: uuid.UUID | None = None,
                       remarks: str | None = None, over_receive_authorized: bool = False,
                       over_receive_reason: str | None = None, authorized_by: uuid.UUID | None = None,
                       created_by: uuid.UUID | None = None) -> TradingGRN:
    """Create a DRAFT trading GRN for either route. Computes and freezes GST; does
    NOT create stock (only approval does)."""
    if not (number or "").strip():
        raise TradingPurchaseError("GRN number is required")
    inv = _norm_invoice(supplier_invoice_no)
    if not inv:
        raise TradingPurchaseError("Supplier invoice number is required")
    if not lines:
        raise TradingPurchaseError("A GRN needs at least one line")
    supplier = _require_supplier(db, company_id, supplier_id)
    _require_factory(db, company_id, factory_id)

    # Duplicate supplier-invoice pre-check (the DB unique constraint is the
    # authoritative guard for concurrent submissions).
    dup = db.execute(select(TradingGRN).where(
        TradingGRN.company_id == company_id, TradingGRN.supplier_id == supplier_id,
        TradingGRN.supplier_invoice_no == inv)).scalar_one_or_none()
    if dup is not None:
        raise TradingDuplicateInvoice(
            f"Supplier invoice '{supplier_invoice_no}' already received for this supplier")

    po = None
    if purchase_order_id is not None:
        po = get_trading_po(db, company_id, purchase_order_id)
        if po.supplier_id != supplier_id:
            raise TradingPurchaseError("GRN supplier does not match the purchase order supplier")
        if po.status not in (TradingPOStatus.APPROVED.value, TradingPOStatus.PARTIAL.value):
            raise TradingPurchaseError(f"Purchase order is not open for receipt (is {po.status})")
        if factory_id is not None and po.factory_id is not None and po.factory_id != factory_id:
            raise TradingPurchaseError("GRN factory does not match the purchase order factory")

    intra = _intra(db, company_id, supplier)
    source = TradingGRNSource.PO.value if po is not None else TradingGRNSource.DIRECT.value

    grn = TradingGRN(
        company_id=company_id, supplier_id=supplier_id, factory_id=factory_id,
        purchase_order_id=purchase_order_id, source_type=source, number=number.strip(),
        grn_date=grn_date, supplier_invoice_no=inv, supplier_invoice_date=supplier_invoice_date,
        status=TradingGRNStatus.DRAFT.value, remarks=remarks, created_by=created_by)
    db.add(grn)
    db.flush()

    doc_taxable = doc_tax = doc_total = MZERO
    over_needed = False
    # Safeguard 3: the GRN uses the ACTUAL supplier-invoice tax code/rate; the PO's
    # own tax details are preserved untouched. Any PO-vs-invoice difference is
    # captured here so it is visible and auditable (never silently overwritten).
    tax_diffs: list[dict] = []
    for i, ln in enumerate(lines, start=1):
        item = _require_item(db, company_id, ln["trading_item_id"], must_be_active=True)
        _require_refs(db, company_id, uom_id=ln.get("uom_id"), tax_code_id=ln.get("tax_code_id"))
        # Warehouse must be a TRADING warehouse of this company/factory (strict).
        from app.services.trading import assert_trading_warehouse, TradingWarehouseError
        try:
            assert_trading_warehouse(db, company_id, ln.get("warehouse_id"), factory_id=factory_id)
        except TradingWarehouseError as exc:
            raise TradingPurchaseError(str(exc))
        qty = _qty(ln.get("qty_received"))
        if qty <= QZERO:
            raise TradingPurchaseError("Received quantity must be positive")
        rejected = _qty(ln.get("qty_rejected"))
        if rejected < QZERO:
            raise TradingPurchaseError("Rejected quantity cannot be negative")
        rate = _money(ln.get("rate"))

        po_line_id = ln.get("purchase_order_line_id")
        if po is not None:
            if po_line_id is None:
                raise TradingPurchaseError("Each PO-based GRN line must reference a PO line")
            po_line = db.get(TradingPurchaseOrderLine, po_line_id)
            if po_line is None or po_line.order_id != po.id:
                raise TradingPurchaseError("PO line does not belong to the referenced purchase order")
            if po_line.trading_item_id != item.id:
                raise TradingPurchaseError("GRN line SKU does not match the PO line SKU")
            outstanding = quantize(_qty(po_line.qty_ordered) - _qty(po_line.qty_received)
                                   - _qty(po_line.qty_cancelled), QTY_SCALE)
            if qty > outstanding:
                over_needed = True
            # Record (do not block) a tax-code or rate difference from the PO line.
            if po_line.tax_code_id != ln.get("tax_code_id") or _money(po_line.rate) != rate:
                tax_diffs.append({
                    "line_no": i,
                    "po_tax_code_id": str(po_line.tax_code_id) if po_line.tax_code_id else None,
                    "invoice_tax_code_id": str(ln.get("tax_code_id")) if ln.get("tax_code_id") else None,
                    "po_rate": str(_money(po_line.rate)), "invoice_rate": str(rate)})
        elif po_line_id is not None:
            raise TradingPurchaseError("A direct GRN line cannot reference a PO line")

        tax_code = db.get(TaxCode, ln.get("tax_code_id")) if ln.get("tax_code_id") else None
        taxable = quantize(qty * rate, MONEY_SCALE)
        g = compute_gst(tax_code, taxable, intra_state=intra)
        line_tax = quantize(g["cgst"] + g["sgst"] + g["igst"], MONEY_SCALE)
        total = quantize(taxable + line_tax, MONEY_SCALE)
        grn.lines.append(TradingGRNLine(
            line_no=i, trading_item_id=item.id, purchase_order_line_id=po_line_id,
            warehouse_id=ln["warehouse_id"], qty_received=qty, qty_rejected=rejected,
            uom_id=ln.get("uom_id"), rate=rate, tax_code_id=ln.get("tax_code_id"),
            taxable_value=taxable, cgst=g["cgst"], sgst=g["sgst"], igst=g["igst"],
            total_value=total, remarks=ln.get("remarks"), created_by=created_by))
        doc_taxable = quantize(doc_taxable + taxable, MONEY_SCALE)
        doc_tax = quantize(doc_tax + line_tax, MONEY_SCALE)
        doc_total = quantize(doc_total + total, MONEY_SCALE)

    if over_needed:
        if not over_receive_authorized:
            raise TradingOverReceiptError(
                "Receipt exceeds the PO outstanding quantity; over-receipt requires the "
                "'trading.over_receive' permission and a reason")
        if not (over_receive_reason or "").strip():
            raise TradingOverReceiptError("Over-receipt requires a reason")
        grn.is_over_received = True
        grn.over_receive_reason = over_receive_reason.strip()
        grn.over_received_by = authorized_by
        grn.over_received_at = _now()

    grn.taxable_value = doc_taxable
    grn.tax_amount = doc_tax
    grn.total_value = doc_total
    db.flush()
    _audit(db, created_by, "CREATE", str(grn.id),
           {"doc": "trading_grn", "number": grn.number, "source": source,
            "invoice": inv, "over_received": grn.is_over_received,
            "po_tax_differences": tax_diffs})
    return grn


def get_trading_grn(db: Session, company_id: uuid.UUID, grn_id: uuid.UUID) -> TradingGRN:
    grn = db.get(TradingGRN, grn_id)
    if grn is None or grn.company_id != company_id:
        raise TradingPurchaseError("GRN not found in the active company")
    return grn


def submit_trading_grn(db: Session, company_id: uuid.UUID, grn_id: uuid.UUID, *,
                       submitted_by: uuid.UUID | None = None) -> TradingGRN:
    grn = get_trading_grn(db, company_id, grn_id)
    if grn.status != TradingGRNStatus.DRAFT.value:
        raise TradingPurchaseError(f"Only a DRAFT GRN can be submitted (is {grn.status})")
    grn.status = TradingGRNStatus.SUBMITTED.value
    grn.submitted_by = submitted_by
    grn.submitted_at = _now()
    db.flush()
    _audit(db, submitted_by, "SUBMIT", str(grn.id), {"doc": "trading_grn"})
    return grn


def reject_trading_grn(db: Session, company_id: uuid.UUID, grn_id: uuid.UUID, *, reason: str,
                       rejected_by: uuid.UUID | None = None) -> TradingGRN:
    grn = get_trading_grn(db, company_id, grn_id)
    if grn.status not in (TradingGRNStatus.DRAFT.value, TradingGRNStatus.SUBMITTED.value):
        raise TradingPurchaseError(f"Only a DRAFT/SUBMITTED GRN can be rejected (is {grn.status})")
    if not (reason or "").strip():
        raise TradingPurchaseError("A rejection reason is required")
    grn.status = TradingGRNStatus.REJECTED.value
    grn.reject_reason = reason.strip()
    grn.rejected_by = rejected_by
    grn.rejected_at = _now()
    db.flush()
    _audit(db, rejected_by, "REJECT", str(grn.id), {"doc": "trading_grn", "reason": reason.strip()})
    return grn


def cancel_trading_grn(db: Session, company_id: uuid.UUID, grn_id: uuid.UUID, *,
                       cancelled_by: uuid.UUID | None = None) -> TradingGRN:
    grn = get_trading_grn(db, company_id, grn_id)
    if grn.status == TradingGRNStatus.APPROVED.value:
        raise TradingPurchaseError("An approved GRN cannot be cancelled; use a reversal")
    if grn.status in (TradingGRNStatus.REJECTED.value, TradingGRNStatus.CANCELLED.value):
        raise TradingPurchaseError(f"GRN cannot be cancelled (is {grn.status})")
    grn.status = TradingGRNStatus.CANCELLED.value
    grn.cancelled_by = cancelled_by
    grn.cancelled_at = _now()
    db.flush()
    _audit(db, cancelled_by, "CANCEL", str(grn.id), {"doc": "trading_grn"})
    return grn


def _grn_mappings_ready(db: Session, company_id: uuid.UUID) -> bool:
    """True only when EVERY TRADING_GRN slot has a configured company account."""
    status = trading_rules_configured(db, company_id).get("TRADING_GRN", {})
    required = TRADING_EVENT_SLOTS["TRADING_GRN"]
    return bool(status) and all(status.get(slot, False) for slot in required)


def approve_trading_grn(db: Session, company_id: uuid.UUID, grn_id: uuid.UUID, *,
                        approved_by: uuid.UUID | None = None) -> dict:
    """Approve a SUBMITTED GRN: create trading FIFO stock AND post the purchase
    voucher, atomically and idempotently. Blocks (no stock, no voucher) when the
    company's TRADING_GRN account mappings are not configured.
    """
    grn = get_trading_grn(db, company_id, grn_id)
    ref = grn.posting_reference or f"TGRN:{grn.number}"

    # Idempotent: an already-approved GRN returns its existing voucher; no second
    # layer or voucher is ever created.
    if grn.status == TradingGRNStatus.APPROVED.value:
        return {"already_approved": True, "grn_id": str(grn.id),
                "voucher": pe.find_existing(db, company_id, ref)}
    if grn.status != TradingGRNStatus.SUBMITTED.value:
        raise TradingPurchaseError(f"Only a SUBMITTED GRN can be approved (is {grn.status})")

    # CA gate: never post without configured trading accounts, and never fall back
    # to chemical/manufactured accounts.
    if not _grn_mappings_ready(db, company_id):
        _audit(db, approved_by, "POST_BLOCKED", str(grn.id),
               {"doc": "trading_grn", "reason": "trading GRN account mappings not configured"})
        raise TradingPostingBlocked(
            "Trading GRN account mappings are not configured for this company. Configure the "
            "TRADING_GRN posting rules (inventory, input CGST/SGST/IGST, payable) before approval. "
            "The GRN remains submitted and can be approved once configured.")

    # Lock the PO (if any) so concurrent approvals cannot both consume the same
    # outstanding quantity.
    po = None
    if grn.purchase_order_id is not None:
        stmt = select(TradingPurchaseOrder).where(TradingPurchaseOrder.id == grn.purchase_order_id)
        bind = db.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update()
        po = db.execute(stmt).scalar_one_or_none()

    # Re-validate PO outstanding at approval time (authoritative; catches the
    # concurrent-approval race). Over-receipt is allowed only if authorised at entry.
    if po is not None:
        for line in grn.lines:
            if line.purchase_order_line_id is None:
                continue
            po_line = db.get(TradingPurchaseOrderLine, line.purchase_order_line_id)
            outstanding = quantize(_qty(po_line.qty_ordered) - _qty(po_line.qty_received)
                                   - _qty(po_line.qty_cancelled), QTY_SCALE)
            if _qty(line.qty_received) > outstanding and not grn.is_over_received:
                raise TradingOverReceiptError(
                    f"Receipt of {line.qty_received} exceeds PO outstanding {outstanding}; "
                    "authorise over-receipt to proceed")

    # Post exactly once (idempotent on ref). If a voucher already exists for this
    # ref, the stock was already created — do not create it again.
    voucher = pe.find_existing(db, company_id, ref)
    if voucher is None:
        voucher = _post_grn(db, grn, ref, approved_by)

    # Roll PO received quantities forward and refresh PO status.
    if po is not None:
        for line in grn.lines:
            if line.purchase_order_line_id is None:
                continue
            po_line = db.get(TradingPurchaseOrderLine, line.purchase_order_line_id)
            po_line.qty_received = quantize(_qty(po_line.qty_received) + _qty(line.qty_received), QTY_SCALE)
        _refresh_po_status(db, po)

    grn.status = TradingGRNStatus.APPROVED.value
    grn.posting_reference = ref
    grn.approved_by = approved_by
    grn.approved_at = _now()
    db.flush()
    _audit(db, approved_by, "APPROVE", str(grn.id),
           {"doc": "trading_grn", "voucher": getattr(voucher, "number", None)})
    return {"already_approved": False, "grn_id": str(grn.id), "voucher": voucher}


def _post_grn(db: Session, grn: TradingGRN, ref: str, approved_by):
    """Create the trading FIFO layers and the GST purchase voucher (one voucher)."""
    company_id = grn.company_id
    debits_by_account: dict[str, Decimal] = defaultdict(lambda: MZERO)
    cgst = sgst = igst = MZERO

    for line in grn.lines:
        inv_acct = resolve_account(db, company_id, "TRADING_GRN", "inventory")
        debits_by_account[inv_acct] = quantize(debits_by_account[inv_acct] + _money(line.taxable_value), MONEY_SCALE)
        # Only the accepted quantity enters stock; rejected quantity is recorded on
        # the line but never stocked (no rejection-valuation policy is invented).
        inventory.receive(
            db, company_id=company_id, item_type=ItemType.TRADING_FABRIC, item_id=line.trading_item_id,
            qty=line.qty_received, unit_cost=line.rate, warehouse_id=line.warehouse_id,
            event_type="GRN", source_module="trading_grn", source_id=str(grn.id), posting_reference=ref)
        cgst = quantize(cgst + _money(line.cgst), MONEY_SCALE)
        sgst = quantize(sgst + _money(line.sgst), MONEY_SCALE)
        igst = quantize(igst + _money(line.igst), MONEY_SCALE)

    lines = [pe.PostingLine(account_code=acct, debit=amt, narration="Trading GRN inventory")
             for acct, amt in debits_by_account.items()]
    if cgst > 0:
        lines.append(pe.PostingLine(account_code=resolve_account(db, company_id, "TRADING_GRN", "input_cgst"),
                                    debit=cgst, narration="Input CGST"))
    if sgst > 0:
        lines.append(pe.PostingLine(account_code=resolve_account(db, company_id, "TRADING_GRN", "input_sgst"),
                                    debit=sgst, narration="Input SGST"))
    if igst > 0:
        lines.append(pe.PostingLine(account_code=resolve_account(db, company_id, "TRADING_GRN", "input_igst"),
                                    debit=igst, narration="Input IGST"))
    gross = sum((l.debit for l in lines), MZERO)
    lines.append(pe.PostingLine(
        account_code=resolve_account(db, company_id, "TRADING_GRN", "payable"),
        credit=gross, narration="Supplier payable", party_type="supplier", party_id=grn.supplier_id))

    return pe.post_voucher(db, pe.PostingRequest(
        company_id=company_id, voucher_type_code="PI", voucher_date=grn.grn_date,
        posting_reference=ref, narration=f"Trading GRN {grn.number}",
        source_module="trading_grn", source_id=str(grn.id), created_by=approved_by, lines=lines))


def list_trading_grns(db: Session, company_id: uuid.UUID, *, status: str | None = None,
                      supplier_id: uuid.UUID | None = None) -> list[TradingGRN]:
    stmt = select(TradingGRN).where(TradingGRN.company_id == company_id)
    if status:
        stmt = stmt.where(TradingGRN.status == status)
    if supplier_id:
        stmt = stmt.where(TradingGRN.supplier_id == supplier_id)
    return db.execute(stmt.order_by(TradingGRN.grn_date.desc())).scalars().all()


def list_trading_pos(db: Session, company_id: uuid.UUID, *, status: str | None = None) -> list[TradingPurchaseOrder]:
    stmt = select(TradingPurchaseOrder).where(TradingPurchaseOrder.company_id == company_id)
    if status:
        stmt = stmt.where(TradingPurchaseOrder.status == status)
    return db.execute(stmt.order_by(TradingPurchaseOrder.order_date.desc())).scalars().all()
