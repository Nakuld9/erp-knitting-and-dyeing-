"""Stage 3F — hardening of the Stage 3E job-work billing review findings."""
from __future__ import annotations

import os
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.core import Branch, Company
from app.models.inventory import ItemType
from app.models.jobwork import (
    JobWork, JobWorkBill, JobWorkBillStatus, JobWorkStatus,
)
from app.models.manufacturing import KnittingProduction
from app.models.masters import Supplier
from app.services import jobwork, jobwork_billing as bill, jobwork_custody as cust
from app.services.accounting import trial_balance as tb
from app.services.hybrid import update_process_mode
from app.services.manufacturing.knitting import record_production

WHEN = date(2026, 11, 10)


def _worker(db, demo):
    s = db.get(Supplier, demo["supplier_id"]); s.is_job_worker = True; db.flush(); return s


def _order(db, demo, *, number, rate="20", qty="100"):
    update_process_mode(db, demo["company_id"], {"dye_outsourced": True})
    return jobwork.create(db, company_id=demo["company_id"], number=number, direction="OUTWARD",
                          process="DYEING", party_id=demo["supplier_id"], order_date=WHEN,
                          qty_kg=qty, rate=rate, tax_code_id=demo["tax_id"])


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


def _bill(db, demo, *, invoice_no, amount, issue=None, **kw):
    lines = [{"job_work_issue_id": (issue.id if issue else None), "amount": amount}]
    return bill.create_bill(db, company_id=demo["company_id"], supplier_id=demo["supplier_id"],
                            invoice_no=invoice_no, invoice_date=WHEN, process="DYEING", lines=lines, **kw)


# ---- H1: cross-path duplicate billing ----

def test_legacy_then_new_blocked(db, demo):
    _worker(db, demo)
    o = _order(db, demo, number="XP-1"); iss = _issue(db, demo, o, number="KP-X1")
    jobwork.bill(db, o, created_by=None)                      # legacy posts, order -> BILLED
    nb = bill.create_bill(db, company_id=demo["company_id"], supplier_id=demo["supplier_id"],
                          invoice_no="NB-1", invoice_date=WHEN, process="DYEING",
                          lines=[{"job_work_id": o.id, "job_work_issue_id": iss.id, "amount": "100"}])
    with pytest.raises(bill.JobWorkBillError):
        bill.approve_bill(db, nb, created_by=None)            # blocked: already billed via legacy


def test_new_then_legacy_blocked(db, demo):
    _worker(db, demo)
    o = _order(db, demo, number="XP-2"); iss = _issue(db, demo, o, number="KP-X2")
    nb = bill.create_bill(db, company_id=demo["company_id"], supplier_id=demo["supplier_id"],
                          invoice_no="NB-2", invoice_date=WHEN, process="DYEING",
                          lines=[{"job_work_id": o.id, "job_work_issue_id": iss.id, "amount": "100"}])
    bill.approve_bill(db, nb, created_by=None)
    with pytest.raises(jobwork.JobWorkError):
        jobwork.bill(db, o, created_by=None)                 # legacy blocked: already billed via new path


# ---- H2/M3: note linkage + limits ----

def test_note_requires_original_and_matches(db, demo):
    _worker(db, demo)
    with pytest.raises(bill.JobWorkBillError):                # no original
        _bill(db, demo, invoice_no="CN-A", amount="100", bill_type="CREDIT_NOTE")


def test_credit_note_within_and_exceeding_limit(db, demo):
    _worker(db, demo)
    o = _order(db, demo, number="XP-3"); iss = _issue(db, demo, o, number="KP-X3")
    inv = _bill(db, demo, invoice_no="INV-A", amount="2000", issue=iss)
    bill.approve_bill(db, inv, created_by=None)
    # within eligible (<=2000) ok
    cn1 = _bill(db, demo, invoice_no="CN-1", amount="500", issue=iss, bill_type="CREDIT_NOTE", original_bill_id=inv.id)
    assert cn1.taxable_amount == Decimal("500.00")
    # a second partial note (cumulative 500+1500 = 2000) ok
    cn2 = _bill(db, demo, invoice_no="CN-2", amount="1500", issue=iss, bill_type="CREDIT_NOTE", original_bill_id=inv.id)
    assert cn2.taxable_amount == Decimal("1500.00")
    # exceeding the remaining (0 left) is rejected
    with pytest.raises(bill.JobWorkBillError):
        _bill(db, demo, invoice_no="CN-3", amount="1", issue=iss, bill_type="CREDIT_NOTE", original_bill_id=inv.id)


