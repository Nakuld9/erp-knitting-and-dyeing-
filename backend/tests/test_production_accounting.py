"""GL journal integration for the BOM-driven grey→dyed workflow.

Verifies balanced double-entry postings through the existing posting engine,
idempotency, period locks, reversals, and inventory-to-GL reconciliation.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.accounting import (
    AccountingPeriod, ChartOfAccount, JournalLine, PeriodStatus, Voucher,
)
from app.models.inventory import ItemType
from app.services.accounting import posting_engine as pe
from app.services.accounting import production_reconciliation as prec
from app.services.accounting.trial_balance import trial_balance
from app.services.bom import production, service as bom
from app.services.dyeing import service as dyeing
from app.services.inventory import service as inventory

VD = date(2026, 6, 15)   # a date inside an open FY2026 period


def _voucher(db, company_id, ref):
    return db.execute(select(Voucher).where(
        Voucher.company_id == company_id, Voucher.posting_reference == ref)).scalar_one_or_none()


def _line_by_code(db, voucher, code):
    """(debit, credit) summed for the lines of account `code` in a voucher."""
    acct = db.execute(select(ChartOfAccount).where(
        ChartOfAccount.company_id == voucher.company_id, ChartOfAccount.code == code)).scalar_one()
    d = c = Decimal("0")
    for ln in voucher.lines:
        if ln.account_id == acct.id:
            d += Decimal(str(ln.debit)); c += Decimal(str(ln.credit))
    return d, c


def _grey_order(db, demo, *, code, qty="100", conversion="600"):
    b = bom.create_bom(db, company_id=demo["company_id"], code=code, name=code)
    v = b.versions[0]
    bom.set_yarns(db, v.id, [{"yarn_id": demo["yarn_id"], "blend_percent": Decimal("100"),
                              "std_consumption_per_kg": Decimal("1.0"), "std_unit_cost": Decimal("240")}])
    bom.approve_version(db, version_id=v.id, approved_by=None)
    o = production.create_order(db, company_id=demo["company_id"], bom_version_id=v.id,
                               planned_qty_kg=Decimal(qty), number="PO-"+code, order_date=VD)
    res = o.reservations[0]
    production.issue_material(db, order_id=o.id, reservation_id=res.id, qty=res.planned_qty)
    production.complete_order(db, order_id=o.id, produced_qty_kg=Decimal(qty))
    if conversion is not None:
        production.record_actual_knitting_conversion_cost(db, order_id=o.id, amount=Decimal(conversion))
    production.value_grey_output(db, order_id=o.id, voucher_date=VD)
    return o


def _dye_job(db, demo, order, *, number, mode="INTERNAL", costs=None, lots=None, **kw):
    job = dyeing.create_job(db, company_id=demo["company_id"], number=number, mode=mode,
                            grey_production_order_id=order.id,
                            jobwork_supplier_id=(demo["supplier_id"] if mode == "JOBWORK" else None))
    dyeing.issue_grey(db, job_id=job.id, source_production_order_id=order.id,
                     qty_kg=order.produced_qty_kg, voucher_date=VD)
    if costs:
        dyeing.record_costs(db, job_id=job.id, **costs)
    for lot in (lots or [{"lot_code": "L1", "qty_kg": Decimal("95")}]):
        dyeing.record_output(db, job_id=job.id, lots=[lot])
    dyeing.complete_job(db, job_id=job.id, voucher_date=VD, **kw)
    return job


# --- A. Knitting → grey journal flow ----------------------------------------

def test_grey_journal_balanced_and_wip_zero(db, demo):
    o = _grey_order(db, demo, code="ACC1", qty="100", conversion="600")
    cid = demo["company_id"]
    # HIGH-2: yarn->WIP is posted at ISSUE time (its own voucher), not in the grey voucher
    iv = db.execute(select(Voucher).where(
        Voucher.company_id == cid, Voucher.posting_reference.like(f"PO:{o.number}:issue:%"))).scalar_one()
    assert iv.total_debit == iv.total_credit
    iv_wip_d, _ = _line_by_code(db, iv, "1080")
    _, iv_yarn_c = _line_by_code(db, iv, "1060")
    assert iv_wip_d == iv_yarn_c == Decimal("24000.00")    # Dr WIP / Cr Yarn at issue
    # grey voucher: conversion into WIP + WIP -> greige (no yarn lines)
    v = _voucher(db, cid, f"PO:{o.number}:grey")
    assert v.total_debit == v.total_credit
    assert _line_by_code(db, v, "1060") == (Decimal("0"), Decimal("0"))   # no yarn here
    greige_d, _ = _line_by_code(db, v, "1070")
    _, conv_c = _line_by_code(db, v, "2020")
    assert conv_c == Decimal("600.00")
    assert greige_d == Decimal("24600.00")                 # grey receipt == grey cost (24000 + 600)
    # WIP across BOTH order vouchers nets to zero
    wD = wC = Decimal("0")
    for vv in (iv, v):
        d, c = _line_by_code(db, vv, "1080"); wD += d; wC += c
    assert wD - wC == Decimal("0.00")
    bal = inventory.balance(db, company_id=cid, item_type=ItemType.FABRIC, item_id=o.id)
    assert bal["value"] == greige_d
    assert trial_balance(db, cid)["is_balanced"]
    # yarn sub-ledger reduction now equals the GL 1060 reduction immediately
    from app.services.accounting.stock_reconciliation import stock_ledger_reconciliation
    rec = stock_ledger_reconciliation(db, cid)
    assert rec["is_reconciled"] is True


def test_grey_journal_without_conversion_still_balances(db, demo):
    o = _grey_order(db, demo, code="ACC2", qty="50", conversion=None)
    v = _voucher(db, demo["company_id"], f"PO:{o.number}:grey")
    assert v.total_debit == v.total_credit
    _, conv_c = _line_by_code(db, v, "2020")
    assert conv_c == Decimal("0")                          # no conversion line
    greige_d, _ = _line_by_code(db, v, "1070")
    assert greige_d == Decimal("12000.00")                 # 50 * 1.0 * 240 (no wastage here)


# --- B. Dyeing journal flow -------------------------------------------------

def test_dyeing_journal_internal_balanced(db, demo):
    o = _grey_order(db, demo, code="ACC3")
    job = _dye_job(db, demo, o, number="DJ-ACC3",
                   costs={"dyeing_charge": Decimal("3000"), "chemical_cost": Decimal("1500")})
    # grey-issue voucher: greige credit == grey cost consumed
    gi = db.execute(select(Voucher).where(
        Voucher.company_id == demo["company_id"],
        Voucher.posting_reference.like(f"DYE:{job.number}:grey:%"))).scalar_one()
    _, greige_c = _line_by_code(db, gi, "1070")
    assert greige_c == Decimal("24600.00")
    # dyed voucher: FG debit == total accumulated; WIP of the job nets to zero
    dv = _voucher(db, demo["company_id"], f"DYE:{job.number}:dyed")
    assert dv.total_debit == dv.total_credit
    fg_d, _ = _line_by_code(db, dv, "1085")
    assert fg_d == Decimal("29100.00")                     # 24600 + 3000 + 1500
    _, exp_c = _line_by_code(db, dv, "2020")
    assert exp_c == Decimal("4500.00")                     # 3000 + 1500 accrued
    assert trial_balance(db, demo["company_id"])["is_balanced"]


def test_dyeing_jobwork_credits_accounts_payable(db, demo):
    o = _grey_order(db, demo, code="ACC4", qty="40", conversion="200")
    job = _dye_job(db, demo, o, number="DJ-ACC4", mode="JOBWORK",
                   costs={"jobwork_charge": Decimal("2000"), "chemical_cost": Decimal("500")},
                   lots=[{"lot_code": "J1", "qty_kg": Decimal("38")}])
    dv = _voucher(db, demo["company_id"], f"DYE:{job.number}:dyed")
    _, ap_c = _line_by_code(db, dv, "2010")                # accounts payable (subcontractor)
    assert ap_c == Decimal("2000.00")
    _, exp_c = _line_by_code(db, dv, "2020")
    assert exp_c == Decimal("500.00")                      # chemical accrued
    assert dv.total_debit == dv.total_credit


def test_dyed_receipt_matches_inventory_and_multiple_lots(db, demo):
    o = _grey_order(db, demo, code="ACC5")
    job = _dye_job(db, demo, o, number="DJ-ACC5",
                   costs={"dyeing_charge": Decimal("2000"), "chemical_cost": Decimal("1000")},
                   lots=[{"lot_code": "A", "qty_kg": Decimal("60")}, {"lot_code": "B", "qty_kg": Decimal("30")}],
                   rejection_kg=Decimal("5"))
    dv = _voucher(db, demo["company_id"], f"DYE:{job.number}:dyed")
    fg_d, _ = _line_by_code(db, dv, "1085")
    total = Decimal("24600") + Decimal("2000") + Decimal("1000")
    assert fg_d == total
    # dyed FABRIC sub-ledger (both lots) equals the GL finished debit
    dyed_val = sum((inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC,
                                      item_id=out.dyed_item_id)["value"] for out in job.outputs), Decimal("0"))
    assert dyed_val == fg_d


# --- Controls: idempotency, period lock, reversal ---------------------------

def test_posting_idempotent_and_duplicate_guarded(db, demo):
    o = _grey_order(db, demo, code="ACC6")
    v1 = _voucher(db, demo["company_id"], f"PO:{o.number}:grey")
    # re-valuation is refused at the app level (grey already valued)
    with pytest.raises(production.ProductionError):
        production.value_grey_output(db, order_id=o.id, voucher_date=VD)
    # the posting engine is idempotent on the unique posting_reference
    again = pe.post_voucher(db, pe.PostingRequest(
        company_id=demo["company_id"], voucher_type_code="PJ", voucher_date=VD,
        posting_reference=f"PO:{o.number}:grey", narration="dup",
        lines=[pe.PostingLine("1080", debit=Decimal("1")), pe.PostingLine("1060", credit=Decimal("1"))]))
    assert again.id == v1.id                               # returns the existing voucher


def test_period_lock_blocks_posting(db, demo):
    # close the June-2026 period, then valuation posting must be refused
    o_number = "PO-ACCLOCK"
    b = bom.create_bom(db, company_id=demo["company_id"], code="ACCLOCK", name="x")
    v = b.versions[0]
    bom.set_yarns(db, v.id, [{"yarn_id": demo["yarn_id"], "blend_percent": Decimal("100"),
                              "std_consumption_per_kg": Decimal("1.0"), "std_unit_cost": Decimal("240")}])
    bom.approve_version(db, version_id=v.id, approved_by=None)
    o = production.create_order(db, company_id=demo["company_id"], bom_version_id=v.id,
                               planned_qty_kg=Decimal("10"), number=o_number, order_date=VD)
    res = o.reservations[0]
    production.issue_material(db, order_id=o.id, reservation_id=res.id, qty=res.planned_qty)
    production.complete_order(db, order_id=o.id, produced_qty_kg=Decimal("10"))
    period = db.execute(select(AccountingPeriod).where(
        AccountingPeriod.company_id == demo["company_id"],
        AccountingPeriod.start_date <= VD, AccountingPeriod.end_date >= VD)).scalar_one()
    period.status = PeriodStatus.CLOSED
    db.flush()
    with pytest.raises(pe.ClosedPeriodError):
        production.value_grey_output(db, order_id=o.id, voucher_date=VD)


def test_reversal_entry(db, demo):
    o = _grey_order(db, demo, code="ACC7")
    v = _voucher(db, demo["company_id"], f"PO:{o.number}:grey")
    rev = pe.reverse_voucher(db, v.id, reason="correction", reversal_date=VD)
    assert rev.posting_reference == f"REVERSAL:PO:{o.number}:grey"
    # original + reversal net to zero on greige; trial balance still balances
    tb = trial_balance(db, demo["company_id"])
    assert tb["is_balanced"]
    greige_d, greige_c = _line_by_code(db, rev, "1070")
    assert greige_c == Decimal("24600.00")                 # reversal credits greige (swap)


# --- Reconciliation + isolation ---------------------------------------------

def test_inventory_to_gl_reconciliation(db, demo):
    o = _grey_order(db, demo, code="ACC8")
    job = _dye_job(db, demo, o, number="DJ-ACC8",
                   costs={"dyeing_charge": Decimal("3000"), "chemical_cost": Decimal("1500")})
    rec = prec.production_ledger_reconciliation(db, demo["company_id"])
    assert rec["is_reconciled"], rec
    # per-order and per-job reconcile
    assert prec.grey_order_reconciliation(db, demo["company_id"], o.id)["is_reconciled"]
    assert prec.dyeing_job_reconciliation(db, demo["company_id"], job.id)["is_reconciled"]


def test_reconciliation_company_isolation(db, demo):
    from app.models.core import Company
    o = _grey_order(db, demo, code="ACC9")
    _dye_job(db, demo, o, number="DJ-ACC9", costs={"dyeing_charge": Decimal("3000"), "chemical_cost": Decimal("1500")})
    cob = Company(code="RECOB", name="Co B", state_code="29")
    db.add(cob); db.flush()
    # company B has no production → trivially reconciled, and sees none of A's jobs
    recb = prec.production_ledger_reconciliation(db, cob.id)
    assert recb["is_reconciled"]
    assert recb["grey_orders"] == [] and recb["dyeing_jobs"] == []
