"""Stage 3C — job-work inventory custody.

Issuing material to a job worker moves company-owned stock into the vendor's
custody warehouse (one authoritative ledger, FIFO, GL-neutral), records an
outstanding issue challan, preserves the Roll ID, and shows up as vendor-wise
outstanding. No sale, no consume, no duplicate stock or GL.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.accounting import Voucher
from app.models.core import Company
from app.models.inventory import ItemType
from app.models.jobwork import JobWorkIssue, JobWorkIssueStatus
from app.models.manufacturing import KnittingProduction
from app.models.masters import Supplier, Warehouse
from app.services import jobwork_custody as cust
from app.services.hybrid import update_process_mode
from app.services.inventory import service as inventory
from app.services.inventory.service import InsufficientStockError
from app.services.manufacturing.knitting import record_production

WHEN = date(2026, 11, 7)


def _worker(db, demo):
    s = db.get(Supplier, demo["supplier_id"])
    s.is_job_worker = True
    db.flush()
    return s


def _greige_roll(db, demo, number="KP-JW", kg="250"):
    kp = KnittingProduction(company_id=demo["company_id"], number=number, machine_id=demo["machines"]["knit"],
                            yarn_id=demo["yarn_id"], yarn_lot_id=demo["yarn_lot_id"],
                            fabric_type_id=demo["fabric_type_id"], production_date=WHEN,
                            produced_kg=Decimal(kg), yarn_consumed_kg=Decimal("255"))
    db.add(kp); db.flush()
    return record_production(db, kp, [{"weight_kg": kg, "length_m": "900",
                                       "warehouse_id": demo["warehouses"]["greige"]}])["rolls"][0]


def _issue(db, demo, roll, *, process="DYEING", qty=None, worker=None):
    return cust.issue_to_vendor(
        db, company_id=demo["company_id"], job_worker_id=(worker or demo["supplier_id"]),
        process=process, item_type=ItemType.FABRIC, item_id=roll.id,
        qty=qty or Decimal(str(roll.weight_kg)), source_warehouse_id=roll.warehouse_id,
        fabric_roll_id=roll.id, weight_kg=roll.weight_kg, length_m=roll.length_m, issue_date=WHEN)


# ---- job-worker classification ----

def test_non_jobworker_supplier_rejected(db, demo):
    update_process_mode(db, demo["company_id"], {"dye_outsourced": True})
    roll = _greige_roll(db, demo)
    with pytest.raises(cust.JobWorkCustodyError):
        _issue(db, demo, roll)            # demo supplier is NOT a job worker yet


def test_jobworker_classification_allows_issue(db, demo):
    _worker(db, demo)
    update_process_mode(db, demo["company_id"], {"dye_outsourced": True})
    roll = _greige_roll(db, demo)
    iss = _issue(db, demo, roll)
    assert iss.status == JobWorkIssueStatus.OUTSTANDING
    assert iss.challan_no


# ---- custody: ownership, valuation, location, Roll ID ----

def test_issue_moves_to_vendor_custody_company_owned(db, demo):
    _worker(db, demo)
    update_process_mode(db, demo["company_id"], {"dye_outsourced": True})
    roll = _greige_roll(db, demo)
    before = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC, item_id=roll.id)
    original_roll_no = roll.roll_no
    iss = _issue(db, demo, roll)
    db.flush()
    custody = db.get(Warehouse, iss.custody_warehouse_id)
    # custody warehouse is a company-owned VENDOR_CUSTODY location tied to the vendor
    assert custody.company_id == demo["company_id"]
    assert custody.vendor_id == demo["supplier_id"]
    assert custody.category == "VENDOR_CUSTODY"
    # the roll physically sits in custody now, same Roll ID
    db.refresh(roll)
    assert roll.warehouse_id == iss.custody_warehouse_id
    assert roll.roll_no == original_roll_no
    # valuation intact: same qty + value, just a different warehouse (no sale/consume)
    after = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC, item_id=roll.id)
    assert after["qty_on_hand"] == before["qty_on_hand"]
    assert after["value"] == before["value"]


def test_issue_posts_no_voucher(db, demo):
    _worker(db, demo)
    update_process_mode(db, demo["company_id"], {"dye_outsourced": True})
    roll = _greige_roll(db, demo)
    before = db.execute(select(func.count()).select_from(Voucher).where(
        Voucher.company_id == demo["company_id"])).scalar()
    _issue(db, demo, roll)
    after = db.execute(select(func.count()).select_from(Voucher).where(
        Voucher.company_id == demo["company_id"])).scalar()
    assert after == before            # GL-neutral: no duplicate expense / sale / GL


# ---- FIFO cost moved intact ----

def test_fifo_cost_preserved_in_custody(db, demo):
    _worker(db, demo)
    update_process_mode(db, demo["company_id"], {"dye_outsourced": True})
    roll = _greige_roll(db, demo)
    src_value = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC, item_id=roll.id)["value"]
    iss = _issue(db, demo, roll)
    # the custody warehouse now carries exactly the moved value (FIFO cost intact)
    from app.models.inventory import StockLayer
    custody_val = db.execute(select(func.coalesce(func.sum(StockLayer.value_remaining), 0)).where(
        StockLayer.company_id == demo["company_id"], StockLayer.item_id == roll.id,
        StockLayer.warehouse_id == iss.custody_warehouse_id)).scalar()
    assert Decimal(str(custody_val)) == src_value


# ---- vendor-wise outstanding ----

def test_vendor_outstanding(db, demo):
    _worker(db, demo)
    update_process_mode(db, demo["company_id"], {"dye_outsourced": True})
    r1 = _greige_roll(db, demo, number="KP-O1")
    r2 = _greige_roll(db, demo, number="KP-O2", kg="100")
    _issue(db, demo, r1)
    _issue(db, demo, r2)
    rows = cust.vendor_outstanding(db, demo["company_id"], job_worker_id=demo["supplier_id"], process="DYEING")
    assert len(rows) == 1
    assert rows[0]["open_issues"] == 2
    assert rows[0]["outstanding_qty"] == Decimal("350.000000")
    # line-level detail by Roll ID
    detail = cust.outstanding_detail(db, demo["company_id"], fabric_roll_id=r1.id)
    assert len(detail) == 1 and detail[0].fabric_roll_id == r1.id


# ---- guards ----

def test_insufficient_stock_rejected(db, demo):
    _worker(db, demo)
    update_process_mode(db, demo["company_id"], {"dye_outsourced": True})
    roll = _greige_roll(db, demo, kg="100")
    with pytest.raises(InsufficientStockError):
        _issue(db, demo, roll, qty=Decimal("500"))   # more than on hand


def test_duplicate_issue_rejected(db, demo):
    _worker(db, demo)
    update_process_mode(db, demo["company_id"], {"dye_outsourced": True})
    roll = _greige_roll(db, demo)
    _issue(db, demo, roll)
    with pytest.raises(cust.JobWorkCustodyError):
        _issue(db, demo, roll)           # already outstanding


def test_process_mode_enforced(db, demo):
    _worker(db, demo)
    roll = _greige_roll(db, demo)
    # dye_outsourced is OFF by default -> issuing for DYEING is blocked
    with pytest.raises(cust.JobWorkCustodyError):
        _issue(db, demo, roll, process="DYEING")
    # enabling it allows the issue
    update_process_mode(db, demo["company_id"], {"dye_outsourced": True})
    assert _issue(db, demo, roll, process="DYEING").id


# ---- isolation ----

def test_cross_company_jobworker_rejected(db, demo):
    update_process_mode(db, demo["company_id"], {"dye_outsourced": True})
    other = db.execute(select(Company).where(Company.code == "UATB")).scalar_one_or_none()
    if other is None:
        other = Company(code="UATB", name="UAT Weaves", legal_name="UAT Weaves Pvt Ltd",
                        gstin="27ABCDE9999F1Z2", state_code="27", currency="INR")
        db.add(other); db.flush()
    foreign = Supplier(company_id=other.id, code="JW-FOR", name="Foreign JW", state_code="27", is_job_worker=True)
    db.add(foreign); db.flush()
    roll = _greige_roll(db, demo)
    with pytest.raises(cust.JobWorkCustodyError):
        _issue(db, demo, roll, worker=foreign.id)   # worker belongs to another company


def test_audit_trail_recorded(db, demo):
    _worker(db, demo)
    update_process_mode(db, demo["company_id"], {"dye_outsourced": True})
    roll = _greige_roll(db, demo)
    actor = uuid.uuid4()
    iss = cust.issue_to_vendor(db, company_id=demo["company_id"], job_worker_id=demo["supplier_id"],
                               process="DYEING", item_type=ItemType.FABRIC, item_id=roll.id,
                               qty=Decimal(str(roll.weight_kg)), source_warehouse_id=roll.warehouse_id,
                               fabric_roll_id=roll.id, issue_date=WHEN, created_by=actor)
    assert iss.created_by == actor
    assert iss.posting_reference == f"JWISSUE:{iss.challan_no}"
    assert iss.process == "DYEING"
