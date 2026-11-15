"""Job-work vendor bill entry, lot-wise matching & Direct Expense posting.

Stage 3E entry/matching/Direct-Expense posting, hardened in Stage 3F:
* TDS is resolved from a company-authorised configuration (never free request
  input); a bill requesting TDS with no approved section is blocked.
* Credit/debit notes carry an explicit original-bill link and are capped to the
  eligible remaining of that approved invoice.
* Over-allocation requires a stored authorisation (reason + user + time); the
  permission is enforced at the API boundary.
* The agreed charge is snapshotted at first billing so later order-rate / receipt
  changes never rewrite an approved bill's baseline.
* Over-allocation is re-checked at approval under a row lock (concurrency-safe).
* Ambiguous postings (advance bills, debit notes, and credit notes until the CA
  treatment is approved) are blocked rather than guessed.

Billing never touches inventory, custody, Roll IDs or Stage-3D material quantities.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, QTY_SCALE, quantize
from app.models.core import Branch, Company
from app.models.jobwork import (
    JobWork,
    JobWorkBill,
    JobWorkBillLine,
    JobWorkBillStatus,
    JobWorkBillType,
    JobWorkBillingPolicy,
    JobWorkIssue,
    JobWorkProcess,
    JobWorkTdsRate,
)
from app.models.masters import Supplier, TaxCode
from app.services.accounting import posting_engine as pe
from app.services.tax import compute_gst, resolve_intra_state

MZERO = quantize(0, MONEY_SCALE)
_TOL = Decimal("0.01")

OVER_ALLOCATE_PERMISSION = "jobwork.over_allocate"

_EXPENSE = {JobWorkProcess.DYEING.value: "5090", JobWorkProcess.FINISHING.value: "5100"}
_EXPENSE_DEFAULT = "5110"
IN_CGST, IN_SGST, IN_IGST = "1121", "1122", "1123"
TDS_PAYABLE = "2050"
PAYABLE = "2010"


class JobWorkBillError(ValueError):
    """An invalid job-work bill action."""


class JobWorkPostingBlocked(JobWorkBillError):
    """Posting is blocked pending a CA-approved accounting treatment. Carries the
    precise missing decision so callers can surface it rather than guess."""


def _m(v) -> Decimal:
    return quantize(Decimal(str(v or 0)), MONEY_SCALE)


def _q(v):
    return None if v is None else quantize(Decimal(str(v)), QTY_SCALE)


def _finite_nonneg(v, label: str) -> Decimal:
    try:
        d = Decimal(str(v if v is not None else 0))
    except (InvalidOperation, ValueError, TypeError):
        raise JobWorkBillError(f"{label} is not a valid number")
    if not d.is_finite():
        raise JobWorkBillError(f"{label} must be a finite number")
    if d < 0:
        raise JobWorkBillError(f"{label} cannot be negative")
    return d


# ---------------------------------------------------------------- config helpers

def get_policy(db: Session, company_id: uuid.UUID) -> JobWorkBillingPolicy | None:
    return db.execute(select(JobWorkBillingPolicy).where(
        JobWorkBillingPolicy.company_id == company_id)).scalars().first()


def set_policy(db: Session, company_id: uuid.UUID, *, credit_note=None, debit_note=None,
               advance=None, updated_by: uuid.UUID | None = None) -> JobWorkBillingPolicy:
    pol = get_policy(db, company_id)
    if pol is None:
        pol = JobWorkBillingPolicy(company_id=company_id, created_by=updated_by)
        db.add(pol); db.flush()
    if credit_note is not None:
        pol.credit_note_posting_approved = bool(credit_note)
    if debit_note is not None:
        pol.debit_note_posting_approved = bool(debit_note)
    if advance is not None:
        pol.advance_posting_approved = bool(advance)
    pol.updated_by = updated_by
    db.flush()
    return pol


def set_tds_rate(db: Session, company_id: uuid.UUID, *, section_code: str, rate,
                 description: str | None = None, updated_by: uuid.UUID | None = None) -> JobWorkTdsRate:
    r = _finite_nonneg(rate, "TDS rate")
    if r > 100:
        raise JobWorkBillError("TDS rate cannot exceed 100%")
    row = db.execute(select(JobWorkTdsRate).where(
        JobWorkTdsRate.company_id == company_id, JobWorkTdsRate.section_code == section_code)).scalars().first()
    if row is None:
        row = JobWorkTdsRate(company_id=company_id, section_code=section_code, created_by=updated_by)
        db.add(row)
    row.rate = _q(r); row.description = description; row.is_active = True; row.updated_by = updated_by
    db.flush()
    return row


def _resolve_tds(db: Session, company_id: uuid.UUID, section_code: str | None) -> tuple[Decimal, str | None]:
    """Resolve the TDS rate from the company's authorised configuration. No section
    => no TDS. A named section must exist and be active, else posting is blocked
    (CA decision)."""
    if not section_code:
        return Decimal("0"), None
    cfg = db.execute(select(JobWorkTdsRate).where(
        JobWorkTdsRate.company_id == company_id, JobWorkTdsRate.section_code == section_code,
        JobWorkTdsRate.is_active.is_(True))).scalars().first()
    if cfg is None:
        raise JobWorkPostingBlocked(
            f"TDS section '{section_code}' is not configured/approved for this company. "
            "Enter the CA-approved TDS section and rate before applying TDS.")
    rate = _finite_nonneg(cfg.rate, "TDS rate")
    if rate > 100:
        raise JobWorkBillError("Configured TDS rate is invalid (> 100%)")
    return rate, f"CONFIG:{section_code}"


def _assert_factory(db: Session, company_id: uuid.UUID, factory_id: uuid.UUID | None) -> None:
    if factory_id is None:
        return
    b = db.get(Branch, factory_id)
    if b is None or b.company_id != company_id:
        raise JobWorkBillError("Factory does not belong to this company")


# ----------------------------------------------------------- agreed-charge snapshot

def _snapshot_issue_agreed(db: Session, issue: JobWorkIssue) -> Decimal | None:
    """Freeze the agreed charge on the issue at first billing. Returns the frozen
    amount (or None when no order rate is available)."""
    if issue.agreed_charge_snapshot is not None:
        return _m(issue.agreed_charge_snapshot)
    if issue.job_work_id is None:
        return None
    order = db.get(JobWork, issue.job_work_id)
    if order is None or Decimal(str(order.rate or 0)) <= 0:
        return None
    qty = Decimal(str(issue.qty or 0))
    rate = Decimal(str(order.rate))
    amount = _m(qty * rate)
    issue.agreed_charge_snapshot = amount
    issue.agreed_rate_snapshot = _m(rate)
    issue.agreed_basis_snapshot = "PER_KG"
    issue.agreed_qty_snapshot = _q(qty)
    issue.agreed_uom_snapshot = issue.uom or "KG"
    db.flush()
    return amount


def _frozen_agreed(issue: JobWorkIssue) -> Decimal | None:
    return _m(issue.agreed_charge_snapshot) if issue.agreed_charge_snapshot is not None else None


def _net_billed_for_issue(db: Session, company_id: uuid.UUID, issue_id: uuid.UUID,
                          exclude_bill_id: uuid.UUID | None = None) -> Decimal:
    rows = db.execute(
        select(JobWorkBill.bill_type, func.coalesce(func.sum(JobWorkBillLine.amount), 0))
        .join(JobWorkBillLine, JobWorkBillLine.bill_id == JobWorkBill.id)
        .where(JobWorkBill.company_id == company_id,
               JobWorkBillLine.job_work_issue_id == issue_id,
               JobWorkBill.status == JobWorkBillStatus.APPROVED,
               *([JobWorkBill.id != exclude_bill_id] if exclude_bill_id else []))
        .group_by(JobWorkBill.bill_type)
    ).all()
    net = MZERO
    for btype, amt in rows:
        bt = btype.value if hasattr(btype, "value") else btype
        net += _m(amt) if bt == JobWorkBillType.INVOICE.value else -_m(amt)
    return net


# --------------------------------------------------------------- note eligibility

def _note_already(db: Session, company_id: uuid.UUID, original_bill_id: uuid.UUID,
                  exclude_bill_id: uuid.UUID | None = None) -> Decimal:
    """Sum of taxable on existing notes (any status except cancelled) linked to the
    original invoice — so a draft note cannot be double-allocated either."""
    total = db.execute(select(func.coalesce(func.sum(JobWorkBill.taxable_amount), 0)).where(
        JobWorkBill.company_id == company_id, JobWorkBill.original_bill_id == original_bill_id,
        JobWorkBill.status != JobWorkBillStatus.CANCELLED,
        *([JobWorkBill.id != exclude_bill_id] if exclude_bill_id else []))).scalar()
    return _m(total)


# ------------------------------------------------------------------- create / approve

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
    tds_section_code: str | None = None,
    additional_charges=0,
    is_advance: bool = False,
    original_bill_id: uuid.UUID | None = None,
    remarks: str | None = None,
    over_allocation_authorized: bool = False,
    over_allocation_reason: str | None = None,
    authorized_by: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
) -> JobWorkBill:
    """Enter a job-work bill (DRAFT). Does NOT post or touch inventory."""
    btype = JobWorkBillType(bill_type)
    vendor = db.get(Supplier, supplier_id)
    if vendor is None or vendor.company_id != company_id:
        raise JobWorkBillError("Supplier not found in the active company")
    if not vendor.is_job_worker:
        raise JobWorkBillError(f"Supplier '{vendor.code}' is not a job worker")
    if not lines:
        raise JobWorkBillError("A bill needs at least one line")
    _assert_factory(db, company_id, factory_id)

    is_note = btype in (JobWorkBillType.CREDIT_NOTE, JobWorkBillType.DEBIT_NOTE)
    original = None
    if is_note:
        if original_bill_id is None:
            raise JobWorkBillError("A credit/debit note must reference an original bill")
        original = db.get(JobWorkBill, original_bill_id)
        if original is None or original.company_id != company_id:
            raise JobWorkBillError("Original bill not found in the active company")
        if original.supplier_id != supplier_id:
            raise JobWorkBillError("Original bill belongs to a different supplier")
        if original.bill_type != JobWorkBillType.INVOICE:
            raise JobWorkBillError("A note can only reference an INVOICE")
        if original.status != JobWorkBillStatus.APPROVED:
            raise JobWorkBillError("A note can only reference an APPROVED invoice")
    elif original_bill_id is not None:
        raise JobWorkBillError("original_bill_id is only valid for a credit/debit note")

    # duplicate-invoice guard (DB unique index is the final backstop)
    if db.execute(select(JobWorkBill.id).where(
            JobWorkBill.company_id == company_id, JobWorkBill.supplier_id == supplier_id,
            JobWorkBill.bill_type == btype, JobWorkBill.invoice_no == invoice_no)).first() is not None:
        raise JobWorkBillError(f"Invoice {invoice_no} is already entered for this vendor")

    tds_rate, tds_source = _resolve_tds(db, company_id, tds_section_code)

    bill = JobWorkBill(company_id=company_id, factory_id=factory_id, supplier_id=supplier_id,
                       bill_type=btype, process=process, invoice_no=invoice_no, invoice_date=invoice_date,
                       tax_code_id=tax_code_id, tds_section_code=tds_section_code, tds_source=tds_source,
                       tds_rate=_q(tds_rate), is_advance=bool(is_advance), original_bill_id=original_bill_id,
                       status=JobWorkBillStatus.DRAFT, payment_status="UNPAID", remarks=remarks,
                       created_by=created_by)
    db.add(bill); db.flush()

    service_total = MZERO
    per_issue_new: dict[uuid.UUID, Decimal] = {}
    for spec in lines:
        if spec.get("amount") is not None:
            amt = _m(_finite_nonneg(spec["amount"], "line amount"))
        else:
            qty = _finite_nonneg(spec.get("qty"), "line qty")
            rate = _finite_nonneg(spec.get("rate"), "line rate")
            amt = _m(qty * rate)
        issue_id = spec.get("job_work_issue_id")
        if issue_id is not None:
            issue = db.get(JobWorkIssue, issue_id)
            if issue is None or issue.company_id != company_id:
                raise JobWorkBillError("Job-work lot (issue) not found in the active company")
            _snapshot_issue_agreed(db, issue)
            per_issue_new[issue_id] = per_issue_new.get(issue_id, MZERO) + amt
        db.add(JobWorkBillLine(
            company_id=company_id, bill_id=bill.id, job_work_id=spec.get("job_work_id"),
            job_work_issue_id=issue_id, fabric_roll_id=spec.get("fabric_roll_id"),
            lot_no=spec.get("lot_no"), process=spec.get("process") or process,
            qty=_q(spec.get("qty") or 0), uom=spec.get("uom", "KG"),
            weight_kg=_q(spec.get("weight_kg")), length_m=_q(spec.get("length_m")),
            rate=_m(spec.get("rate") or 0), rate_basis=spec.get("rate_basis", "PER_KG"), amount=amt,
            is_disputed=bool(spec.get("is_disputed", False)),
            disputed_amount=_m(spec.get("disputed_amount") or 0), narration=spec.get("narration"),
            created_by=created_by))
        service_total += amt

    add_chg = _m(_finite_nonneg(additional_charges, "additional_charges"))
    taxable = _m(service_total + add_chg)

    # note cap against the eligible remaining of the original invoice
    if is_note:
        eligible = _m(original.taxable_amount) - _note_already(db, company_id, original_bill_id, exclude_bill_id=bill.id)
        if taxable > eligible + _TOL:
            raise JobWorkBillError(
                f"Note amount {taxable} exceeds the eligible remaining {eligible} of invoice "
                f"{original.invoice_no}.")

    # over-allocation guard (invoices only), against the FROZEN snapshot baseline
    over = False
    if btype == JobWorkBillType.INVOICE:
        for iid, new_amt in per_issue_new.items():
            issue = db.get(JobWorkIssue, iid)
            agreed = _frozen_agreed(issue)
            if agreed is not None:
                already = _net_billed_for_issue(db, company_id, iid, exclude_bill_id=bill.id)
                if already + new_amt > agreed + _TOL:
                    over = True
    if over:
        if not over_allocation_authorized:
            raise JobWorkBillError(
                "Over-allocation beyond the agreed charge requires authorisation "
                f"(permission '{OVER_ALLOCATE_PERMISSION}' + reason).")
        if not (over_allocation_reason or "").strip():
            raise JobWorkBillError("Over-allocation requires a reason")
        bill.is_over_allocated = True
        bill.over_allocation_reason = over_allocation_reason.strip()
        bill.over_allocated_by = authorized_by
        bill.over_allocated_at = datetime.now(timezone.utc)

    company = db.get(Company, company_id)
    intra = resolve_intra_state(company.state_code if company else None,
                                getattr(vendor, "state_code", None), context=f"Job-work bill {invoice_no}")
    tax_code = db.get(TaxCode, tax_code_id) if tax_code_id else None
    g = compute_gst(tax_code, taxable, intra_state=intra)
    tds = quantize(taxable * (tds_rate / Decimal("100")), MONEY_SCALE)
    gst_total = g["cgst"] + g["sgst"] + g["igst"]

    bill.service_charge = _m(service_total)
    bill.additional_charges = add_chg
    bill.taxable_amount = taxable
    bill.cgst, bill.sgst, bill.igst = g["cgst"], g["sgst"], g["igst"]
    bill.tds_amount = tds
    bill.total_amount = quantize(taxable + gst_total - tds, MONEY_SCALE)
    db.flush()
    return bill


def _cross_path_guard(db: Session, bill: JobWorkBill) -> None:
    """H1: refuse to post a new bill for a job already billed through the legacy
    jobwork.bill() path (status BILLED), and lock the order row."""
    from app.models.jobwork import JobWorkStatus
    order_ids = {ln.job_work_id for ln in bill.lines if ln.job_work_id is not None}
    for oid in order_ids:
        order = db.execute(select(JobWork).where(JobWork.id == oid).with_for_update()).scalars().first()
        if order is not None and JobWorkStatus(order.status) == JobWorkStatus.BILLED:
            raise JobWorkBillError(
                f"Job work {order.number} is already billed via the legacy path; "
                "it cannot be billed again here.")


def approve_bill(db: Session, bill: JobWorkBill, *, created_by: uuid.UUID | None = None):
    """Approve + post a job-work bill to Direct Expenses (idempotent, concurrency-safe)."""
    if bill.status == JobWorkBillStatus.APPROVED:
        return pe.find_existing(db, bill.company_id, bill.posting_reference) if bill.posting_reference else None
    if bill.status == JobWorkBillStatus.CANCELLED:
        raise JobWorkBillError("A cancelled bill cannot be approved")

    btype = bill.bill_type.value if hasattr(bill.bill_type, "value") else bill.bill_type
    policy = get_policy(db, bill.company_id)

    # ---- posting gates for ambiguous treatments (block, do not guess) ----
    if bill.is_advance:
        raise JobWorkPostingBlocked(
            "Advance job-work bill posting treatment is not established; CA confirmation required. "
            "The advance is recorded but not posted.")
    if btype == JobWorkBillType.DEBIT_NOTE.value:
        raise JobWorkPostingBlocked(
            "Debit-note ledger/GST/TDS treatment is not established; CA confirmation required. "
            "The debit note is recorded and linked but not posted.")
    if btype == JobWorkBillType.CREDIT_NOTE.value and not (policy and policy.credit_note_posting_approved):
        raise JobWorkPostingBlocked(
            "Credit-note posting treatment requires CA approval (enable it in the job-work billing "
            "policy once the ledger/GST/TDS reversal treatment is confirmed).")

    # ---- re-validate TDS config still active ----
    _resolve_tds(db, bill.company_id, bill.tds_section_code)

    # ---- re-validate over-allocation at approval under a row lock (M2) ----
    issue_ids = {ln.job_work_issue_id for ln in bill.lines if ln.job_work_issue_id is not None}
    if issue_ids:
        locked = db.execute(select(JobWorkIssue).where(
            JobWorkIssue.id.in_(issue_ids)).with_for_update()).scalars().all()
        per_issue_new: dict[uuid.UUID, Decimal] = {}
        for ln in bill.lines:
            if ln.job_work_issue_id is not None:
                per_issue_new[ln.job_work_issue_id] = per_issue_new.get(ln.job_work_issue_id, MZERO) + _m(ln.amount)
        if btype == JobWorkBillType.INVOICE.value:
            for issue in locked:
                agreed = _frozen_agreed(issue)
                if agreed is not None:
                    already = _net_billed_for_issue(db, bill.company_id, issue.id, exclude_bill_id=bill.id)
                    if already + per_issue_new.get(issue.id, MZERO) > agreed + _TOL and not bill.is_over_allocated:
                        raise JobWorkBillError(
                            f"Over-allocation on lot {issue.challan_no} detected at approval "
                            "(another bill consumed the agreed charge); requires authorisation.")

    _cross_path_guard(db, bill)

    ref = f"JWBILL:{btype}:{bill.invoice_no}:{bill.supplier_id}"
    existing = pe.find_existing(db, bill.company_id, ref)
    if existing is not None:
        bill.status = JobWorkBillStatus.APPROVED
        bill.posting_reference = ref
        return existing

    expense_acc = _EXPENSE.get(bill.process, _EXPENSE_DEFAULT)
    taxable = _m(bill.taxable_amount); cg = _m(bill.cgst); sg = _m(bill.sgst); ig = _m(bill.igst)
    tds = _m(bill.tds_amount); payable = _m(bill.total_amount)
    reverse = btype == JobWorkBillType.CREDIT_NOTE.value   # only credit notes reach posting as a reversal

    def L(acc, amt, *, dr, narration, party=False):
        if amt <= 0:
            return None
        kw = dict(narration=narration)
        if party:
            kw.update(party_type="supplier", party_id=bill.supplier_id)
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
    bill.approved_by = created_by
    bill.approved_at = datetime.now(timezone.utc)
    db.flush()
    return voucher


def lot_reconciliation(db: Session, company_id: uuid.UUID, job_work_issue_id: uuid.UUID) -> dict:
    issue = db.get(JobWorkIssue, job_work_issue_id)
    if issue is None or issue.company_id != company_id:
        raise JobWorkBillError("Job-work lot (issue) not found in the active company")
    agreed = _frozen_agreed(issue)
    if agreed is None:
        # not yet snapshotted — show the live estimate without freezing it
        agreed = _snapshot_live_estimate(db, issue)
    billed = _net_billed_for_issue(db, company_id, job_work_issue_id)
    # unmatched = approved billing on lines NOT tied to this (or any) lot is surfaced
    # separately at bill level; here, disputed is distinct from billed.
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
        "agreed_charge": (quantize(agreed, MONEY_SCALE) if agreed is not None else None),
        "agreed_charge_frozen": issue.agreed_charge_snapshot is not None,
        "amount_billed": quantize(billed, MONEY_SCALE),
        "amount_unbilled": (quantize(agreed - billed, MONEY_SCALE) if agreed is not None else None),
        "unmatched_amount": (None if agreed is not None else quantize(billed, MONEY_SCALE)),
        "disputed_amount": quantize(Decimal(str(disputed or 0)), MONEY_SCALE),
    }


def _snapshot_live_estimate(db: Session, issue: JobWorkIssue) -> Decimal | None:
    if issue.job_work_id is None:
        return None
    order = db.get(JobWork, issue.job_work_id)
    if order is None or Decimal(str(order.rate or 0)) <= 0:
        return None
    return _m(Decimal(str(issue.qty or 0)) * Decimal(str(order.rate)))


def bill_unmatched_amount(db: Session, company_id: uuid.UUID, bill_id: uuid.UUID) -> Decimal:
    """Approved charge on a bill's lines that are not allocated to any lot."""
    total = db.execute(select(func.coalesce(func.sum(JobWorkBillLine.amount), 0)).where(
        JobWorkBillLine.company_id == company_id, JobWorkBillLine.bill_id == bill_id,
        JobWorkBillLine.job_work_issue_id.is_(None))).scalar()
    return _m(total)


def vendor_bill_summary(db: Session, company_id: uuid.UUID, supplier_id: uuid.UUID) -> dict:
    rows = db.execute(select(JobWorkBill.bill_type, func.coalesce(func.sum(JobWorkBill.total_amount), 0))
                      .where(JobWorkBill.company_id == company_id, JobWorkBill.supplier_id == supplier_id,
                             JobWorkBill.status == JobWorkBillStatus.APPROVED)
                      .group_by(JobWorkBill.bill_type)).all()
    net = MZERO
    for bt, amt in rows:
        b = bt.value if hasattr(bt, "value") else bt
        net += _m(amt) if b == JobWorkBillType.INVOICE.value else -_m(amt)
    return {"supplier_id": str(supplier_id), "net_billed_payable": quantize(net, MONEY_SCALE)}