def test_note_cross_supplier_rejected(db, demo):
    _worker(db, demo)
    o = _order(db, demo, number="XP-4"); iss = _issue(db, demo, o, number="KP-X4")
    inv = _bill(db, demo, invoice_no="INV-B", amount="2000", issue=iss)
    bill.approve_bill(db, inv, created_by=None)
    other = Supplier(company_id=demo["company_id"], code="JW2", name="Other JW", state_code="03", is_job_worker=True)
    db.add(other); db.flush()
    with pytest.raises(bill.JobWorkBillError):
        bill.create_bill(db, company_id=demo["company_id"], supplier_id=other.id, invoice_no="CN-X",
                         invoice_date=WHEN, bill_type="CREDIT_NOTE", original_bill_id=inv.id,
                         lines=[{"amount": "100"}])


# ---- M6 / H2: debit note + credit note posting gates ----

def test_debit_note_posting_blocked(db, demo):
    _worker(db, demo)
    o = _order(db, demo, number="XP-5"); iss = _issue(db, demo, o, number="KP-X5")
    inv = _bill(db, demo, invoice_no="INV-C", amount="2000", issue=iss); bill.approve_bill(db, inv, created_by=None)
    dn = _bill(db, demo, invoice_no="DN-1", amount="100", issue=iss, bill_type="DEBIT_NOTE", original_bill_id=inv.id)
    with pytest.raises(bill.JobWorkPostingBlocked):
        bill.approve_bill(db, dn, created_by=None)            # treatment not established


def test_credit_note_blocked_until_policy_approved(db, demo):
    _worker(db, demo)
    o = _order(db, demo, number="XP-6"); iss = _issue(db, demo, o, number="KP-X6")
    inv = _bill(db, demo, invoice_no="INV-D", amount="2000", issue=iss); bill.approve_bill(db, inv, created_by=None)
    cn = _bill(db, demo, invoice_no="CN-D", amount="500", issue=iss, bill_type="CREDIT_NOTE", original_bill_id=inv.id)
    with pytest.raises(bill.JobWorkPostingBlocked):
        bill.approve_bill(db, cn, created_by=None)
    bill.set_policy(db, demo["company_id"], credit_note=True)
    assert bill.approve_bill(db, cn, created_by=None) is not None


# ---- H3: over-allocation authorisation ----

def test_over_allocation_requires_auth_and_reason(db, demo):
    _worker(db, demo)
    o = _order(db, demo, number="XP-7"); iss = _issue(db, demo, o, number="KP-X7")   # agreed 2000
    with pytest.raises(bill.JobWorkBillError):                                        # unauthorised
        _bill(db, demo, invoice_no="OV-A", amount="2500", issue=iss)
    with pytest.raises(bill.JobWorkBillError):                                        # authorised but no reason
        _bill(db, demo, invoice_no="OV-B", amount="2500", issue=iss,
              over_allocation_authorized=True, over_allocation_reason="  ", authorized_by=uuid.uuid4())
    actor = uuid.uuid4()
    b = _bill(db, demo, invoice_no="OV-C", amount="2500", issue=iss,
              over_allocation_authorized=True, over_allocation_reason="extra rework", authorized_by=actor)
    assert b.is_over_allocated and b.over_allocated_by == actor and b.over_allocated_at is not None


