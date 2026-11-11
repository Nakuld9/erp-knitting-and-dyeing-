"""Sales invoice posting: build a GST sales voucher from invoice lines."""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.commercial import DocStatus, SalesInvoice
from app.models.core import Company
from app.models.masters import Customer, TaxCode
from app.services.accounting import posting_engine as pe
from app.services.accounting.posting_rules import resolve_account
from app.services.tax import compute_gst

MZERO = quantize(0, MONEY_SCALE)


def post_sales_invoice(db: Session, invoice: SalesInvoice, *, created_by: uuid.UUID | None = None):
    ref = invoice.posting_reference or f"SI:{invoice.number}"
    existing = pe.find_existing(db, invoice.company_id, ref)
    if existing is not None:
        return existing

    company = db.get(Company, invoice.company_id)
    customer = db.get(Customer, invoice.customer_id)
    from app.services.tax import resolve_intra_state
    intra = resolve_intra_state(company.state_code, customer.state_code if customer else None,
                                context=f"Sales invoice {invoice.number}")

    taxable = MZERO
    cgst = sgst = igst = MZERO
    for line in invoice.lines:
        amount = quantize(Decimal(str(line.qty)) * Decimal(str(line.rate)), MONEY_SCALE)
        line.amount = amount
        taxable += amount
        tax_code = db.get(TaxCode, line.tax_code_id) if line.tax_code_id else None
        g = compute_gst(tax_code, amount, intra_state=intra)
        cgst += g["cgst"]; sgst += g["sgst"]; igst += g["igst"]

    tax_total = cgst + sgst + igst
    gross = taxable + tax_total

    lines = [pe.PostingLine(resolve_account(db, invoice.company_id, "SALES_INVOICE", "receivable"),
                            debit=gross, narration="Customer receivable",
                            party_type="customer", party_id=invoice.customer_id),
             pe.PostingLine(resolve_account(db, invoice.company_id, "SALES_INVOICE", "sales"),
                            credit=taxable, narration="Fabric sales")]
    if cgst > 0:
        lines.append(pe.PostingLine(resolve_account(db, invoice.company_id, "SALES_INVOICE", "output_cgst"), credit=cgst, narration="Output CGST"))
    if sgst > 0:
        lines.append(pe.PostingLine(resolve_account(db, invoice.company_id, "SALES_INVOICE", "output_sgst"), credit=sgst, narration="Output SGST"))
    if igst > 0:
        lines.append(pe.PostingLine(resolve_account(db, invoice.company_id, "SALES_INVOICE", "output_igst"), credit=igst, narration="Output IGST"))

    voucher = pe.post_voucher(db, pe.PostingRequest(
        company_id=invoice.company_id, voucher_type_code="SI", voucher_date=invoice.invoice_date,
        posting_reference=ref, narration=f"Sales invoice {invoice.number}",
        source_module="sales", source_id=str(invoice.id), created_by=created_by, lines=lines,
    ))
    invoice.taxable_value = taxable
    invoice.tax_amount = tax_total
    invoice.total_value = gross
    invoice.posting_reference = ref
    invoice.status = DocStatus.POSTED
    db.flush()
    return voucher
