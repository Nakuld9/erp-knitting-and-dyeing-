"""Stage 3E — job-work vendor bill entry, lot-wise matching & Direct Expense posting."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.core import Company
from app.models.inventory import ItemType
from app.models.jobwork import JobWorkBillStatus, JobWorkBillType
from app.models.manufacturing import KnittingProduction
from app.models.masters import Supplier
from app.services import jobwork, jobwork_billing as bill, jobwork_custody as cust
from app.services.accounting import trial_balance as tb
from app.services.hybrid import update_process_mode
from app.services.manufacturing.knitting import record_production

WHEN = date(2026, 11, 10)


def _worker(db, demo):
    s = db.get(Supplier, demo["supplier_id"]); s.is_job_worker = True; db.flush(); return s


def _order(db, demo, *, number, rate="20"):
    update_process_mode(db, demo["company_id"], {"dye_outsourced": True})
    return jobwork.create(db, company_id=demo["company_id"], number=number, direction="OUTWARD",
                          process="DYEING", party_id=demo["supplier_id"], order_date=WHEN,
                          qty_kg="100", rate=rate, tax_code_id=demo["tax_id"])


def _issue(db, demo, order, *, number, kg="100"):
    kp = KnittingProduction(company_id=demo["company_id"], number=number, machine_id=demo["machines"]["knit"],
                            yarn_id=demo["yarn_id"], yarn_lot_id=demo["yarn_lot_id"],
                            fabric_type_id=demo["fabric_type_id"], production_date=WHEN,
                            produced_kg=Decimal(kg), yarn_consumed_kg=Decimal("255"))
    db.add(kp); db.flush()
    roll = record_production(db, kp, [{"weight_kg": kg, "warehouse_id": demo["warehouses"]["greige"]}])["rolls"][0]
    return cust.issue_to_vendor(db, company_id=demo["company_id"], job_worker_id=demo["supplier_id"],
                                process="DYEING", item_type=ItemType.FABRIC, item_id=roll.id, qty=Decimal(kg),
                                source_warehouse_id=roll.warehouse_id, fabric_roll_id=roll.id,
                                job_work_id=order.id, issue_date=WHEN)


def _tb(db, demo, code):
    rows = tb.trial_balance(db, demo["company_id"])["rows"]
    return next((r for r in rows if r["account_code"] == code), None)


# ---- entry + Direct Expense posting + GST/TDS ----

def test_create_and_approve_posts_direct_expense_with_gst_tds(db, demo):
    _worker(db, demo)
    o = _order(db, demo, number="JWO-1", rate="20")
    iss = _issue(db, demo, o, number="KP-B1")
    b = bill.create_bill(db, company_id=demo["company_id"], supplier_id=demo["supplier_id"],
                         invoice_no="INV-1", invoice_date=WHEN, process="DYEING", tax_code_id=demo["tax_id"], tds_rate=Decimal("2"),
                         lines=[{"job_work_id": o.id, "job_work_issue_id": iss.id, "qty": "100", "rate": "20",
                                 "amount": "2000"}])
    assert b.taxable_amount == Decimal("2000.00")
    assert b.cgst == Decimal("180.00") and b.sgst == Decimal("180.00")   # 18% intra
    assert b.tds_amount == Decimal("40.00")                               # 2% of 2000
    assert b.total_amount == Decimal("2320.00")                          # 2000 + 360 - 40
    bill.approve_bill(db, b, created_by=None); db.commit()
    assert b.status == JobWorkBillStatus.APPROVED
    # Direct Expense (5090 Processing Charges) debited with the charge
    assert _tb(db, demo, "5090")["period_debit"] == Decimal("2000.00")
    # TDS Payable (2050) credited; vendor payable (2010) credited net
    assert _tb(db, demo, "2050")["period_credit"] == Decimal("40.00")
    assert _tb(db, demo, "2010")["period_credit"] == Decimal("2320.00")


# ---- multi-lot invoice ----

def test_multi_lot_invoice(db, demo):
    _worker(db, demo)
    o = _order(db, demo, number="JWO-2", rate="20")
    i1 = _issue(db, demo, o, number="KP-B2a")
    i2 = _issue(db, demo, o, number="KP-B2b")
    b = bill.create_bill(db, company_id=demo["company_id"], supplier_id=demo["supplier_id"],
                         invoice_no="INV-2", invoice_date=WHEN,
                         lines=[{"job_work_issue_id": i1.id, "amount": "2000"},
                                {"job_work_issue_id": i2.id, "amount": "2000"}])
    assert b.taxable_amount == Decimal("4000.00")
    assert bill.lot_reconciliation(db, demo["company_id"], i1.id)["amount_billed"] == Decimal("0.00")
    bill.approve_bill(db, b, created_by=None)
    assert bill.lot_reconciliation(db, demo["company_id"], i1.id)["amount_billed"] == Decimal("2000.00")


# ---- multiple invoices per lot + partial billing ----

def test_partial_and_multiple_invoices_per_lot(db, demo):
    _worker(db, demo)
    o = _order(db, demo, number="JWO-3", rate="20")           # agreed per lot = 100*20 = 2000
    iss = _issue(db, demo, o, number="KP-B3")
    b1 = bill.create_bill(db, company_id=demo["company_id"], supplier_id=demo["supplier_id"],
                          invoice_no="INV-3a", invoice_date=WHEN,
                          lines=[{"job_work_issue_id": iss.id, "amount": "1200"}])
    bill.approve_bill(db, b1, created_by=None)
    rec = bill.lot_reconciliation(db, demo["company_id"], iss.id)
    assert rec["agreed_charge"] == Decimal("2000.00")
    assert rec["amount_billed"] == Decimal("1200.00")
    assert rec["amount_unbilled"] == Decimal("800.00")
    # second invoice bills the remainder
    b2 = bill.create_bill(db, company_id=demo["company_id"], supplier_id=demo["supplier_id"],
                          invoice_no="INV-3b", invoice_date=WHEN,
                          lines=[{"job_work_issue_id": iss.id, "amount": "800"}])
    bill.approve_bill(db, b2, created_by=None)
    assert bill.lot_reconciliation(db, demo["company_id"], iss.id)["amount_unbilled"] == Decimal("0.00")


# ---- duplicate invoice + over-allocation prevention ----

def test_duplicate_invoice_rejected(db, demo):
    _worker(db, demo)
    o = _order(db, demo, number="JWO-4"); iss = _issue(db, demo, o, number="KP-B4")
    bill.create_bill(db, company_id=demo["company_id"], supplier_id=demo["supplier_id"],
                     invoice_no="DUP-1", invoice_date=WHEN, lines=[{"job_work_issue_id": iss.id, "amount": "100"}])
    with pytest.raises(bill.JobWorkBillError):
        bill.create_bill(db, company_id=demo["company_id"], supplier_id=demo["supplier_id"],
                         invoice_no="DUP-1", invoice_date=WHEN, lines=[{"job_work_issue_id": iss.id, "amount": "100"}])


def test_over_allocation_blocked_without_authorisation(db, demo):
    _worker(db, demo)
    o = _order(db, demo, number="JWO-5", rate="20"); iss = _issue(db, demo, o, number="KP-B5")  # agreed 2000
    with pytest.raises(bill.JobWorkBillError):
        bill.create_bill(db, company_id=demo["company_id"], supplier_id=demo["supplier_id"],
                         invoice_no="OV-1", invoice_date=WHEN, lines=[{"job_work_issue_id": iss.id, "amount": "2500"}])
    # with authorisation it is allowed
    b = bill.create_bill(db, company_id=demo["company_id"], supplier_id=demo["supplier_id"],
                         invoice_no="OV-2", invoice_date=WHEN, authorize_over_allocation=True,
                         lines=[{"job_work_issue_id": iss.id, "amount": "2500"}])
    assert b.taxable_amount == Decimal("2500.00")


# ---- credit note adjustment ----

def test_credit_note_reduces_billed_and_posts_reverse(db, demo):
    _worker(db, demo)
    o = _order(db, demo, number="JWO-6", rate="20"); iss = _issue(db, demo, o, number="KP-B6")
    inv = bill.create_bill(db, company_id=demo["company_id"], supplier_id=demo["supplier_id"],
                           invoice_no="INV-6", invoice_date=WHEN, process="DYEING",
                           lines=[{"job_work_issue_id": iss.id, "amount": "2000"}])
    bill.approve_bill(db, inv, created_by=None)
    cn = bill.create_bill(db, company_id=demo["company_id"], supplier_id=demo["supplier_id"],
                          invoice_no="CN-6", invoice_date=WHEN, bill_type="CREDIT_NOTE", process="DYEING",
                          lines=[{"job_work_issue_id": iss.id, "amount": "500"}])
    bill.approve_bill(db, cn, created_by=None); db.commit()
    # net billed = 2000 - 500
    assert bill.lot_reconciliation(db, demo["company_id"], iss.id)["amount_billed"] == Decimal("1500.00")
    # expense net of the credit note
    assert _tb(db, demo, "5090")["period_debit"] - _tb(db, demo, "5090")["period_credit"] == Decimal("1500.00")


# ---- vendor summary + inventory separation ----

def test_vendor_summary_and_inventory_untouched(db, demo):
    _worker(db, demo)
    o = _order(db, demo, number="JWO-7", rate="20"); iss = _issue(db, demo, o, number="KP-B7")
    before_resolved = Decimal(str(iss.resolved_input_qty))
    from app.services.inventory import service as inv
    before_qty = inv.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC, item_id=iss.item_id)["qty_on_hand"]
    b = bill.create_bill(db, company_id=demo["company_id"], supplier_id=demo["supplier_id"],
                         invoice_no="INV-7", invoice_date=WHEN, lines=[{"job_work_issue_id": iss.id, "amount": "2000"}])
    bill.approve_bill(db, b, created_by=None)
    # billing does NOT change custody / outstanding material / inventory
    db.refresh(iss)
    assert Decimal(str(iss.resolved_input_qty)) == before_resolved
    after_qty = inv.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC, item_id=iss.item_id)["qty_on_hand"]
    assert after_qty == before_qty
    # no tax_code / tds on this bill -> net payable == taxable
    assert bill.vendor_bill_summary(db, demo["company_id"], demo["supplier_id"])["net_billed_payable"] == Decimal("2000.00")


# ---- company isolation ----

def test_cross_company_supplier_rejected(db, demo):
    other = db.execute(select(Company).where(Company.code == "UATB")).scalar_one_or_none()
    if other is None:
        other = Company(code="UATB", name="UAT Weaves", legal_name="UAT Weaves Pvt Ltd",
                        gstin="27ABCDE9999F1Z2", state_code="27", currency="INR")
        db.add(other); db.flush()
    foreign = Supplier(company_id=other.id, code="JWB-FOR", name="Foreign JW", state_code="27", is_job_worker=True)
    db.add(foreign); db.flush()
    with pytest.raises(bill.JobWorkBillError):
        bill.create_bill(db, company_id=demo["company_id"], supplier_id=foreign.id,
                         invoice_no="X-1", invoice_date=WHEN, lines=[{"amount": "100"}])