def test_api_ordinary_user_cannot_over_allocate(client, auth_headers, db, demo):
    # auth_headers is the admin/superuser; make a plain voucher.create user instead
    from app.core.security import hash_password
    from app.models.core import User, Role
    _worker(db, demo)
    o = _order(db, demo, number="XP-8"); iss = _issue(db, demo, o, number="KP-X8"); db.commit()
    cid = demo["company_id"]
    # a clerk with voucher.create but NOT jobwork.over_allocate
    role = db.execute(select(Role).where(Role.code == "PURCHASE_MANAGER")).scalar_one_or_none()
    u = User(username="billclerk", email="bc@demo.local", full_name="Bill Clerk",
             hashed_password=hash_password("clerkpw123"), is_active=True, is_superuser=False, company_id=cid)
    from app.models.core import Permission
    vc = db.execute(select(Permission).where(Permission.code == "voucher.create")).scalar_one()
    from app.models.core import Role as R
    r = R(code="BILLCLERK", name="Bill Clerk"); r.permissions.append(vc); db.add(r); u.roles.append(r)
    db.add(u); db.commit()
    tok = client.post("/api/auth/login", data={"username": "billclerk", "password": "clerkpw123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    r = client.post("/api/jobwork/bills", headers=h, json={
        "supplier_id": str(demo["supplier_id"]), "invoice_no": "APIOV-1", "invoice_date": "2026-11-10",
        "process": "DYEING", "lines": [{"job_work_issue_id": str(iss.id), "amount": "2500"}]})
    assert r.status_code == 422        # over-allocation not authorised for this user


# ---- M1: agreed-charge snapshot stability ----

def test_agreed_charge_stable_after_order_rate_change(db, demo):
    _worker(db, demo)
    o = _order(db, demo, number="XP-9", rate="20"); iss = _issue(db, demo, o, number="KP-X9")
    _bill(db, demo, invoice_no="SNP-1", amount="1000", issue=iss)                 # freezes agreed at 2000
    o.rate = Decimal("50"); db.flush()                                            # order rate changes later
    assert bill.lot_reconciliation(db, demo["company_id"], iss.id)["agreed_charge"] == Decimal("2000.00")


def test_agreed_charge_stable_after_receipt_change(db, demo):
    _worker(db, demo)
    o = _order(db, demo, number="XP-10", rate="20"); iss = _issue(db, demo, o, number="KP-X10")
    _bill(db, demo, invoice_no="SNP-2", amount="1000", issue=iss)                 # freeze 2000
    iss.accepted_output_qty = Decimal("40"); db.flush()                          # a later receipt changes accepted
    assert bill.lot_reconciliation(db, demo["company_id"], iss.id)["agreed_charge"] == Decimal("2000.00")


# ---- H4: TDS validation ----

def test_tds_section_missing_config_blocks(db, demo):
    _worker(db, demo)
    o = _order(db, demo, number="XP-11"); iss = _issue(db, demo, o, number="KP-X11")
    with pytest.raises(bill.JobWorkPostingBlocked):
        _bill(db, demo, invoice_no="TDS-1", amount="2000", issue=iss, tds_section_code="194C")


def test_tds_negative_and_invalid_rejected(db, demo):
    with pytest.raises(bill.JobWorkBillError):
        bill.set_tds_rate(db, demo["company_id"], section_code="BAD", rate=Decimal("-2"))
    with pytest.raises(bill.JobWorkBillError):
        bill.set_tds_rate(db, demo["company_id"], section_code="BAD2", rate=Decimal("150"))
    with pytest.raises(bill.JobWorkBillError):
        bill.set_tds_rate(db, demo["company_id"], section_code="BAD3", rate="not-a-number")


# ---- M5: factory / company validation ----

def test_cross_company_factory_rejected(db, demo):
    _worker(db, demo)
    other = db.execute(select(Company).where(Company.code == "UATB")).scalar_one_or_none()
    if other is None:
        other = Company(code="UATB", name="UAT Weaves", legal_name="UAT Weaves Pvt Ltd",
                        gstin="27ABCDE9999F1Z2", state_code="27", currency="INR")
        db.add(other); db.flush()
    foreign_factory = Branch(company_id=other.id, code="FF", name="Foreign Factory"); db.add(foreign_factory); db.flush()
    with pytest.raises(bill.JobWorkBillError):
        bill.create_bill(db, company_id=demo["company_id"], supplier_id=demo["supplier_id"],
                         invoice_no="FAC-1", invoice_date=WHEN, factory_id=foreign_factory.id,
                         lines=[{"amount": "100"}])


