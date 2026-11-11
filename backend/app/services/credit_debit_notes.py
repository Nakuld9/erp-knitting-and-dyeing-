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

from sqlalchemy import select
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
    from app.services.tax import resolve_intra_state
    return resolve_intra_state(company.state_code, state_code, context="Credit/debit note")


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

def restock_returned_rolls(db: Session, company_id: uuid.UUID, roll_ids, *, reference: str,
                           vdate, created_by: uuid.UUID | None = None):
    """Take delivered rolls back into stock at their dispatch cost and reverse
    the COGS booked on delivery (Dr fabric inventory / Cr COGS)."""
    from collections import defaultdict
    from app.models.commercial import DeliveryNoteLine
    from app.models.manufacturing import FabricRoll, RollKind
    ref = f"RETCOGS:{reference}"
    if pe.find_existing(db, company_id, ref) is not None:
        return pe.find_existing(db, company_id, ref)

    debits: dict[str, Decimal] = defaultdict(lambda: MZERO)
    total = MZERO
    for rid in roll_ids:
        roll = db.get(FabricRoll, rid)
        if roll is None or roll.company_id != company_id:
            raise RuntimeError(f"Roll {rid} not found")
        if roll.delivery_note_id is None:
            raise RuntimeError(f"Roll {roll.roll_no} was not dispatched")
        dl = db.execute(select(DeliveryNoteLine).where(
            DeliveryNoteLine.fabric_roll_id == roll.id).order_by(DeliveryNoteLine.created_at.desc())
        ).scalars().first()
        cost = quantize(Decimal(str(dl.cogs_value)) if dl else 0, MONEY_SCALE)
        qty = quantize(Decimal(str(dl.qty_kg)) if dl else Decimal(str(roll.weight_kg)), MONEY_SCALE)
        unit = quantize(cost / qty, MONEY_SCALE) if qty > 0 else MZERO
        inventory.receive(db, company_id=company_id, item_type=ItemType.FABRIC, item_id=roll.id,
                          qty=qty, unit_cost=unit, warehouse_id=roll.warehouse_id,
                          event_type="SALES_RETURN", source_module="sales_return",
                          source_id=reference, posting_reference=ref)
        roll.delivery_note_id = None
        roll.status = "ACTIVE"
        kind = roll.kind.value if isinstance(roll.kind, RollKind) else roll.kind
        slot = "greige" if kind == RollKind.GREIGE.value else "fg"
        acct = resolve_account(db, company_id, "FABRIC_DELIVERY", slot)
        debits[acct] = quantize(debits[acct] + cost, MONEY_SCALE)
        total = quantize(total + cost, MONEY_SCALE)
    db.flush()
    if total <= 0:
        return None
    lines = [pe.PostingLine(a, debit=v, narration="Returned fabric back to stock") for a, v in debits.items()]
    lines.append(pe.PostingLine(resolve_account(db, company_id, "FABRIC_DELIVERY", "cogs"),
                                credit=total, narration="Reverse COGS on return"))
    return pe.post_voucher(db, pe.PostingRequest(
        company_id=company_id, voucher_type_code="SJ", voucher_date=vdate, posting_reference=ref,
        narration=f"Sales return restock {reference}", source_module="sales_return",
        source_id=reference, created_by=created_by, lines=lines))


def post_sales_return(db: Session, note: CreditNote, *, restock_roll_ids=None,
                      created_by: uuid.UUID | None = None):
    """A credit note that also takes returned goods back into stock: posts the
    financial credit note and, for any returned rolls, restocks them and
    reverses the COGS booked at delivery."""
    cn_voucher = post_sales_credit_note(db, note, created_by=created_by)
    cogs_voucher = None
    if restock_roll_ids:
        cogs_voucher = restock_returned_rolls(db, note.company_id, restock_roll_ids,
                                              reference=note.number, vdate=note.note_date,
                                              created_by=created_by)
    return {"credit_note_voucher": cn_voucher, "cogs_reversal_voucher": cogs_voucher}


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
