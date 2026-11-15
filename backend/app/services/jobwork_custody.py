"""Job-work inventory custody (Stage 3C).

Issuing material to a job worker moves it — in the ONE authoritative inventory
ledger — from a source warehouse into that vendor's *custody warehouse* (a
warehouse flagged with ``vendor_id`` and category VENDOR_CUSTODY). The material:

* stays company-owned and FIFO-valued (it is still in a company warehouse);
* is never recorded as a sale and never auto-consumed;
* is visible as vendor-wise outstanding stock until resolved (Stage 3D);
* creates no second ledger, no duplicate stock and no GL entry
  (``inventory.transfer_stock`` is GL-neutral).

A roll keeps its protected Roll ID — no new output roll is created at issue time.

Out of scope here (later stages): receipts, returns, rejections, reprocessing,
new output rolls, charge/freight capitalisation, ITC-04. Existing job-work
billing (``app.services.jobwork``) is untouched.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.types import QTY_SCALE, quantize
from app.models.hybrid import Process, WarehouseCategory, WorkMode
from app.models.inventory import ItemType
from app.models.jobwork import JobWorkIssue, JobWorkIssueStatus, JobWorkProcess
from app.models.manufacturing import FabricRoll
from app.models.masters import Supplier, Warehouse
from app.services import hybrid, inventory
from app.services.sequences import next_document_number

# JobWorkProcess -> hybrid Process (OTHER has no toggle)
_PROC = {JobWorkProcess.KNITTING: Process.KNITTING, JobWorkProcess.DYEING: Process.DYEING,
         JobWorkProcess.FINISHING: Process.FINISHING}


class JobWorkCustodyError(ValueError):
    """An invalid job-work custody action (bad worker, disabled mode, duplicate
    issue, insufficient stock, isolation breach)."""


def require_job_worker(db: Session, company_id: uuid.UUID, supplier_id: uuid.UUID) -> Supplier:
    """The vendor must exist in this company and be authorised as a job worker."""
    s = db.get(Supplier, supplier_id)
    if s is None or s.company_id != company_id:
        raise JobWorkCustodyError("Job worker not found in the active company")
    if not s.is_job_worker:
        raise JobWorkCustodyError(f"Supplier '{s.code}' is not authorised as a job worker")
    return s


def get_or_create_custody_warehouse(db: Session, company_id: uuid.UUID, vendor: Supplier,
                                    created_by: uuid.UUID | None = None) -> Warehouse:
    """Return the vendor's custody warehouse, creating it (company-level) on first
    use. It is company-owned storage that happens to sit at the job worker."""
    wh = db.execute(
        select(Warehouse).where(Warehouse.company_id == company_id, Warehouse.vendor_id == vendor.id)
    ).scalars().first()
    if wh is None:
        wh = Warehouse(company_id=company_id, vendor_id=vendor.id, code=f"JWC-{vendor.code}"[:20],
                       name=f"Custody @ {vendor.name}"[:120], kind="VENDOR_CUSTODY",
                       category=WarehouseCategory.VENDOR_CUSTODY.value, created_by=created_by)
        db.add(wh)
        db.flush()
    return wh


def _q(v):
    return None if v is None else quantize(Decimal(str(v)), QTY_SCALE)


def issue_to_vendor(
    db: Session,
    *,
    company_id: uuid.UUID,
    job_worker_id: uuid.UUID,
    process: str,
    item_type: ItemType | str,
    item_id: uuid.UUID,
    qty,
    source_warehouse_id: uuid.UUID | None = None,
    fabric_roll_id: uuid.UUID | None = None,
    job_work_id: uuid.UUID | None = None,
    factory_id: uuid.UUID | None = None,
    uom: str = "KG",
    weight_kg=None,
    length_m=None,
    issue_date: date | None = None,
    challan_no: str | None = None,
    created_by: uuid.UUID | None = None,
) -> JobWorkIssue:
    """Issue ``qty`` of an item into a job worker's custody. Validates the worker,
    the outsourced process mode and stock, moves the stock into the custody
    warehouse (FIFO, GL-neutral) and records an outstanding issue challan."""
    it = ItemType(item_type) if not isinstance(item_type, ItemType) else item_type
    proc = JobWorkProcess(process)
    if Decimal(str(qty or 0)) <= 0:
        raise JobWorkCustodyError("Issue quantity must be positive")

    vendor = require_job_worker(db, company_id, job_worker_id)

    # Respect the six process-mode switches: sending work OUT needs the matching
    # <process>_outsourced enabled (OTHER is not gated).
    hproc = _PROC.get(proc)
    if hproc is not None:
        try:
            hybrid.require_mode(db, company_id, hproc, WorkMode.OUTSOURCED)
        except hybrid.HybridConfigError as e:
            raise JobWorkCustodyError(str(e))

    # Duplicate-issue guard for a specific roll: it cannot be issued again while an
    # earlier issue of it is still outstanding.
    if fabric_roll_id is not None:
        roll = db.get(FabricRoll, fabric_roll_id)
        if roll is None or roll.company_id != company_id:
            raise JobWorkCustodyError("Roll not found in the active company")
        assert_available_for_use(db, roll, action="issued")   # not already in custody
        dup = db.execute(
            select(JobWorkIssue).where(
                JobWorkIssue.company_id == company_id,
                JobWorkIssue.fabric_roll_id == fabric_roll_id,
                JobWorkIssue.status.in_([JobWorkIssueStatus.OUTSTANDING,
                                         JobWorkIssueStatus.PARTIALLY_RECEIVED]))
        ).scalars().first()
        if dup is not None:
            raise JobWorkCustodyError(f"Roll {roll.roll_no} is already out with a job worker")
    else:
        roll = None

    custody = get_or_create_custody_warehouse(db, company_id, vendor, created_by=created_by)
    idate = issue_date or date.today()
    if not challan_no:
        from app.services.accounting.posting_engine import fiscal_year_for
        challan_no = next_document_number(db, company_id=company_id, doc_type="JWCHALLAN",
                                          fiscal_year=fiscal_year_for(idate), prefix="JWC-")

    # Move the stock source -> custody. FIFO + row locking + InsufficientStockError
    # all come from the existing engine; over-issue is rejected there.
    res = inventory.transfer_stock(db, company_id=company_id, item_type=it, item_id=item_id,
                                   from_warehouse_id=source_warehouse_id,
                                   to_warehouse_id=custody.id, qty=qty,
                                   source_module="jobwork_issue", source_id=challan_no)

    # Keep a roll's authoritative location in sync (Stage 3B pattern); same Roll ID.
    if roll is not None:
        roll.warehouse_id = custody.id

    issue = JobWorkIssue(
        company_id=company_id, factory_id=factory_id, job_work_id=job_work_id,
        job_worker_id=vendor.id, process=proc.value, source_warehouse_id=source_warehouse_id,
        custody_warehouse_id=custody.id, item_type=it.value, item_id=item_id,
        fabric_roll_id=fabric_roll_id, qty=_q(res["qty"]), uom=uom,
        weight_kg=_q(weight_kg), length_m=_q(length_m), issue_date=idate, challan_no=challan_no,
        status=JobWorkIssueStatus.OUTSTANDING, posting_reference=f"JWISSUE:{challan_no}",
        created_by=created_by)
    db.add(issue)
    db.flush()
    return issue


def vendor_outstanding(db: Session, company_id: uuid.UUID, *, job_worker_id: uuid.UUID | None = None,
                       factory_id: uuid.UUID | None = None, process: str | None = None,
                       job_work_id: uuid.UUID | None = None) -> list[dict]:
    """Vendor-wise outstanding custody position, grouped by (vendor, process).
    Filterable by vendor, factory, process and job-work order for reconciliation."""
    stmt = select(JobWorkIssue.job_worker_id, JobWorkIssue.process,
                  func.count(JobWorkIssue.id), func.coalesce(func.sum(JobWorkIssue.qty), 0)).where(
        JobWorkIssue.company_id == company_id,
        JobWorkIssue.status.notin_([JobWorkIssueStatus.CLOSED, JobWorkIssueStatus.RESOLVED]))
    if job_worker_id is not None:
        stmt = stmt.where(JobWorkIssue.job_worker_id == job_worker_id)
    if factory_id is not None:
        stmt = stmt.where(JobWorkIssue.factory_id == factory_id)
    if process is not None:
        stmt = stmt.where(JobWorkIssue.process == JobWorkProcess(process).value)
    if job_work_id is not None:
        stmt = stmt.where(JobWorkIssue.job_work_id == job_work_id)
    stmt = stmt.group_by(JobWorkIssue.job_worker_id, JobWorkIssue.process)
    rows = db.execute(stmt).all()
    return [{"job_worker_id": str(w), "process": p, "open_issues": int(n),
             "outstanding_qty": quantize(Decimal(str(q)), QTY_SCALE)} for w, p, n, q in rows]


def outstanding_detail(db: Session, company_id: uuid.UUID, *, job_worker_id: uuid.UUID | None = None,
                       fabric_roll_id: uuid.UUID | None = None) -> list[JobWorkIssue]:
    """Line-level outstanding issues (per material / Roll ID) for reconciliation."""
    stmt = select(JobWorkIssue).where(
        JobWorkIssue.company_id == company_id,
        JobWorkIssue.status.notin_([JobWorkIssueStatus.CLOSED, JobWorkIssueStatus.RESOLVED]))
    if job_worker_id is not None:
        stmt = stmt.where(JobWorkIssue.job_worker_id == job_worker_id)
    if fabric_roll_id is not None:
        stmt = stmt.where(JobWorkIssue.fabric_roll_id == fabric_roll_id)
    return list(db.execute(stmt).scalars().all())


# ===================== Stage 3D: receipts & reconciliation =====================

from app.models.jobwork import JobWorkInspection, JobWorkReceipt, JobWorkIssue, JobWorkIssueStatus  # noqa: E402
from app.models.manufacturing import FabricRoll, RollKind  # noqa: E402
from app.services.manufacturing import roll_genealogy, roll_identity  # noqa: E402

_TOL = Decimal("0.000001")

# destination warehouse category for accepted output, by process
_DEST_CATEGORY = {JobWorkProcess.KNITTING: WarehouseCategory.GREIGE,
                  JobWorkProcess.DYEING: WarehouseCategory.SEMI_FINISHED,
                  JobWorkProcess.FINISHING: WarehouseCategory.FINISHED_GOODS}


def _is_vendor_custody(db: Session, warehouse_id) -> bool:
    if warehouse_id is None:
        return False
    wh = db.get(Warehouse, warehouse_id)
    return wh is not None and wh.vendor_id is not None


def assert_available_for_use(db: Session, roll: FabricRoll, action: str = "used") -> None:
    """Raise if a roll is currently in a job worker's custody (Stage 3D). Blocks
    sales dispatch, packing, warehouse transfer and re-issue until its return to
    company custody is recorded through an authorised receipt."""
    if roll is not None and _is_vendor_custody(db, roll.warehouse_id):
        raise JobWorkCustodyError(
            f"Roll {roll.roll_no} is in a job worker's custody and cannot be {action} "
            "until its return is recorded.")


def _d(v) -> Decimal:
    return Decimal(str(v or 0))


def receive_from_vendor(
    db: Session,
    *,
    company_id: uuid.UUID,
    job_work_issue_id: uuid.UUID,
    receipt_date: date | None = None,
    challan_no: str | None = None,
    accepted_input=0,
    accepted_output_qty=None,
    output_weight_kg=None,
    output_length_m=None,
    returned_qty=0,
    rejected_qty=0,
    rework_qty=0,
    process_loss=0,
    shortage_qty=0,
    output_warehouse_id: uuid.UUID | None = None,
    return_warehouse_id: uuid.UUID | None = None,
    preserve_roll_id: uuid.UUID | None = None,
    inspection_result: str | None = None,
    authorize_excess: bool = False,
    created_by: uuid.UUID | None = None,
) -> JobWorkReceipt:
    """Record one receipt against a custody issue, reconciling the input disposition
    and moving accepted output / unprocessed returns through the inventory ledger
    (GL-neutral). Recorded loss/rejection/shortage are reconciliation quantities
    only (no write-off here). Multiple partial receipts are supported."""
    issue = db.get(JobWorkIssue, job_work_issue_id)
    if issue is None or issue.company_id != company_id:
        raise JobWorkCustodyError("Job-work issue not found in the active company")
    if issue.status in (JobWorkIssueStatus.CLOSED, JobWorkIssueStatus.RESOLVED):
        raise JobWorkCustodyError("This issue is already closed")

    acc_in = _d(accepted_input); ret = _d(returned_qty); rej = _d(rejected_qty)
    rwk = _d(rework_qty); loss = _d(process_loss); short = _d(shortage_qty)
    for name, val in (("accepted_input", acc_in), ("returned_qty", ret), ("rejected_qty", rej),
                      ("rework_qty", rwk), ("process_loss", loss), ("shortage_qty", short)):
        if val < 0:
            raise JobWorkCustodyError(f"{name} cannot be negative")

    input_resolved = acc_in + ret + rej + loss + short
    if input_resolved <= 0:
        raise JobWorkCustodyError("A receipt must account for some issued material")

    remaining_before = _d(issue.qty) - _d(issue.resolved_input_qty)
    remaining_after = remaining_before - input_resolved
    is_excess = remaining_after < -_TOL
    if is_excess and not authorize_excess:
        raise JobWorkCustodyError(
            f"Over-receipt: resolving {input_resolved} exceeds the {remaining_before} outstanding; "
            "requires authorised approval (authorize_excess).")

    proc = JobWorkProcess(issue.process)
    insp = JobWorkInspection(inspection_result).value if inspection_result else None
    acc_out = _d(accepted_output_qty) if accepted_output_qty is not None else acc_in
    custody_wh = issue.custody_warehouse_id
    output_roll_id = None

    # ---- accepted output ----
    if preserve_roll_id is not None:
        # Finishing (or any process) returning the SAME physical roll unchanged in
        # identity. Rejected/rework may never be received as finished goods.
        roll = db.get(FabricRoll, preserve_roll_id)
        if roll is None or roll.company_id != company_id:
            raise JobWorkCustodyError("Preserved roll not found in the active company")
        if insp in (JobWorkInspection.REJECT.value, JobWorkInspection.REWORK.value):
            raise JobWorkCustodyError(
                f"Roll {roll.roll_no} is {insp}; a rejected/rework roll cannot be received as accepted output")
        dest = output_warehouse_id
        cat = _DEST_CATEGORY.get(proc)
        if dest is not None and cat is not None:
            hybrid.require_warehouse_stage(db, company_id, dest, cat, factory_id=issue.factory_id)
        # move the roll out of custody into the destination (same Roll ID)
        bal = inventory.balance(db, company_id=company_id, item_type=ItemType.FABRIC, item_id=roll.id)
        if dest is not None and roll.warehouse_id != dest and bal["qty_on_hand"] and bal["qty_on_hand"] > 0:
            inventory.transfer_stock(db, company_id=company_id, item_type=ItemType.FABRIC, item_id=roll.id,
                                     from_warehouse_id=roll.warehouse_id, to_warehouse_id=dest,
                                     qty=bal["qty_on_hand"], source_module="jobwork_receipt",
                                     source_id=str(job_work_issue_id))
        if dest is not None:
            roll.warehouse_id = dest
        if proc == JobWorkProcess.FINISHING:
            roll.kind = RollKind.FINISHED
        roll_genealogy.record_finishing(db, roll, input_weight_kg=issue.weight_kg,
                                        output_weight_kg=output_weight_kg,
                                        production_ref=challan_no, recorded_by=created_by)
        output_roll_id = roll.id
        acc_out = _d(output_weight_kg) if output_weight_kg is not None else _d(roll.weight_kg)

    elif acc_in > 0:
        # Knitting/dyeing: consume accepted input from custody and create a NEW
        # protected-ID output roll in the destination warehouse.
        cat = _DEST_CATEGORY.get(proc)
        if cat is not None:
            hybrid.require_warehouse_stage(db, company_id, output_warehouse_id, cat, factory_id=issue.factory_id)
        res = inventory.consume(db, company_id=company_id, item_type=ItemType.FABRIC, item_id=issue.item_id,
                                qty=acc_in, warehouse_id=custody_wh, event_type="JOBWORK_CONSUME",
                                source_module="jobwork_receipt", source_id=str(job_work_issue_id))
        cost = res["total_cost"]
        when = receipt_date or date.today()
        if proc == JobWorkProcess.KNITTING:
            new_no = roll_identity.new_greige_roll_id(db, company_id=company_id, when=when)
            kind = RollKind.GREIGE
        elif proc == JobWorkProcess.DYEING:
            new_no = roll_identity.new_dyed_roll_id(db, company_id=company_id, when=when)
            kind = RollKind.DYED
        else:
            new_no = roll_identity.new_greige_roll_id(db, company_id=company_id, when=when)
            kind = RollKind.FINISHED
        out_roll = FabricRoll(company_id=company_id, factory_id=issue.factory_id, roll_no=new_no,
                              barcode=new_no, kind=kind, weight_kg=quantize(acc_out, QTY_SCALE),
                              length_m=_q(output_length_m), warehouse_id=output_warehouse_id,
                              quality_status="OK", status="ACTIVE", created_by=created_by)
        db.add(out_roll); db.flush()
        from app.core.types import MONEY_SCALE
        unit = quantize(cost / acc_out, MONEY_SCALE) if acc_out > 0 else Decimal("0")
        # value=cost carries the exact FIFO cost of the consumed input onto the new
        # roll (no per-kg dust, no value created or lost) -> GL-neutral.
        inventory.receive(db, company_id=company_id, item_type=ItemType.FABRIC, item_id=out_roll.id,
                          qty=acc_out, unit_cost=unit, value=cost, warehouse_id=output_warehouse_id,
                          event_type="JOBWORK_PRODUCE", source_module="jobwork_receipt",
                          source_id=str(job_work_issue_id))
        roll_genealogy.record_edge(
            db, company_id=company_id, process_type=(roll_genealogy.KNIT if proc == JobWorkProcess.KNITTING
                                                     else roll_genealogy.DYE),
            source_roll_id=issue.fabric_roll_id, output_roll_id=out_roll.id, factory_id=issue.factory_id,
            production_ref=challan_no, uom="KG", input_weight_kg=acc_in, output_weight_kg=acc_out,
            output_length_m=output_length_m, recorded_by=created_by)
        output_roll_id = out_roll.id

    # ---- unprocessed return back to a company warehouse ----
    if ret > 0 and return_warehouse_id is not None:
        inventory.transfer_stock(db, company_id=company_id, item_type=ItemType.FABRIC, item_id=issue.item_id,
                                 from_warehouse_id=custody_wh, to_warehouse_id=return_warehouse_id,
                                 qty=ret, source_module="jobwork_return", source_id=str(job_work_issue_id))
        if issue.fabric_roll_id is not None:
            r = db.get(FabricRoll, issue.fabric_roll_id)
            if r is not None:
                r.warehouse_id = return_warehouse_id

    # ---- reconciliation running totals + status ----
    issue.resolved_input_qty = quantize(_d(issue.resolved_input_qty) + input_resolved, QTY_SCALE)
    issue.accepted_output_qty = quantize(_d(issue.accepted_output_qty) + acc_out, QTY_SCALE)
    issue.returned_qty = quantize(_d(issue.returned_qty) + ret, QTY_SCALE)
    issue.rejected_qty = quantize(_d(issue.rejected_qty) + rej, QTY_SCALE)
    issue.process_loss_qty = quantize(_d(issue.process_loss_qty) + loss, QTY_SCALE)
    issue.shortage_qty = quantize(_d(issue.shortage_qty) + short, QTY_SCALE)
    issue.status = (JobWorkIssueStatus.CLOSED if (_d(issue.qty) - _d(issue.resolved_input_qty)) <= _TOL
                    else JobWorkIssueStatus.PARTIALLY_RECEIVED)

    receipt = JobWorkReceipt(
        company_id=company_id, factory_id=issue.factory_id, job_work_issue_id=issue.id,
        job_work_id=issue.job_work_id, job_worker_id=issue.job_worker_id, process=proc.value,
        receipt_date=receipt_date or date.today(), challan_no=challan_no,
        input_resolved_qty=quantize(input_resolved, QTY_SCALE),
        accepted_input_qty=_q(acc_in), returned_qty=_q(ret), rejected_qty=_q(rej), rework_qty=_q(rwk),
        process_loss_qty=_q(loss), shortage_qty=_q(short), accepted_output_qty=_q(acc_out),
        output_weight_kg=_q(output_weight_kg), output_length_m=_q(output_length_m),
        inspection_result=insp, source_roll_id=issue.fabric_roll_id, output_roll_id=output_roll_id,
        output_warehouse_id=output_warehouse_id, return_warehouse_id=return_warehouse_id,
        is_excess=bool(is_excess), authorized_by=(created_by if is_excess else None),
        posting_reference=(f"JWRCPT:{challan_no}" if challan_no else None), created_by=created_by)
    db.add(receipt); db.flush()
    return receipt


def reconciliation(db: Session, issue: JobWorkIssue) -> dict:
    """Input reconciliation for one issue: issued = resolved + remaining, where
    resolved = accepted + returned + rejected + loss + shortage."""
    issued = _d(issue.qty)
    resolved = _d(issue.resolved_input_qty)
    return {"issued_qty": quantize(issued, QTY_SCALE),
            "accepted_output_qty": quantize(_d(issue.accepted_output_qty), QTY_SCALE),
            "returned_qty": quantize(_d(issue.returned_qty), QTY_SCALE),
            "rejected_qty": quantize(_d(issue.rejected_qty), QTY_SCALE),
            "process_loss_qty": quantize(_d(issue.process_loss_qty), QTY_SCALE),
            "shortage_qty": quantize(_d(issue.shortage_qty), QTY_SCALE),
            "resolved_input_qty": quantize(resolved, QTY_SCALE),
            "remaining_outstanding": quantize(issued - resolved, QTY_SCALE),
            "status": issue.status.value if hasattr(issue.status, "value") else issue.status}