# ---- M4: advance distinction + posting block ----

def test_advance_distinct_and_posting_blocked(db, demo):
    _worker(db, demo)
    o = _order(db, demo, number="XP-12"); iss = _issue(db, demo, o, number="KP-X12")
    adv = _bill(db, demo, invoice_no="ADV-1", amount="500", issue=iss, is_advance=True)
    assert adv.is_advance is True
    with pytest.raises(bill.JobWorkPostingBlocked):
        bill.approve_bill(db, adv, created_by=None)


# ---- L2: approval actor recorded ----

def test_approval_actor_recorded(db, demo):
    _worker(db, demo)
    o = _order(db, demo, number="XP-13"); iss = _issue(db, demo, o, number="KP-X13")
    b = _bill(db, demo, invoice_no="ACT-1", amount="2000", issue=iss)
    actor = uuid.uuid4()
    bill.approve_bill(db, b, created_by=actor)
    assert b.approved_by == actor and b.approved_at is not None


# ---- L3: unallocated + disputed visible ----

def test_unallocated_and_disputed_visible(db, demo):
    _worker(db, demo)
    o = _order(db, demo, number="XP-14"); iss = _issue(db, demo, o, number="KP-X14")
    b = bill.create_bill(db, company_id=demo["company_id"], supplier_id=demo["supplier_id"],
                         invoice_no="UM-1", invoice_date=WHEN, process="DYEING",
                         lines=[{"job_work_issue_id": iss.id, "amount": "1800", "is_disputed": True,
                                 "disputed_amount": "200"},
                                {"amount": "300"}])        # second line unallocated (no lot)
    bill.approve_bill(db, b, created_by=None)
    assert bill.bill_unmatched_amount(db, demo["company_id"], b.id) == Decimal("300.00")
    rec = bill.lot_reconciliation(db, demo["company_id"], iss.id)
    assert rec["disputed_amount"] == Decimal("200.00")


# ---- L4: rounding consistency ----

def test_rounding_consistency(db, demo):
    _worker(db, demo)
    bill.set_tds_rate(db, demo["company_id"], section_code="194C", rate=Decimal("2"))
    o = _order(db, demo, number="XP-15"); iss = _issue(db, demo, o, number="KP-X15")
    b = _bill(db, demo, invoice_no="RND-1", amount="333.33", issue=iss,
              tax_code_id=demo["tax_id"], tds_section_code="194C", over_allocation_authorized=False)
    # taxable 333.33; gst 18% -> 59.9994 -> 29.9997+29.9997 rounded; tds 2% -> 6.6666 -> 6.67
    assert b.tds_amount == Decimal("6.67")
    assert b.total_amount == b.taxable_amount + b.cgst + b.sgst + b.igst - b.tds_amount


# ---- inventory invariant ----

def test_billing_leaves_inventory_unchanged(db, demo):
    _worker(db, demo)
    from app.services.inventory import service as inv
    o = _order(db, demo, number="XP-16"); iss = _issue(db, demo, o, number="KP-X16")
    before = inv.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC, item_id=iss.item_id)["qty_on_hand"]
    before_res = Decimal(str(iss.resolved_input_qty))
    b = _bill(db, demo, invoice_no="INVN-1", amount="2000", issue=iss)
    bill.approve_bill(db, b, created_by=None); db.refresh(iss)
    after = inv.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC, item_id=iss.item_id)["qty_on_hand"]
    assert after == before and Decimal(str(iss.resolved_input_qty)) == before_res


# =============================== PostgreSQL concurrency ===============================

PG_URL = os.environ.get("PG_TEST_URL")
pg_only = pytest.mark.skipif(not PG_URL, reason="PG_TEST_URL not set (needs a real PostgreSQL)")


