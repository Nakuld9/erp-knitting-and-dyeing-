"""Job-work vendor bill entry, lot-wise matching & Direct Expense posting (Stage 3E).

A job worker's bill is entered with charge lines optionally allocated to a
job-work order, lot (issue) and/or roll. On approval it posts to Direct Expenses
through the EXISTING posting engine (Dr Direct Expense + Dr Input GST, Cr TDS
Payable, Cr Accounts Payable), reusing the verified GST rules; TDS is applied at a
caller-supplied rate (never hard-coded). Credit/debit notes post the reverse.

Billing is kept strictly separate from material custody: nothing here creates an
inventory receipt, consumes material, changes Roll IDs, changes custody or alters
outstanding material quantities. No charge or freight is capitalised into cost.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, QTY_SCALE, quantize
from app.models.core import Company
from app.models.jobwork import (
    JobWork,
    JobWorkBill,
    JobWorkBillLine,
    JobWorkBillStatus,
    JobWorkBillType,
    JobWorkIssue,
    JobWorkProcess,
)
from app.models.masters import Supplier, TaxCode
from app.services.accounting import posting_engine as pe
from app.services.tax import compute_gst, resolve_intra_state

MZERO = quantize(0, MONEY_SCALE)

# Direct Expense accounts by process (same as app.services.jobwork billing).
_EXPENSE = {JobWorkProcess.DYEING.value: "5090", JobWorkProcess.FINISHING.value: "5100"}
_EXPENSE_DEFAULT = "5110"
IN_CGST, IN_SGST, IN_IGST = "1121", "1122", "1123"
TDS_PAYABLE = "2050"
PAYABLE = "2010"


class JobWorkBillError(ValueError):
    """An invalid job-work bill action (bad vendor, duplicate invoice, over-
    allocation, posting on a non-draft bill, isolation breach)."""


def _m(v) -> Decimal:
    return quantize(Decimal(str(v or 0)), MONEY_SCALE)


def _q(v):
    return None if v is None else quantize(Decimal(str(v)), QTY_SCALE)


def _line_amount(spec: dict) -> Decimal:
    if spec.get("amount") is not None:
        return _m(spec["amount"])
    qty = Decimal(str(spec.get("qty") or 0))
    rate = Decimal(str(spec.get("rate") or 0))
    return _m(qty * rate)


def agreed_charge(db: Session, issue: JobWorkIssue) -> Decimal | None:
    """Agreed charge for a lot = (accepted, else issued) qty x the order rate.
    Returns None when no order rate is available (no cap can be enforced)."""
    if issue.job_work_id is None:
        return None
    order = db.get(JobWork, issue.job_work_id)
    if order is None or Decimal(str(order.rate or 0)) <= 0:
        return None
    base = Decimal(str(issue.accepted_output_qty or 0))
    if base <= 0:
        base = Decimal(str(issue.qty or 0))
    return _m(base * Decimal(str(order.rate)))


def _net_billed_for_issue(db: Session, company_id: uuid.UUID, issue_id: uuid.UUID,
                          exclude_bill_id: uuid.UUID | None = None) -> Decimal:
    """Net billed against a lot across APPROVED bills: invoices add, credit/debit
    notes subtract."""
    rows = db.execute(
        select(JobWorkBill.bill_type, func.coalesce(func.sum(JobWorkBillLine.amount), 0))
        .join(JobWorkBillLine, JobWorkBillLine.bill_id == JobWorkBill.id)
        .where(JobWorkBill.company_id == company_id,
               JobWorkBillLine.job_work_issue_id == issue_id,
               JobWorkBill.status == JobWorkBillStatus.APPROVED,
               *( [JobWorkBill.id != exclude_bill_id] if exclude_bill_id else [] ))
        .group_by(JobWorkBill.bill_type)
    ).all()
    net = MZERO
    for btype, amt in rows:
        bt = btype.value if hasattr(btype, "value") else btype
        net += _m(amt) if bt == JobWorkBillType.INVOICE.value else -_m(amt)
    return net


def create_bill(
    db: Session,
    *,
    company_id: uuid.UUID,
    supplier_id: uuid.UUID,
    invoice_no: str,
    invoice_date: date,
    lines: list[dict],
    bill_type: str = JobWorkBillType.INVOICE.value,
    process: str | None = None,
    factory_id: uuid.UUID | None = None,
    tax_code_id: uuid.UUID | None = None,
    additional_charges=0,
    tds_rate=0,
    remarks: str | None = None,
    authorize_over_allocation: bool = False,
    created_by: uuid.UUID | None = None,
) -> JobWorkBill:
    """Enter a job-work bill (DRAFT) with charge lines. Does NOT post or touch
    inventory. Prevents duplicate invoices and lot over-allocation."""
    btype = JobWorkBillType(bill_type)
    vendor = db.get(Supplier, supplier_id)
    if vendor is None or vendor.company_id != company_id:
        raise JobWorkBillError("Supplier not found in the active company")
    if not vendor.is_job_worker:
        raise JobWorkBillError(f"Supplier '{vendor.code}' is not a job worker")
    if not lines:
        raise JobWorkBillError("A bill needs at least one line")

    # duplicate-invoice guard (also enforced by a DB unique index)
    dup = db.execute(select(JobWorkBill).where(
        JobWorkBill.company_id == company_id, JobWorkBill.supplier_id == supplier_id,
        JobWorkBill.bill_type == btype, JobWorkBill.invoice_no == invoice_no)).scalars().first()
    if dup is not None:
        raise JobWorkBillError(f"Invoice {invoice_no} is already entered for this vendor")

    bill = JobWorkBill(company_id=company_id, factory_id=factory_id, supplier_id=supplier_id,
                       bill_type=btype, process=process, invoice_no=invoice_no, invoice_date=invoice_date,
                       tax_code_id=tax_code_id, tds_rate=_q(tds_rate), status=JobWorkBillStatus.DRAFT,
                       payment_status="UNPAID", remarks=remarks, created_by=created_by)
    db.add(bill); db.flush()

    service_total = MZERO
    # track per-issue proposed billing within THIS bill for over-allocation
    per_issue_new: dict[uuid.UUID, Decimal] = {}
    for spec in lines:
        amt = _line_amount(spec)
        if amt < 0:
            raise JobWorkBillError("A line amount cannot be negative")
        issue_id = spec.get("job_work_issue_id")
        issue = None
        if issue_id is not None:
            issue = db.get(JobWorkIssue, issue_id)
            if issue is None or issue.company_id != company_id:
                raise JobWorkBillError("Job-work lot (issue) not found in the active company")
            per_issue_new[issue_id] = per_issue_new.get(issue_id, MZERO) + amt
        line = JobWorkBillLine(
            company_id=company_id, bill_id=bill.id, job_work_id=spec.get("job_work_id"),
            job_work_issue_id=issue_id, fabric_roll_id=spec.get("fabric_roll_id"),
            lot_no=spec.get("lot_no"), process=spec.get("process") or process,
            qty=_q(spec.get("qty") or 0), uom=spec.get("uom", "KG"),
            weight_kg=_q(spec.get("weight_kg")), length_m=_q(spec.get("length_m")),
            rate=_m(spec.get("rate") or 0), rate_basis=spec.get("rate_basis", "PER_KG"), amount=amt,
            is_disputed=bool(spec.get("is_disputed", False)),
            disputed_amount=_m(spec.get("disputed_amount") or 0), narration=spec.get("narration"),
            created_by=created_by)
        db.add(line)
        service_total += amt

    # over-allocation guard (only for invoices, and only where an agreed charge exists)
    if btype == JobWorkBillType.INVOICE and not authorize_over_allocation:
        for iid, new_amt in per_issue_new.items():
            issue = db.get(JobWorkIssue, iid)
            agreed = agreed_charge(db, issue)
            if agreed is not None:
                already = _net_billed_for_issue(db, company_id, iid)
                if already + new_amt > agreed + Decimal("0.01"):
                    raise JobWorkBillError(
                        f"Over-allocation on lot {issue.challan_no}: billed {already + new_amt} "
                        f"exceeds agreed {agreed}; requires authorised adjustment.")

    add_chg = _m(additional_charges)
    taxable = _m(service_total + add_chg)
    company = db.get(Company, company_id)
    intra = resolve_intra_state(company.state_code if company else None,
                                getattr(vendor, "state_code", None), context=f"Job-work bill {invoice_no}")
    tax_code = db.get(TaxCode, tax_code_id) if tax_code_id else None
    g = compute_gst(tax_code, taxable, intra_state=intra)
    tds = quantize(taxable * (Decimal(str(tds_rate or 0)) / Decimal("100")), MONEY_SCALE)
    gst_total = g["cgst"] + g["sgst"] + g["igst"]

    bill.service_charge = _m(service_total)
    bill.additional_charges = add_chg
    bill.taxable_amount = taxable
    bill.cgst, bill.sgst, bill.igst = g["cgst"], g["sgst"], g["igst"]
    bill.tds_amount = tds
    bill.total_amount = quantize(taxable + gst_total - tds, MONEY_SCALE)   # net payable to vendor
    db.flush()
    return bill


def approve_bill(db: Session, bill: JobWorkBill, *, created_by: uuid.UUID | None = None):
    """Post an approved job-work bill to Direct Expenses (idempotent). Reuses the
    existing posting engine; credit/debit notes post the reverse. No inventory."""
    if bill.status == JobWorkBillStatus.APPROVED:
        return pe.find_existing(db, bill.company_id, bill.posting_reference) if bill.posting_reference else None
    if bill.status == JobWorkBillStatus.CANCELLED:
        raise JobWorkBillError("A cancelled bill cannot be approved")

    ref = f"JWBILL:{bill.bill_type.value if hasattr(bill.bill_type,'value') else bill.bill_type}:{bill.invoice_no}:{bill.supplier_id}"
    existing = pe.find_existing(db, bill.company_id, ref)
    if existing is not None:
        bill.status = JobWorkBillStatus.APPROVED
        bill.posting_reference = ref
        return existing

    expense_acc = _EXPENSE.get(bill.process, _EXPENSE_DEFAULT)
    taxable = _m(bill.taxable_amount); cg = _m(bill.cgst); sg = _m(bill.sgst); ig = _m(bill.igst)
    tds = _m(bill.tds_amount); payable = _m(bill.total_amount)
    btype = bill.bill_type.value if hasattr(bill.bill_type, "value") else bill.bill_type
    reverse = btype in (JobWorkBillType.CREDIT_NOTE.value, JobWorkBillType.DEBIT_NOTE.value)

    def L(acc, amt, *, dr, narration, party=False):
        if amt <= 0:
            return None
        kw = dict(narration=narration)
        if party:
            kw.update(party_type="supplier", party_id=bill.supplier_id)
        # reverse swaps debit<->credit for credit/debit notes
        side_dr = dr if not reverse else (not dr)
        kw["debit" if side_dr else "credit"] = amt
        return pe.PostingLine(acc, **kw)

    lines = [L(expense_acc, taxable, dr=True, narration="Job-work processing charges"),
             L(IN_CGST, cg, dr=True, narration="Input CGST"),
             L(IN_SGST, sg, dr=True, narration="Input SGST"),
             L(IN_IGST, ig, dr=True, narration="Input IGST"),
             L(TDS_PAYABLE, tds, dr=False, narration="TDS payable"),
             L(PAYABLE, payable, dr=False, narration="Subcontractor payable", party=True)]
    lines = [ln for ln in lines if ln is not None]

    voucher = pe.post_voucher(db, pe.PostingRequest(
        company_id=bill.company_id, voucher_type_code="PI", voucher_date=bill.invoice_date,
        posting_reference=ref, narration=f"Job-work {btype} {bill.invoice_no}",
        source_module="jobwork_bill", source_id=str(bill.id), created_by=created_by, lines=lines))
    bill.status = JobWorkBillStatus.APPROVED
    bill.posting_reference = ref
    db.flush()
    return voucher


def lot_reconciliation(db: Session, company_id: uuid.UUID, job_work_issue_id: uuid.UUID) -> dict:
    """Lot-wise reconciliation: material disposition (Stage 3D) + billing match."""
    issue = db.get(JobWorkIssue, job_work_issue_id)
    if issue is None or issue.company_id != company_id:
        raise JobWorkBillError("Job-work lot (issue) not found in the active company")
    agreed = agreed_charge(db, issue)
    billed = _net_billed_for_issue(db, company_id, job_work_issue_id)
    disputed = db.execute(select(func.coalesce(func.sum(JobWorkBillLine.disputed_amount), 0))
                          .join(JobWorkBill, JobWorkBill.id == JobWorkBillLine.bill_id)
                          .where(JobWorkBillLine.company_id == company_id,
                                 JobWorkBillLine.job_work_issue_id == job_work_issue_id,
                                 JobWorkBill.status == JobWorkBillStatus.APPROVED)).scalar()
    issued = Decimal(str(issue.qty or 0)); resolved = Decimal(str(issue.resolved_input_qty or 0))
    return {
        "job_work_issue_id": str(job_work_issue_id), "challan_no": issue.challan_no,
        "issued_qty": quantize(issued, QTY_SCALE),
        "accepted_qty": quantize(Decimal(str(issue.accepted_output_qty or 0)), QTY_SCALE),
        "rejected_qty": quantize(Decimal(str(issue.rejected_qty or 0)), QTY_SCALE),
        "returned_qty": quantize(Decimal(str(issue.returned_qty or 0)), QTY_SCALE),
        "outstanding_qty": quantize(issued - resolved, QTY_SCALE),
        "agreed_charge": agreed,
        "amount_billed": quantize(billed, MONEY_SCALE),
        "amount_unbilled": (quantize(agreed - billed, MONEY_SCALE) if agreed is not None else None),
        "unmatched_amount": (None if agreed is not None else quantize(billed, MONEY_SCALE)),
        "disputed_amount": quantize(Decimal(str(disputed or 0)), MONEY_SCALE),
    }


def vendor_bill_summary(db: Session, company_id: uuid.UUID, supplier_id: uuid.UUID) -> dict:
    """Vendor's job-work billing summary (approved invoices net of credit/debit
    notes). Payment/ledger balance remains in the existing AP ledger."""
    rows = db.execute(select(JobWorkBill.bill_type, func.coalesce(func.sum(JobWorkBill.total_amount), 0))
                      .where(JobWorkBill.company_id == company_id, JobWorkBill.supplier_id == supplier_id,
                             JobWorkBill.status == JobWorkBillStatus.APPROVED)
                      .group_by(JobWorkBill.bill_type)).all()
    net = MZERO
    for bt, amt in rows:
        b = bt.value if hasattr(bt, "value") else bt
        net += _m(amt) if b == JobWorkBillType.INVOICE.value else -_m(amt)
    return {"supplier_id": str(supplier_id), "net_billed_payable": quantize(net, MONEY_SCALE)}
