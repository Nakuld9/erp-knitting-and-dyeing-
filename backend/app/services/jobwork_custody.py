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
        dup = db.execute(
            select(JobWorkIssue).where(
                JobWorkIssue.company_id == company_id,
                JobWorkIssue.fabric_roll_id == fabric_roll_id,
                JobWorkIssue.status == JobWorkIssueStatus.OUTSTANDING)
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
        JobWorkIssue.status == JobWorkIssueStatus.OUTSTANDING)
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
    stmt = select(JobWorkIssue).where(JobWorkIssue.company_id == company_id,
                                      JobWorkIssue.status == JobWorkIssueStatus.OUTSTANDING)
    if job_worker_id is not None:
        stmt = stmt.where(JobWorkIssue.job_worker_id == job_worker_id)
    if fabric_roll_id is not None:
        stmt = stmt.where(JobWorkIssue.fabric_roll_id == fabric_roll_id)
    return list(db.execute(stmt).scalars().all())
