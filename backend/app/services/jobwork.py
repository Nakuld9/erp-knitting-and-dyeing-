"""Job-work workflow: create orders, dispatch, and post the money event.

Inward (we process others' material) bills a processing charge to the customer:
  Dr Receivable / Cr Job-Work Income / Cr Output GST.
Outward (we send our fabric to another dyer) books the charge they bill us:
  Dr Processing Charges / Dr Input GST / Cr the subcontractor (payable).

The counterparty's or our material is tracked only as a quantity (memo) — inward
material is never valued in our books, and our outward fabric stays our asset.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.core import Company
from app.models.jobwork import (
    JobWork,
    JobWorkDirection,
    JobWorkProcess,
    JobWorkStatus,
)
from app.models.masters import Customer, Supplier, TaxCode
from app.services.accounting import posting_engine as pe
from app.services.tax import compute_gst

MZERO = quantize(0, MONEY_SCALE)

# Income account per process for inward job work.
_INCOME = {JobWorkProcess.KNITTING: "4020", JobWorkProcess.DYEING: "4030"}
_INCOME_DEFAULT = "4040"                    # Processing Income
# Expense account per process for outward job work.
_EXPENSE = {JobWorkProcess.DYEING: "5090", JobWorkProcess.FINISHING: "5100"}
_EXPENSE_DEFAULT = "5110"                    # Job Work Expenses
RECEIVABLE = "1040"
PAYABLE = "2010"
OUT_CGST, OUT_SGST, OUT_IGST = "2041", "2042", "2043"
IN_CGST, IN_SGST, IN_IGST = "1121", "1122", "1123"


class JobWorkError(Exception):
    """An invalid job-work action."""


def _party(db: Session, jw: JobWork):
    model = Customer if jw.party_type == "customer" else Supplier
    return db.get(model, jw.party_id)


def create(db: Session, *, company_id: uuid.UUID, number: str, direction: str, process: str,
           party_id: uuid.UUID, order_date: date, qty_kg, rate, fabric_type_id=None,
           description: str | None = None, tax_code_id: uuid.UUID | None = None,
           created_by: uuid.UUID | None = None) -> JobWork:
    d = JobWorkDirection(direction)
    p = JobWorkProcess(process)
    if Decimal(str(qty_kg or 0)) <= 0:
        raise JobWorkError("Quantity (kg) must be positive")
    jw = JobWork(
        company_id=company_id, number=number, direction=d, process=p, order_date=order_date,
        party_type="customer" if d == JobWorkDirection.INWARD else "supplier", party_id=party_id,
        fabric_type_id=fabric_type_id, description=description,
        qty_kg=quantize(Decimal(str(qty_kg)), MONEY_SCALE), rate=quantize(Decimal(str(rate or 0)), MONEY_SCALE),
        tax_code_id=tax_code_id, status=JobWorkStatus.OPEN, created_by=created_by)
    db.add(jw)
    db.flush()
    return jw


def dispatch(db: Session, jw: JobWork) -> JobWork:
    """Outward only: mark the fabric as sent to the subcontractor."""
    if JobWorkDirection(jw.direction) != JobWorkDirection.OUTWARD:
        raise JobWorkError("Only outward job work is dispatched")
    if JobWorkStatus(jw.status) != JobWorkStatus.OPEN:
        raise JobWorkError(f"Job work is {jw.status}, not open")
    jw.status = JobWorkStatus.SENT
    db.flush()
    return jw


def _charge(jw: JobWork) -> Decimal:
    return quantize(Decimal(str(jw.qty_kg)) * Decimal(str(jw.rate)), MONEY_SCALE)


def bill(db: Session, jw: JobWork, *, created_by: uuid.UUID | None = None):
    """Post the money event (inward income or outward cost) and mark BILLED."""
    direction = JobWorkDirection(jw.direction)
    process = JobWorkProcess(jw.process)
    if JobWorkStatus(jw.status) == JobWorkStatus.BILLED:
        return pe.find_existing(db, jw.company_id, jw.posting_reference) if jw.posting_reference else None
    if JobWorkStatus(jw.status) == JobWorkStatus.CANCELLED:
        raise JobWorkError("Cancelled job work cannot be billed")

    company = db.get(Company, jw.company_id)
    party = _party(db, jw)
    intra = (company.state_code == getattr(party, "state_code", None)) if (
        company and company.state_code and getattr(party, "state_code", None)) else True
    taxable = _charge(jw)
    if taxable <= 0:
        raise JobWorkError("Processing charge must be positive (set a rate)")
    tax_code = db.get(TaxCode, jw.tax_code_id) if jw.tax_code_id else None
    g = compute_gst(tax_code, taxable, intra_state=intra)
    cgst, sgst, igst = g["cgst"], g["sgst"], g["igst"]
    tax_total = cgst + sgst + igst
    gross = taxable + tax_total

    ref = f"JW:{jw.number}"
    existing = pe.find_existing(db, jw.company_id, ref)
    if existing is not None:
        jw.status = JobWorkStatus.BILLED
        jw.posting_reference = ref
        return existing

    if direction == JobWorkDirection.INWARD:
        income = _INCOME.get(process, _INCOME_DEFAULT)
        lines = [pe.PostingLine(RECEIVABLE, debit=gross, narration="Job-work receivable",
                                party_type="customer", party_id=jw.party_id),
                 pe.PostingLine(income, credit=taxable, narration="Job-work income")]
        if cgst > 0: lines.append(pe.PostingLine(OUT_CGST, credit=cgst, narration="Output CGST"))
        if sgst > 0: lines.append(pe.PostingLine(OUT_SGST, credit=sgst, narration="Output SGST"))
        if igst > 0: lines.append(pe.PostingLine(OUT_IGST, credit=igst, narration="Output IGST"))
        vtype = "SI"
    else:
        expense = _EXPENSE.get(process, _EXPENSE_DEFAULT)
        lines = [pe.PostingLine(expense, debit=taxable, narration="Processing charges")]
        if cgst > 0: lines.append(pe.PostingLine(IN_CGST, debit=cgst, narration="Input CGST"))
        if sgst > 0: lines.append(pe.PostingLine(IN_SGST, debit=sgst, narration="Input SGST"))
        if igst > 0: lines.append(pe.PostingLine(IN_IGST, debit=igst, narration="Input IGST"))
        lines.append(pe.PostingLine(PAYABLE, credit=gross, narration="Subcontractor payable",
                                    party_type="supplier", party_id=jw.party_id))
        vtype = "PI"

    voucher = pe.post_voucher(db, pe.PostingRequest(
        company_id=jw.company_id, voucher_type_code=vtype, voucher_date=jw.order_date,
        posting_reference=ref, narration=f"Job work {jw.number} ({direction.value})",
        source_module="jobwork", source_id=str(jw.id), created_by=created_by, lines=lines))

    jw.taxable_value = taxable
    jw.tax_amount = tax_total
    jw.total_value = gross
    jw.returned_qty = jw.qty_kg
    jw.status = JobWorkStatus.BILLED
    jw.posting_reference = ref
    db.flush()
    return voucher
