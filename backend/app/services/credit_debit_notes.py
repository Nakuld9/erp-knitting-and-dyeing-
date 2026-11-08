"""Credit and debit notes.

Sales credit note (to a customer): a financial adjustment that reduces the
receivable and reverses the output GST originally charged — for a discount,
rate difference or short shipment. It does not restock goods.

Purchase debit note (to a supplier): a goods return that relieves chemical
stock FIFO, reduces the supplier payable and reverses the input GST claimed.
The taxable value is the actual cost relieved from stock, so the books stay
reconciled with the inventory sub-ledger.

Both post balanced GST vouchers and are idempotent on the note number.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.commercial import CreditNote, DebitNote, DocStatus
from app.models.core import Company
from app.models.inventory import ItemType
from app.models.masters import Chemical, Customer, Supplier, TaxCode
from app.services import inventory
from app.services.accounting import posting_engine as pe
from app.services.accounting.posting_rules import resolve_account
from app.services.tax import compute_gst

MZERO = quantize(0, MONEY_SCALE)


def _intra(company: Company, state_code: str | None) -> bool:
    if company.state_code and state_code:
        return company.state_code == state_code
    return True


# ------------------------------ sales credit note ---------------------------

def post_sales_credit_note(db: Session, note: CreditNote, *, created_by: uuid.UUID | None = None):
    ref = note.posting_reference or f"CN:{note.number}"
    existing = pe.find_existing(db, note.company_id, ref)
    if existing is not None:
        return existing

    company = db.get(Company, note.company_id)
    customer = db.get(Customer, note.customer_id)
    intra = _intra(company, customer.state_code if customer else None)

    taxable = MZERO
    cgst = sgst = igst = MZERO
    for line in note.lines:
        amount = quantize(Decimal(str(line.qty)) * Decimal(str(line.rate)), MONEY_SCALE)
        line.amount = amount
        taxable += amount
        tc = db.get(TaxCode, line.tax_code_id) if line.tax_code_id else None
        g = compute_gst(tc, amount, intra_state=intra)
        cgst += g["cgst"]; sgst += g["sgst"]; igst += g["igst"]

    tax_total = cgst + sgst + igst
    gross = taxable + tax_total

    # Mirror of a sales invoice: debit back sales + output tax, credit the
    # customer's receivable.
    lines = [pe.PostingLine(resolve_account(db, note.company_id, "SALES_CREDIT_NOTE", "sales"),
                            debit=taxable, narration="Sales credit note")]
    if cgst > 0:
        lines.append(pe.PostingLine(resolve_account(db, note.company_id, "SALES_CREDIT_NOTE", "output_cgst"), debit=cgst, narration="Output CGST reversal"))
    if sgst > 0:
        lines.append(pe.PostingLine(resolve_account(db, note.company_id, "SALES_CREDIT_NOTE", "output_sgst"), debit=sgst, narration="Output SGST reversal"))
    if igst > 0:
        lines.append(pe.PostingLine(resolve_account(db, note.company_id, "SALES_CREDIT_NOTE", "output_igst"), debit=igst, narration="Output IGST reversal"))
    lines.append(pe.PostingLine(resolve_account(db, note.company_id, "SALES_CREDIT_NOTE", "receivable"),
                                credit=gross, narration="Customer receivable",
                                party_type="customer", party_id=note.customer_id))

    voucher = pe.post_voucher(db, pe.PostingRequest(
        company_id=note.company_id, voucher_type_code="CN", voucher_date=note.note_date,
        posting_reference=ref, narration=f"Credit note {note.number}",
        source_module="credit_note", source_id=str(note.id), created_by=created_by, lines=lines,
    ))
    note.taxable_value = taxable
    note.tax_amount = tax_total
    note.total_value = gross
    note.posting_reference = ref
    note.status = DocStatus.POSTED
    db.flush()
    return voucher


# ---------------------------- purchase debit note ---------------------------

def post_purchase_debit_note(db: Session, note: DebitNote, *, created_by: uuid.UUID | None = None):
    ref = note.posting_reference or f"DN:{note.number}"
    existing = pe.find_existing(db, note.company_id, ref)
    if existing is not None:
        return existing

    company = db.get(Company, note.company_id)
    supplier = db.get(Supplier, note.supplier_id)
    intra = _intra(company, supplier.state_code if supplier else None)

    credits_by_account: dict[str, Decimal] = defaultdict(lambda: MZERO)
    cgst = sgst = igst = MZERO
    taxable_total = MZERO

    for line in note.lines:
        # Relieve the returned stock FIFO (from the named lot when given) and
        # take the actual cost back out of the inventory account.
        res = inventory.consume(
            db, company_id=note.company_id, item_type=ItemType.CHEMICAL, item_id=line.chemical_id,
            qty=line.qty, lot_id=line.chemical_lot_id, event_type="PURCHASE_RETURN",
            source_module="debit_note", source_id=str(note.id), posting_reference=ref,
        )
        cost = res["total_cost"]
        line.cost_value = cost
        chem = db.get(Chemical, line.chemical_id)
        inv_acct = "1090" if (chem and chem.is_dye) else resolve_account(
            db, note.company_id, "PURCHASE_DEBIT_NOTE", "inventory")
        credits_by_account[inv_acct] = quantize(credits_by_account[inv_acct] + cost, MONEY_SCALE)
        taxable_total += cost
        tc = db.get(TaxCode, line.tax_code_id) if line.tax_code_id else None
        g = compute_gst(tc, cost, intra_state=intra)
        cgst += g["cgst"]; sgst += g["sgst"]; igst += g["igst"]

    tax_total = cgst + sgst + igst
    gross = taxable_total + tax_total

    # Mirror of a GRN: debit the supplier payable, credit back inventory and
    # the input tax claimed.
    lines = [pe.PostingLine(resolve_account(db, note.company_id, "PURCHASE_DEBIT_NOTE", "payable"),
                            debit=gross, narration="Supplier payable",
                            party_type="supplier", party_id=note.supplier_id)]
    for acct, amt in credits_by_account.items():
        lines.append(pe.PostingLine(acct, credit=amt, narration="Stock returned"))
    if cgst > 0:
        lines.append(pe.PostingLine(resolve_account(db, note.company_id, "PURCHASE_DEBIT_NOTE", "input_cgst"), credit=cgst, narration="Input CGST reversal"))
    if sgst > 0:
        lines.append(pe.PostingLine(resolve_account(db, note.company_id, "PURCHASE_DEBIT_NOTE", "input_sgst"), credit=sgst, narration="Input SGST reversal"))
    if igst > 0:
        lines.append(pe.PostingLine(resolve_account(db, note.company_id, "PURCHASE_DEBIT_NOTE", "input_igst"), credit=igst, narration="Input IGST reversal"))

    voucher = pe.post_voucher(db, pe.PostingRequest(
        company_id=note.company_id, voucher_type_code="DN", voucher_date=note.note_date,
        posting_reference=ref, narration=f"Debit note {note.number}",
        source_module="debit_note", source_id=str(note.id), created_by=created_by, lines=lines,
    ))
    note.taxable_value = taxable_total
    note.tax_amount = tax_total
    note.total_value = gross
    note.posting_reference = ref
    note.status = DocStatus.POSTED
    db.flush()
    return voucher
