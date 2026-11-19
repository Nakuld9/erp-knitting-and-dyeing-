"""Direct yarn sales-invoice services (Stage 5B).

Approach C: reuse the shared ``SalesInvoice``/``SalesInvoiceLine`` register
additively with ``source="YARN"`` plus a quantity-level
``YarnDispatchInvoiceAllocation`` linking each invoice line to the yarn dispatch
line it bills (one invoice line == one dispatch line). The invoice line preserves
the ORIGINAL supplier yarn lot reference (``yarn_lot_id``) where the dispatch line
named a specific lot.

Creation/cancellation handle the DRAFT lifecycle (no accounting). Posting is a
CA-gated, fail-closed step: it recognises revenue + output GST + customer
receivable in ONE balanced voucher via YARN_SALES mappings — never the
manufactured (4010) or trading (4011) revenue account. Posted-invoice
cancellation / credit notes are intentionally NOT implemented (blocked until the
CA-approved policy is supplied); a posted invoice cannot be draft-cancelled.
It moves NO stock and never re-posts dispatch COGS.
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
from app.models.commercial import DocStatus, SalesInvoice, SalesInvoiceLine
from app.models.core import Branch, Company
from app.models.masters import Customer, TaxCode, UnitOfMeasure, YarnLot, YarnMaster
from app.models.yarn_sales import (
    YarnAllocationStatus, YarnDispatchInvoiceAllocation, YarnDispatchStatus,
    YarnSalesDispatch, YarnSalesDispatchLine,
)
from app.services.accounting import posting_engine as pe
from app.services.accounting.posting_engine import AccountNotPostableError, _get_postable_account
from app.services.accounting.posting_rules import YARN_EVENT_SLOTS, resolve_account
from app.services.tax import GstStateError, compute_gst, resolve_intra_state

MZERO = quantize(0, MONEY_SCALE)
QZERO = quantize(0, QTY_SCALE)
YARN_SOURCE = "YARN"


class YarnInvoiceError(ValueError):
    """An invalid yarn invoice request (bad reference, bad state, bad qty)."""


class YarnInvoiceInconsistent(ValueError):
    """The qty_invoiced counter disagrees with active allocation totals; fail
    safely rather than silently repair."""


class YarnInvoicePostingBlocked(ValueError):
    """Required YARN_SALES account mappings or GST inputs are missing/invalid, so
    posting is blocked (fail-closed). The invoice stays DRAFT; no voucher is
    created and no fall-back to the manufactured/trading revenue account occurs."""


def _money(v):
    return quantize(Decimal(str(v or 0)), MONEY_SCALE)


def _qty(v):
    return quantize(Decimal(str(v or 0)), QTY_SCALE)


def _now():
    return datetime.now(timezone.utc)


def _audit(db, actor, action, entity_id, detail):
    db.add(AuditLog(actor_id=actor, action=action, entity="yarn_sales_invoice",
                    entity_id=entity_id, detail=json.dumps(detail, default=str)))


def _active_allocated(db: Session, dispatch_line_id: uuid.UUID) -> Decimal:
    total = db.execute(select(func.coalesce(func.sum(YarnDispatchInvoiceAllocation.quantity_allocated), 0))
                       .where(YarnDispatchInvoiceAllocation.dispatch_line_id == dispatch_line_id,
                              YarnDispatchInvoiceAllocation.status == YarnAllocationStatus.ACTIVE.value)).scalar()
    return _qty(total)


def dispatch_line_uninvoiced(db: Session, line: YarnSalesDispatchLine) -> Decimal:
    """Authoritative uninvoiced qty = dispatch-line quantity - active allocations.
    Also reconciles the qty_invoiced counter; a mismatch raises (no silent repair)."""
    active = _active_allocated(db, line.id)
    counter = _qty(line.qty_invoiced)
    if counter != active:
        raise YarnInvoiceInconsistent(
            f"Dispatch line {line.id}: qty_invoiced {counter} != active allocations {active}")
    return quantize(_qty(line.quantity) - active, QTY_SCALE)


def _lock_row(db: Session, model, obj_id):
    stmt = select(model).where(model.id == obj_id)
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update()
    return db.execute(stmt).scalar_one_or_none()


# ------------------------------ create --------------------------------------

def create_yarn_invoice(db: Session, company_id: uuid.UUID, *, customer_id: uuid.UUID, number: str,
                        invoice_date: date, lines: list[dict], factory_id: uuid.UUID | None = None,
                        created_by: uuid.UUID | None = None) -> SalesInvoice:
    """Create a DRAFT yarn invoice from one or more APPROVED yarn dispatch lines.
    One invoice line per dispatch line. No posting, no stock, no COGS."""
    if not (number or "").strip():
        raise YarnInvoiceError("Invoice number is required")
    if not lines:
        raise YarnInvoiceError("An invoice needs at least one line")

    cust = db.get(Customer, customer_id)
    if cust is None or cust.company_id != company_id:
        raise YarnInvoiceError("Customer is not in the active company")
    if getattr(cust, "is_active", True) is False:
        raise YarnInvoiceError("Customer is inactive")
    if factory_id is not None:
        b = db.get(Branch, factory_id)
        if b is None or b.company_id != company_id:
            raise YarnInvoiceError("Factory is not in the active company")

    dup = db.execute(select(SalesInvoice).where(
        SalesInvoice.company_id == company_id, SalesInvoice.number == number.strip(),
        SalesInvoice.source == YARN_SOURCE)).scalar_one_or_none()
    if dup is not None:
        raise YarnInvoiceError(f"Yarn invoice number '{number}' already exists for this company")

    # Lock every referenced dispatch line in deterministic id order.
    dl_ids = sorted({ln.get("dispatch_line_id") for ln in lines}, key=lambda x: str(x))
    locked: dict[uuid.UUID, YarnSalesDispatchLine] = {}
    for dl_id in dl_ids:
        dl = _lock_row(db, YarnSalesDispatchLine, dl_id)
        if dl is None:
            raise YarnInvoiceError("Dispatch line not found")
        locked[dl_id] = dl

    inv = SalesInvoice(company_id=company_id, customer_id=customer_id, number=number.strip(),
                       invoice_date=invoice_date, status=DocStatus.DRAFT, source=YARN_SOURCE,
                       factory_id=factory_id, taxable_value=MZERO, tax_amount=MZERO,
                       total_value=MZERO, created_by=created_by)
    db.add(inv)
    db.flush()

    doc_taxable = MZERO
    requested: dict[uuid.UUID, Decimal] = {}
    for i, ln in enumerate(lines, start=1):
        dl = locked[ln.get("dispatch_line_id")]
        dispatch = db.get(YarnSalesDispatch, dl.dispatch_id)
        if dispatch is None or dispatch.company_id != company_id:
            raise YarnInvoiceError("Dispatch not found in the active company")
        if dispatch.status != YarnDispatchStatus.APPROVED.value:
            raise YarnInvoiceError("Dispatch is not APPROVED; only approved dispatches can be invoiced")
        if dispatch.customer_id != customer_id:
            raise YarnInvoiceError("Dispatch customer does not match the invoice customer")
        if factory_id is not None and dispatch.factory_id is not None and dispatch.factory_id != factory_id:
            raise YarnInvoiceError("Dispatch factory does not match the invoice factory")

        yarn = db.get(YarnMaster, dl.yarn_id)
        if yarn is None or yarn.company_id != company_id:
            raise YarnInvoiceError("Yarn SKU is not in the active company")

        tax_code_id = ln.get("tax_code_id")
        if tax_code_id is not None:
            tc = db.get(TaxCode, tax_code_id)
            if tc is None or tc.company_id != company_id:
                raise YarnInvoiceError("Tax code is not in the active company")
        uom_id = ln.get("uom_id", dl.uom_id)
        if uom_id is not None:
            u = db.get(UnitOfMeasure, uom_id)
            if u is None or u.company_id != company_id:
                raise YarnInvoiceError("Unit of measure is not in the active company")

        qty = _qty(ln.get("quantity"))
        if qty <= QZERO:
            raise YarnInvoiceError("Invoice quantity must be positive")
        rate = _money(ln.get("rate"))
        discount = _money(ln.get("discount"))
        if discount < MZERO:
            raise YarnInvoiceError("Discount cannot be negative")

        uninvoiced = dispatch_line_uninvoiced(db, dl) - requested.get(dl.id, QZERO)
        if qty > uninvoiced:
            raise YarnInvoiceError("Invoice quantity exceeds the dispatch line's uninvoiced quantity")
        requested[dl.id] = requested.get(dl.id, QZERO) + qty

        amount = quantize(qty * rate - discount, MONEY_SCALE)
        if amount < MZERO:
            raise YarnInvoiceError("Discount cannot exceed the line value")
        inv_line = SalesInvoiceLine(invoice_id=inv.id, line_no=i, description=ln.get("description"),
                                    fabric_roll_id=None, yarn_id=yarn.id, yarn_lot_id=dl.lot_id,
                                    qty=qty, rate=rate, discount=discount, amount=amount,
                                    tax_code_id=tax_code_id, uom_id=uom_id, created_by=created_by)
        inv.lines.append(inv_line)
        db.flush()
        db.add(YarnDispatchInvoiceAllocation(
            company_id=company_id, invoice_id=inv.id, invoice_line_id=inv_line.id,
            dispatch_id=dispatch.id, dispatch_line_id=dl.id, quantity_allocated=qty,
            uom_id=uom_id, status=YarnAllocationStatus.ACTIVE.value, created_by=created_by))
        dl.qty_invoiced = quantize(_qty(dl.qty_invoiced) + qty, QTY_SCALE)
        db.flush()
        doc_taxable = quantize(doc_taxable + amount, MONEY_SCALE)

    inv.taxable_value = doc_taxable
    inv.tax_amount = MZERO
    inv.total_value = doc_taxable
    db.flush()
    _audit(db, created_by, "CREATE", str(inv.id),
           {"doc": "yarn_sales_invoice", "number": inv.number, "source": YARN_SOURCE,
            "lines": len(inv.lines), "taxable_value": str(doc_taxable)})
    return inv


def get_yarn_invoice(db: Session, company_id: uuid.UUID, invoice_id: uuid.UUID) -> SalesInvoice:
    inv = db.get(SalesInvoice, invoice_id)
    if inv is None or inv.company_id != company_id or inv.source != YARN_SOURCE:
        raise YarnInvoiceError("Yarn invoice not found in the active company")
    return inv


def list_yarn_invoices(db: Session, company_id: uuid.UUID, *, customer_id: uuid.UUID | None = None,
                       status: str | None = None, limit: int = 100, offset: int = 0):
    stmt = select(SalesInvoice).where(SalesInvoice.company_id == company_id,
                                      SalesInvoice.source == YARN_SOURCE)
    if customer_id:
        stmt = stmt.where(SalesInvoice.customer_id == customer_id)
    if status:
        stmt = stmt.where(SalesInvoice.status == status)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = db.execute(stmt.order_by(SalesInvoice.invoice_date.desc(), SalesInvoice.id)
                      .limit(max(1, min(limit, 200))).offset(max(0, offset))).scalars().all()
    return rows, total


def cancel_yarn_invoice(db: Session, company_id: uuid.UUID, invoice_id: uuid.UUID, *,
                        cancelled_by: uuid.UUID | None = None) -> SalesInvoice:
    """Cancel a DRAFT yarn invoice: RELEASE its allocations (evidence retained) and
    decrement each dispatch line's qty_invoiced, atomically. DRAFT only — a POSTED
    invoice cannot be draft-cancelled (credit-note/reversal is a blocked CA policy)."""
    inv = get_yarn_invoice(db, company_id, invoice_id)
    status = inv.status.value if hasattr(inv.status, "value") else inv.status
    if status != DocStatus.DRAFT.value:
        raise YarnInvoiceError(f"Only a DRAFT yarn invoice can be cancelled (is {status})")
    allocs = db.execute(select(YarnDispatchInvoiceAllocation).where(
        YarnDispatchInvoiceAllocation.invoice_id == inv.id,
        YarnDispatchInvoiceAllocation.status == YarnAllocationStatus.ACTIVE.value)).scalars().all()
    for a in allocs:
        dl = _lock_row(db, YarnSalesDispatchLine, a.dispatch_line_id)
        if dl is not None:
            dl.qty_invoiced = quantize(_qty(dl.qty_invoiced) - _qty(a.quantity_allocated), QTY_SCALE)
            if _qty(dl.qty_invoiced) < QZERO:
                dl.qty_invoiced = QZERO
        a.status = YarnAllocationStatus.RELEASED.value
        a.released_by = cancelled_by
        a.released_at = _now()
    inv.status = DocStatus.CANCELLED
    db.flush()
    _audit(db, cancelled_by, "CANCEL", str(inv.id),
           {"doc": "yarn_sales_invoice", "released_allocations": len(allocs)})
    return inv


def invoice_lines_view(db: Session, inv: SalesInvoice) -> list[dict]:
    out = []
    for l in inv.lines:
        lot = db.get(YarnLot, l.yarn_lot_id) if l.yarn_lot_id else None
        out.append({"id": str(l.id), "line_no": l.line_no,
                    "yarn_id": str(l.yarn_id) if l.yarn_id else None,
                    "yarn_lot_id": str(l.yarn_lot_id) if l.yarn_lot_id else None,
                    "internal_lot_no": lot.internal_lot_no if lot else None,
                    "supplier_lot_no": lot.supplier_lot_no if lot else None,
                    "qty": str(l.qty), "rate": str(l.rate), "discount": str(l.discount),
                    "amount": str(l.amount), "tax_code_id": str(l.tax_code_id) if l.tax_code_id else None,
                    "uom_id": str(l.uom_id) if l.uom_id else None, "description": l.description})
    return out


# ===========================================================================
# Posting — CA-gated revenue + GST + receivable, no COGS, no stock
# ===========================================================================

_SALES_SLOTS = YARN_EVENT_SLOTS["YARN_SALES"]


def _validate_sales_mappings(db: Session, company_id: uuid.UUID) -> dict:
    """Fail-closed CA gate: resolve + validate every YARN_SALES account (exists,
    same company, postable, active) BEFORE posting. No fall-back to the
    manufactured (4010) or trading (4011) revenue account."""
    resolved = {}
    for slot in _SALES_SLOTS:
        try:
            code = resolve_account(db, company_id, "YARN_SALES", slot)
        except KeyError:
            raise YarnInvoicePostingBlocked(
                f"YARN_SALES '{slot}' account mapping is not configured for this company")
        try:
            _get_postable_account(db, company_id, code)
        except AccountNotPostableError as exc:
            raise YarnInvoicePostingBlocked(
                f"YARN_SALES '{slot}' account is not valid for posting: {exc}")
        resolved[slot] = code
    return resolved


def post_yarn_invoice(db: Session, company_id: uuid.UUID, invoice_id: uuid.UUID, *,
                      posted_by: uuid.UUID | None = None) -> dict:
    """Post a DRAFT yarn invoice: revenue + output GST + customer receivable in ONE
    balanced voucher, atomically and idempotently. Blocks (no voucher, invoice stays
    DRAFT) when YARN_SALES mappings or the GST place-of-supply inputs are
    missing/invalid. Never consumes stock or re-posts dispatch COGS."""
    inv = get_yarn_invoice(db, company_id, invoice_id)
    ref = inv.posting_reference or f"YSI:{inv.number}"
    status = inv.status.value if hasattr(inv.status, "value") else inv.status
    existing = pe.find_existing(db, company_id, ref)

    if status == DocStatus.POSTED.value:
        if existing is None:
            raise YarnInvoiceInconsistent(
                "Invoice is POSTED but its revenue voucher is missing; reconciliation required")
        return {"already_posted": True, "invoice_id": str(inv.id), "voucher": existing}
    if status != DocStatus.DRAFT.value:
        raise YarnInvoiceError(f"Only a DRAFT yarn invoice can be posted (is {status})")
    if existing is not None:
        raise YarnInvoiceInconsistent(
            "A revenue voucher already exists for this invoice reference; refusing to post again")

    accts = _validate_sales_mappings(db, company_id)

    company = db.get(Company, company_id)
    customer = db.get(Customer, inv.customer_id)
    if customer is None or customer.company_id != company_id:
        raise YarnInvoiceError("Customer is not in the active company")
    try:
        intra = resolve_intra_state(company.state_code, customer.state_code,
                                    context=f"Yarn invoice {inv.number}")
    except GstStateError as exc:
        raise YarnInvoicePostingBlocked(str(exc))

    taxable = cgst = sgst = igst = MZERO
    for line in inv.lines:
        line_taxable = _money(line.amount)
        tax_code = db.get(TaxCode, line.tax_code_id) if line.tax_code_id else None
        g = compute_gst(tax_code, line_taxable, intra_state=intra)
        taxable = quantize(taxable + line_taxable, MONEY_SCALE)
        cgst = quantize(cgst + g["cgst"], MONEY_SCALE)
        sgst = quantize(sgst + g["sgst"], MONEY_SCALE)
        igst = quantize(igst + g["igst"], MONEY_SCALE)
    tax_total = quantize(cgst + sgst + igst, MONEY_SCALE)
    gross = quantize(taxable + tax_total, MONEY_SCALE)

    lines = [pe.PostingLine(accts["receivable"], debit=gross, narration="Customer receivable",
                            party_type="customer", party_id=inv.customer_id),
             pe.PostingLine(accts["sales"], credit=taxable, narration="Yarn sales")]
    if cgst > 0:
        lines.append(pe.PostingLine(accts["output_cgst"], credit=cgst, narration="Output CGST"))
    if sgst > 0:
        lines.append(pe.PostingLine(accts["output_sgst"], credit=sgst, narration="Output SGST"))
    if igst > 0:
        lines.append(pe.PostingLine(accts["output_igst"], credit=igst, narration="Output IGST"))

    voucher = pe.post_voucher(db, pe.PostingRequest(
        company_id=company_id, voucher_type_code="SI", voucher_date=inv.invoice_date,
        posting_reference=ref, narration=f"Yarn sales invoice {inv.number}",
        source_module="yarn_sales_invoice", source_id=str(inv.id), created_by=posted_by, lines=lines))

    inv.taxable_value = taxable
    inv.tax_amount = tax_total
    inv.total_value = gross
    inv.posting_reference = ref
    inv.status = DocStatus.POSTED
    db.flush()
    _audit(db, posted_by, "POST", str(inv.id),
           {"doc": "yarn_sales_invoice", "voucher": getattr(voucher, "number", None),
            "taxable": str(taxable), "tax": str(tax_total), "total": str(gross)})
    return {"already_posted": False, "invoice_id": str(inv.id), "voucher": voucher,
            "taxable_value": taxable, "tax_amount": tax_total, "total_value": gross}