@pg_only
def test_pg_concurrent_duplicate_invoice():
    """Two concurrent creates of the same (supplier, invoice_no) — exactly one wins."""
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.exc import IntegrityError
    from app.models import Base
    from app.seed.seed import run_seed
    from app.seed.demo import seed_manufacturing_demo

    engine = create_engine(PG_URL, future=True)
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        info = run_seed(s, fy_start_year=2026); cid = uuid.UUID(info["company_id"])
        h = seed_manufacturing_demo(s, cid); sup = h["supplier_id"]
        sobj = s.get(Supplier, sup); sobj.is_job_worker = True; s.commit()

    def _create():
        with Session() as s:
            try:
                bill.create_bill(s, company_id=cid, supplier_id=sup, invoice_no="DUP-PG",
                                 invoice_date=WHEN, lines=[{"amount": "100"}])
                s.commit(); return "ok"
            except (IntegrityError, bill.JobWorkBillError):
                s.rollback(); return "dup"
    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(lambda _: _create(), range(2)))
    with Session() as s:
        n = s.execute(select(func.count()).select_from(JobWorkBill).where(
            JobWorkBill.company_id == cid, JobWorkBill.invoice_no == "DUP-PG")).scalar()
    assert n == 1 and results.count("ok") == 1
    Base.metadata.drop_all(engine)


@pg_only
def test_pg_concurrent_over_allocation_blocked():
    """Two drafts each under the agreed cap; approving both concurrently must not
    let combined billing exceed the agreed charge (row-lock revalidation)."""
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.seed.seed import run_seed
    from app.seed.demo import seed_manufacturing_demo

    engine = create_engine(PG_URL, future=True)
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        info = run_seed(s, fy_start_year=2026); cid = uuid.UUID(info["company_id"])
        h = seed_manufacturing_demo(s, cid); sup = h["supplier_id"]
        sobj = s.get(Supplier, sup); sobj.is_job_worker = True
        update_process_mode(s, cid, {"dye_outsourced": True})
        order = jobwork.create(s, company_id=cid, number="PGO", direction="OUTWARD", process="DYEING",
                               party_id=sup, order_date=WHEN, qty_kg="100", rate="20")   # agreed 2000
        kp = KnittingProduction(company_id=cid, number="PGKP", machine_id=h["machines"]["knit"],
                                yarn_id=h["yarn_id"], yarn_lot_id=h["yarn_lot_id"], fabric_type_id=h["fabric_type_id"],
                                production_date=WHEN, produced_kg=Decimal("100"), yarn_consumed_kg=Decimal("255"))
        s.add(kp); s.flush()
        roll = record_production(s, kp, [{"weight_kg": "100", "warehouse_id": h["warehouses"]["greige"]}])["rolls"][0]
        iss = cust.issue_to_vendor(s, company_id=cid, job_worker_id=sup, process="DYEING",
                                   item_type=ItemType.FABRIC, item_id=roll.id, qty=Decimal("100"),
                                   source_warehouse_id=roll.warehouse_id, fabric_roll_id=roll.id,
                                   job_work_id=order.id, issue_date=WHEN)
        # two drafts of 1500 each (each alone <= 2000; together 3000 > 2000)
        b1 = bill.create_bill(s, company_id=cid, supplier_id=sup, invoice_no="PG-1", invoice_date=WHEN,
                              lines=[{"job_work_issue_id": iss.id, "amount": "1500"}])
        b2 = bill.create_bill(s, company_id=cid, supplier_id=sup, invoice_no="PG-2", invoice_date=WHEN,
                              lines=[{"job_work_issue_id": iss.id, "amount": "1500"}])
        s.commit(); b1id, b2id, iid = b1.id, b2.id, iss.id

    def _approve(bid):
        with Session() as s:
            try:
                b = s.get(JobWorkBill, bid); bill.approve_bill(s, b, created_by=None); s.commit(); return "ok"
            except bill.JobWorkBillError:
                s.rollback(); return "blocked"
    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(_approve, [b1id, b2id]))
    with Session() as s:
        net = bill._net_billed_for_issue(s, cid, iid)
    assert net <= Decimal("2000.00") + Decimal("0.01")
    assert "blocked" in results or net <= Decimal("2000.00")
    Base.metadata.drop_all(engine)
