"""Sales GST Invoice and Sales Delivery Challan documents (Stage 3).

Both are rendered by the Stage-2 engine from EXISTING posted data — the sales
invoice / delivery note, their lines, the customer, tax codes, fabric rolls and
their dyeing/grey traceability, and any e-invoice / e-way bill. Building a document
is strictly READ-ONLY: it posts no voucher, creates no invoice/challan and changes
no stock. The GST split reuses ``app.services.tax`` so the printed figures equal
the posted figures.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.commercial import (
    DeliveryNote, DeliveryNoteLine, DocStatus, SalesInvoice, SalesOrder,
)
from app.models.core import Company
from app.models.dyeing_cost import DyeingJob, DyeingOutput
from app.models.einvoice import EInvoice
from app.models.ewaybill import EWayBill
from app.models.fabric_bom import FabricBom, FabricBomVersion, ProductionOrder
from app.models.manufacturing import DyeBatch, FabricRoll
from app.models.masters import BankAccount, Customer, FabricType, ShadeCode, TaxCode, Warehouse
from app.services.printing.document import (
    DocumentColumn, DocumentParty, PrintDocument, SignatureBlock,
)
from app.services.printing.numbering import build_letterhead
from app.services.printing.words import amount_in_words

MONEY = Decimal("0.01")


class DocumentError(ValueError):
    pass


def _money(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(MONEY)


def _fmt(v) -> str:
    return f"{_money(v):,.2f}"


def _qty(v) -> str:
    return f"{Decimal(str(v or 0)):.3f}"


def _primary_bank(db: Session, company_id) -> BankAccount | None:
    return db.execute(select(BankAccount).where(
        BankAccount.company_id == company_id, BankAccount.is_active.is_(True))).scalars().first()


def _so_number(db: Session, so_id) -> str | None:
    if not so_id:
        return None
    so = db.get(SalesOrder, so_id)
    return so.number if so else None


# --------------------------------------------------------------------------- #
# Sales GST Invoice
# --------------------------------------------------------------------------- #

def build_sales_invoice_document(db: Session, company_id: uuid.UUID, invoice_id: uuid.UUID, *,
                                 reprint_count: int = 0, is_preview: bool = False) -> PrintDocument:
    inv = db.get(SalesInvoice, invoice_id)
    if inv is None or inv.company_id != company_id:
        raise DocumentError("Invoice not found in the active company")
    company = db.get(Company, company_id)
    customer = db.get(Customer, inv.customer_id)
    lh = build_letterhead(db, company_id)

    # intra vs inter-state (reuse the posting logic; fail soft for the print)
    intra = None
    try:
        from app.services.tax import resolve_intra_state
        intra = resolve_intra_state(company.state_code if company else None,
                                    customer.state_code if customer else None)
    except Exception:
        intra = None
    from app.services.tax import compute_gst

    columns = [
        DocumentColumn("#", "sl", width=0.35),
        DocumentColumn("Description", "desc", width=2.6),
        DocumentColumn("HSN", "hsn", width=0.7),
        DocumentColumn("Qty", "qty", numeric=True),
        DocumentColumn("UOM", "uom", width=0.5),
        DocumentColumn("Rate", "rate", numeric=True),
        DocumentColumn("Taxable", "amount", numeric=True),
    ]
    rows = []
    cgst = sgst = igst = Decimal("0")
    taxable = Decimal("0")
    for i, line in enumerate(sorted(inv.lines, key=lambda x: x.line_no), start=1):
        tc = db.get(TaxCode, line.tax_code_id) if line.tax_code_id else None
        amount = _money(line.amount)
        taxable += amount
        if intra is not None:
            g = compute_gst(tc, amount, intra_state=intra)
            cgst += g["cgst"]; sgst += g["sgst"]; igst += g["igst"]
        rows.append({"sl": i, "desc": line.description or "Fabric",
                     "hsn": (tc.hsn_sac if tc else "") or "",
                     "qty": _qty(line.qty), "uom": "KG",
                     "rate": _fmt(line.rate), "amount": _fmt(amount)})

    tax_lines = [("Taxable value", _fmt(inv.taxable_value))]
    if intra is True:
        tax_lines += [("CGST", _fmt(cgst)), ("SGST", _fmt(sgst))]
    elif intra is False:
        tax_lines += [("IGST", _fmt(igst))]
    else:
        tax_lines += [("Tax", _fmt(inv.tax_amount))]
    grand = _money(inv.total_value)

    # customer party blocks (bill-to / ship-to default to the same address)
    cust_lines = []
    if customer:
        if customer.gstin: cust_lines.append(f"GSTIN: {customer.gstin}")
        if customer.state_code: cust_lines.append(f"State code: {customer.state_code}")
        if customer.address: cust_lines.append(customer.address)
    parties = [DocumentParty("Bill To / Ship To", customer.name if customer else "—", cust_lines)]

    refs = []
    so_no = _so_number(db, inv.sales_order_id)
    if so_no: refs.append(("Sales Order", so_no))
    refs.append(("Place of supply",
                 (customer.state_code if customer else "") or "—"))
    supply = "Intra-state (CGST+SGST)" if intra is True else ("Inter-state (IGST)" if intra is False else "—")
    refs.append(("Supply type", supply))
    # e-invoice IRN / e-way bill, if generated
    ein = db.execute(select(EInvoice).where(EInvoice.invoice_id == inv.id)).scalars().first()
    if ein and ein.irn:
        refs.append(("IRN", ein.irn))
        if ein.ack_no: refs.append(("Ack No", ein.ack_no))

    notes = [f"Amount chargeable (in words): {amount_in_words(grand, company.currency if company else 'INR')}"]
    bank = _primary_bank(db, company_id)
    if bank:
        bn = f"Bank: {bank.bank_name or bank.name}  A/c: {bank.account_number or '—'}  IFSC: {bank.ifsc or '—'}"
        notes.append(bn)

    return PrintDocument(
        title="TAX INVOICE",
        document_kind="GST Sales Invoice",
        letterhead=lh,
        number=inv.number,
        date=inv.invoice_date.strftime("%d-%b-%Y") if inv.invoice_date else "",
        parties=parties,
        columns=columns, rows=rows,
        tax_lines=tax_lines, totals=[("Grand Total", _fmt(grand))],
        references=refs,
        signatures=[SignatureBlock("Receiver's Signature"),
                    SignatureBlock("Authorised Signatory", for_line=f"For {lh.legal_name}")],
        notes=notes,
        footer_note="Computer-generated tax invoice — printing/reprinting posts no accounting entry.",
        is_preview=is_preview,
        is_cancelled=(DocStatus(inv.status) == DocStatus.CANCELLED) if inv.status else False,
        reprint_count=reprint_count,
    )


# --------------------------------------------------------------------------- #
# Sales Delivery Challan
# --------------------------------------------------------------------------- #

def _fabric_via_grey_bom(db: Session, job: DyeingJob | None) -> FabricType | None:
    """Fabric type of a dyed roll derived from its grey production order's BOM
    (dyed rolls inherit the grey fabric's type). Read-only."""
    if job is None or not job.grey_production_order_id:
        return None
    po = db.get(ProductionOrder, job.grey_production_order_id)
    if po is None:
        return None
    ver = db.get(FabricBomVersion, po.bom_version_id) if po.bom_version_id else None
    bom = db.get(FabricBom, ver.bom_id) if ver is not None else None
    if bom and bom.fabric_type_id:
        return db.get(FabricType, bom.fabric_type_id)
    return None


def _roll_trace(db: Session, roll: FabricRoll) -> dict:
    """Shade + dye lot + fabric type + GSM + warehouse for a roll, from its dyeing
    output/job (dyed-roll traceability) and the grey BOM, with dye-batch /
    output-warehouse fallbacks. Fully read-only."""
    fabric = db.get(FabricType, roll.fabric_type_id) if roll.fabric_type_id else None
    shade = dye_lot = None
    warehouse_id = roll.warehouse_id
    out = db.execute(select(DyeingOutput).where(DyeingOutput.dyed_item_id == roll.id)).scalars().first()
    job = None
    if out is not None:
        dye_lot = out.lot_code
        job = db.get(DyeingJob, out.job_id)
        sid = out.shade_id or (job.shade_id if job else None)
        if sid:
            sc = db.get(ShadeCode, sid)
            shade = sc.name if sc else None
        if not dye_lot and job:
            dye_lot = job.dye_lot
        if warehouse_id is None:
            warehouse_id = out.warehouse_id          # fall back to the output's warehouse
    if fabric is None:
        fabric = _fabric_via_grey_bom(db, job)        # derive from the grey fabric BOM
    if dye_lot is None and roll.dye_batch_id:
        b = db.get(DyeBatch, roll.dye_batch_id)
        dye_lot = getattr(b, "batch_no", None) or (str(roll.dye_batch_id)[:8] if roll.dye_batch_id else None)
    gsm = roll.gsm if roll.gsm is not None else (fabric.gsm if fabric else None)
    wh = None
    if warehouse_id:
        w = db.get(Warehouse, warehouse_id)
        wh = w.name if w else None
    return {"fabric": fabric.name if fabric else "Fabric",
            "gsm": (f"{Decimal(str(gsm)):.0f}" if gsm is not None else "—"),
            "shade": shade or "—", "dye_lot": dye_lot or "—", "warehouse": wh}


def build_delivery_challan_document(db: Session, company_id: uuid.UUID, delivery_id: uuid.UUID, *,
                                    reprint_count: int = 0, is_preview: bool = False) -> PrintDocument:
    dn = db.get(DeliveryNote, delivery_id)
    if dn is None or dn.company_id != company_id:
        raise DocumentError("Delivery challan not found in the active company")
    company = db.get(Company, company_id)
    customer = db.get(Customer, dn.customer_id)
    lh = build_letterhead(db, company_id)

    columns = [
        DocumentColumn("#", "sl", width=0.3),
        DocumentColumn("Roll No", "roll", width=1.0),
        DocumentColumn("Fabric", "fabric", width=1.6),
        DocumentColumn("GSM", "gsm", numeric=True, width=0.5),
        DocumentColumn("Shade", "shade", width=0.9),
        DocumentColumn("Dye Lot", "lot", width=0.9),
        DocumentColumn("Warehouse", "wh", width=0.9),
        DocumentColumn("Disp. Qty (kg)", "qty", numeric=True),
    ]
    rows = []
    warehouses = set()
    total = Decimal("0")
    for i, line in enumerate(sorted(dn.lines, key=lambda x: x.line_no), start=1):
        roll = db.get(FabricRoll, line.fabric_roll_id)
        tr = _roll_trace(db, roll) if roll else {"fabric": "—", "gsm": "—", "shade": "—",
                                                 "dye_lot": "—", "warehouse": None}
        wh = tr.get("warehouse")
        if wh: warehouses.add(wh)
        total += _money(line.qty_kg)       # ACTUAL dispatched qty, not the SO qty
        rows.append({"sl": i, "roll": roll.roll_no if roll else "—", "fabric": tr["fabric"],
                     "gsm": tr["gsm"], "shade": tr["shade"], "lot": tr["dye_lot"],
                     "wh": wh or "—", "qty": _qty(line.qty_kg)})

    cust_lines = []
    if customer:
        if customer.gstin: cust_lines.append(f"GSTIN: {customer.gstin}")
        if customer.state_code: cust_lines.append(f"State code: {customer.state_code}")
        if customer.address: cust_lines.append(customer.address)
    parties = [DocumentParty("Consignee", customer.name if customer else "—", cust_lines),
               DocumentParty("Dispatch From", lh.legal_name,
                             [", ".join(sorted(warehouses)) or "—"])]

    refs = []
    so_no = _so_number(db, dn.sales_order_id)
    if so_no: refs.append(("Sales Order", so_no))
    # invoice reference (same sales order), where applicable
    inv = None
    if dn.sales_order_id:
        inv = db.execute(select(SalesInvoice).where(
            SalesInvoice.company_id == company_id,
            SalesInvoice.sales_order_id == dn.sales_order_id)).scalars().first()
    if inv:
        refs.append(("Invoice", inv.number))
    # e-way bill (via the linked invoice), where available
    if inv:
        ewb = db.execute(select(EWayBill).where(EWayBill.invoice_id == inv.id)).scalars().first()
        if ewb:
            if ewb.ewb_no: refs.append(("E-Way Bill", ewb.ewb_no))
            if ewb.vehicle_no: refs.append(("Vehicle", ewb.vehicle_no))
            if ewb.transporter_id: refs.append(("Transporter", ewb.transporter_id))

    return PrintDocument(
        title="DELIVERY CHALLAN",
        document_kind="Sales dispatch — finished fabric",
        letterhead=lh,
        number=dn.number,
        date=dn.delivery_date.strftime("%d-%b-%Y") if dn.delivery_date else "",
        parties=parties,
        columns=columns, rows=rows,
        totals=[("Total dispatched (kg)", _qty(total)), ("Rolls", str(len(rows)))],
        references=refs,
        signatures=[SignatureBlock("Received by (Consignee)"),
                    SignatureBlock("Issued by", for_line=f"For {lh.legal_name}")],
        notes=["Quantities shown are the actual dispatched weights for this challan.",
               "Not a tax invoice — the GST tax invoice is a separate document."],
        footer_note="Computer-generated delivery challan — printing/reprinting changes no stock or ledger.",
        is_preview=is_preview,
        is_cancelled=(DocStatus(dn.status) == DocStatus.CANCELLED) if dn.status else False,
        reprint_count=reprint_count,
    )
