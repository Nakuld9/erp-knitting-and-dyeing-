"""Stage 6F-P6 — opening reconciliation (service + API, SQLite).

Covers the consolidated, company/factory-aware opening reconciliation: inventory vs
GL by warehouse category + control account (vendor custody counted once, trading
fabric mapping reported-not-invented), AR/AP vs control with ageing/advances, bank
BRS + GL, fixed-asset cost/accumulated-depreciation vs GL, GST/statutory, trial
balance, the status lifecycle + discrepancy resolve/reopen/history, the consolidated
dashboard + cutover-readiness gating, the 17 reports with Excel/PDF, company
isolation, and — critically — that a reconciliation run creates NO stock/GL/roll/lot/
job-work records (read/compute only).
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.accounting import JournalLine, Voucher
from app.models.core import Company
from app.models.hybrid import WarehouseCategory
from app.models.inventory import InventoryTransaction, ItemType, StockLayer
from app.models.jobwork import JobWorkIssue
from app.models.manufacturing import FabricRoll
from app.models.masters import Customer, Supplier, Warehouse, YarnLot
from app.models.opening_financial import (
    OpeningApLine, OpeningArLine, OpeningBankLine, OpeningFixedAssetLine, OpeningStatutoryLine,
)
from app.models.opening_reconciliation import (
    DiscrepancyStatus, OpeningDiscrepancy, OpeningReconciliationRun, ReconStatus, ReconType,
)
from app.models.opening_stock import OpeningLineValidation
from app.services import opening as batch_svc
from app.services import opening_reconciliation as recon
from app.services import opening_recon_reports as reports

ED = date(2026, 11, 15)
V = OpeningLineValidation.VALID.value
import datetime as _dt


@pytest.fixture()
def co(db, company):
    company.gstin = company.gstin or "27AAAPL1234C1ZV"
    db.flush()
    return company


def _batch(db, cid):
    return batch_svc.create_batch(db, cid, batch_type="GL_BALANCES", effective_date=ED,
                                  created_by=uuid.uuid4())


def _wh(db, cid, code, category, branch_id=None):
    w = Warehouse(company_id=cid, code=code, name=code, category=category, branch_id=branch_id)
    db.add(w)
    db.flush()
    return w


def _layer(db, cid, item_type, value, wh_id=None, qty="10"):
    db.add(StockLayer(company_id=cid, item_type=item_type, item_id=uuid.uuid4(),
                      qty_in=Decimal(qty), qty_remaining=Decimal(qty), unit_cost=Decimal("1"),
                      value_remaining=Decimal(str(value)), warehouse_id=wh_id,
                      received_at=_dt.datetime.now(_dt.timezone.utc)))
    db.flush()


def _ar(db, cid, bid, cust_id, amt, kind="INVOICE", inv="A1", idate=date(2026, 8, 1)):
    db.add(OpeningArLine(company_id=cid, batch_id=bid, ar_kind=kind, customer_id=cust_id,
                         invoice_no=inv, invoice_date=idate, original_amount=Decimal(str(amt)),
                         outstanding_amount=Decimal(str(amt)), validation_status=V))
    db.flush()


# ------------------------------ inventory -----------------------------------

def test_inventory_custody_counted_once(db, co):
    ys = _wh(db, co.id, "YS", WarehouseCategory.YARN_STORE.value)
    vc = _wh(db, co.id, "VC", WarehouseCategory.VENDOR_CUSTODY.value)
    _layer(db, co.id, ItemType.YARN.value, 1000, ys.id)
    _layer(db, co.id, ItemType.YARN.value, 500, vc.id)   # yarn held at vendor custody
    summary, discs = recon.compute_inventory(db, co.id, as_of=ED)
    cats = summary["warehouse_categories"]
    assert cats["YARN_STORE"]["value"] == "1000.00"
    assert cats["VENDOR_CUSTODY"]["value"] == "500.00"
    # Yarn control ties to ALL yarn layers (1500) exactly once — not 1000+500 again.
    yarn_row = [r for r in summary["control_accounts"] if r["account"] == "1060"][0]
    assert yarn_row["stock_value"] == "1500.00"
    assert summary["total_stock_value"] == "1500.00"
    # No GL posted -> discrepancy (expected GL 0 vs sub-ledger 1500).
    assert any(d["account_code"] == "1060" for d in discs)


def test_trading_fabric_mapping_reported_not_invented(db, co):
    tr = _wh(db, co.id, "TR", WarehouseCategory.TRADING.value)
    _layer(db, co.id, ItemType.TRADING_FABRIC.value, 2000, tr.id)
    summary, discs = recon.compute_inventory(db, co.id, as_of=ED)
    assert summary["incomplete"] is True
    assert any(d["discrepancy_key"] == "INV|MAPPING|TRADING_FABRIC" for d in discs)
    # aggregate presentation preserved
    assert summary["warehouse_categories"]["TRADING"]["value"] == "2000.00"


# ------------------------------- AR / AP ------------------------------------

def test_ar_control_and_ageing(db, co):
    bid = _batch(db, co.id).id
    c1 = Customer(company_id=co.id, code="C1", name="C1")
    db.add(c1); db.flush()
    _ar(db, co.id, bid, c1.id, 3000, inv="A1", idate=date(2026, 8, 1))        # ~106 days
    _ar(db, co.id, bid, c1.id, 2000, kind="ADVANCE", inv="ADV", idate=date(2026, 11, 1))
    summary, discs = recon.compute_ar(db, co.id, as_of=ED)
    assert summary["subledger_total"] == "5000.00"
    assert summary["advances"] == "2000.00"
    assert summary["gl_control"] == "0.00"          # nothing posted yet
    assert any(d["account_code"] == "1040" for d in discs)
    assert "91-180" in summary["ageing"]


def test_ap_control(db, co):
    bid = _batch(db, co.id).id
    s1 = Supplier(company_id=co.id, code="S1", name="S1")
    db.add(s1); db.flush()
    db.add(OpeningApLine(company_id=co.id, batch_id=bid, ap_kind="INVOICE", supplier_id=s1.id,
                         invoice_no="B1", invoice_date=date(2026, 9, 1),
                         outstanding_amount=Decimal("4000"), validation_status=V))
    db.flush()
    summary, discs = recon.compute_ap(db, co.id, as_of=ED)
    assert summary["subledger_total"] == "4000.00"
    assert any(d["account_code"] == "2010" for d in discs)


# ------------------------------ bank / FA -----------------------------------

def test_bank_brs_reconciled(db, co):
    bid = _batch(db, co.id).id
    # book = statement + deposits - outstanding -> 1000 = 1200 + 0 - 200 (reconciled); no GL code
    db.add(OpeningBankLine(company_id=co.id, batch_id=bid, account_code=None, is_cash=False,
                           book_balance=Decimal("1000"), statement_balance=Decimal("1200"),
                           outstanding_cheques=Decimal("200"), deposits_in_transit=Decimal("0"),
                           validation_status=V))
    db.flush()
    summary, discs = recon.compute_bank_cash(db, co.id, as_of=ED)
    assert summary["lines"][0]["adjusted_statement"] == "1000.00"
    assert summary["lines"][0]["reconciled"] is True
    assert discs == []


def test_bank_brs_mismatch(db, co):
    bid = _batch(db, co.id).id
    db.add(OpeningBankLine(company_id=co.id, batch_id=bid, account_code=None, is_cash=False,
                           book_balance=Decimal("1000"), statement_balance=Decimal("1500"),
                           outstanding_cheques=Decimal("100"), deposits_in_transit=Decimal("0"),
                           validation_status=V))
    db.flush()
    _, discs = recon.compute_bank_cash(db, co.id, as_of=ED)
    assert len(discs) == 1 and discs[0]["recon_category"] == ReconType.BANK_CASH.value


def test_fixed_asset_recon(db, co):
    bid = _batch(db, co.id).id
    db.add(OpeningFixedAssetLine(company_id=co.id, batch_id=bid, asset_code="FA1",
                                 asset_account_code="1530", accum_dep_account_code="1590",
                                 original_cost=Decimal("100000"),
                                 accumulated_depreciation=Decimal("40000"),
                                 net_book_value=Decimal("60000"), validation_status=V))
    db.flush()
    summary, discs = recon.compute_fixed_asset(db, co.id, as_of=ED)
    assert summary["total_cost"] == "100000.00" and summary["total_nbv"] == "60000.00"
    # GL 1530 and 1590 are 0 -> two discrepancies
    assert {d["discrepancy_key"] for d in discs} == {"FA|COST|1530", "FA|ACCUM|1590"}


def test_gst_statutory_recon(db, co):
    bid = _batch(db, co.id).id
    db.add(OpeningStatutoryLine(company_id=co.id, batch_id=bid, statutory_type="TDS",
                                account_code="2050", debit=Decimal("0"), credit=Decimal("5000"),
                                validation_status=V))
    db.flush()
    summary, discs = recon.compute_gst_statutory(db, co.id, as_of=ED)
    assert any(r["account"] == "2050" for r in summary["statutory_opening"])
    assert any(d["account_code"] == "2050" for d in discs)


# --------------------------- run + no-posting -------------------------------

def _counts(db, cid):
    def c(model, col="company_id"):
        return db.execute(select(func.count()).select_from(model).where(
            getattr(model, col) == cid)).scalar()
    return {
        "stock": c(StockLayer), "inv_txn": c(InventoryTransaction), "roll": c(FabricRoll),
        "yarnlot": c(YarnLot), "jobwork": c(JobWorkIssue), "voucher": c(Voucher),
        "journal": db.execute(select(func.count()).select_from(JournalLine).join(
            Voucher, Voucher.id == JournalLine.voucher_id).where(Voucher.company_id == cid)).scalar(),
    }


def test_run_creates_no_postings(db, co):
    ys = _wh(db, co.id, "YS", WarehouseCategory.YARN_STORE.value)
    _layer(db, co.id, ItemType.YARN.value, 1000, ys.id)
    before = _counts(db, co.id)
    run = recon.run_reconciliation(db, co.id, recon_type=ReconType.CONSOLIDATED.value,
                                   effective_date=ED, actor=uuid.uuid4())
    after = _counts(db, co.id)
    assert before == after            # nothing posted / created outside recon tables
    assert run.recon_type == ReconType.CONSOLIDATED.value
    assert run.discrepancy_count >= 1
    # Only reconciliation rows exist.
    assert db.execute(select(func.count()).select_from(OpeningReconciliationRun).where(
        OpeningReconciliationRun.company_id == co.id)).scalar() == 1


def test_rerun_increments_run_number_preserves_history(db, co):
    ys = _wh(db, co.id, "YS", WarehouseCategory.YARN_STORE.value)
    _layer(db, co.id, ItemType.YARN.value, 1000, ys.id)
    r1 = recon.run_reconciliation(db, co.id, recon_type=ReconType.INVENTORY.value, effective_date=ED)
    r2 = recon.run_reconciliation(db, co.id, recon_type=ReconType.INVENTORY.value, effective_date=ED)
    assert r2.run_number == r1.run_number + 1
    # both runs' discrepancies preserved (history), none deleted
    total = db.execute(select(func.count()).select_from(OpeningDiscrepancy).where(
        OpeningDiscrepancy.company_id == co.id)).scalar()
    assert total >= 2
    hist = recon.discrepancy_history(db, co.id, "INV|1060")
    assert len(hist) == 2


def test_duplicate_discrepancy_deduped_within_run(db, co):
    ys = _wh(db, co.id, "YS", WarehouseCategory.YARN_STORE.value)
    _layer(db, co.id, ItemType.YARN.value, 1000, ys.id)
    run = recon.run_reconciliation(db, co.id, recon_type=ReconType.INVENTORY.value, effective_date=ED)
    keys = [d.discrepancy_key for d in recon.list_discrepancies(db, co.id, run_id=run.id)]
    assert len(keys) == len(set(keys))   # no duplicate key within a run


# --------------------------- status lifecycle -------------------------------

def test_status_transitions_and_cutover_gating(db, co):
    ys = _wh(db, co.id, "YS", WarehouseCategory.YARN_STORE.value)
    _layer(db, co.id, ItemType.YARN.value, 1000, ys.id)
    run = recon.run_reconciliation(db, co.id, recon_type=ReconType.INVENTORY.value, effective_date=ED)
    assert run.status == ReconStatus.DISCREPANCY_FOUND.value
    # invalid transition
    with pytest.raises(recon.OpeningReconStateError):
        recon.set_run_status(db, co.id, run.id, to_status=ReconStatus.READY_FOR_CUTOVER.value)
    recon.set_run_status(db, co.id, run.id, to_status=ReconStatus.UNDER_REVIEW.value)
    recon.set_run_status(db, co.id, run.id, to_status=ReconStatus.RESOLVED.value)
    # still blocked: an unresolved discrepancy exists
    with pytest.raises(recon.OpeningReconStateError):
        recon.set_run_status(db, co.id, run.id, to_status=ReconStatus.READY_FOR_CUTOVER.value)
    # resolve the discrepancy, then readiness allowed
    for d in recon.list_discrepancies(db, co.id, run_id=run.id):
        recon.resolve_discrepancy(db, co.id, d.id, resolution_notes="verified with CA", actor=uuid.uuid4())
    recon.set_run_status(db, co.id, run.id, to_status=ReconStatus.READY_FOR_CUTOVER.value)
    assert recon.get_run(db, co.id, run.id).status == ReconStatus.READY_FOR_CUTOVER.value


def test_resolve_requires_note_and_reopen(db, co):
    ys = _wh(db, co.id, "YS", WarehouseCategory.YARN_STORE.value)
    _layer(db, co.id, ItemType.YARN.value, 1000, ys.id)
    run = recon.run_reconciliation(db, co.id, recon_type=ReconType.INVENTORY.value, effective_date=ED)
    d = recon.list_discrepancies(db, co.id, run_id=run.id)[0]
    with pytest.raises(recon.OpeningReconError):
        recon.resolve_discrepancy(db, co.id, d.id, resolution_notes="  ")
    recon.resolve_discrepancy(db, co.id, d.id, resolution_notes="ok", actor=uuid.uuid4())
    db.refresh(d)
    assert d.resolution_status == DiscrepancyStatus.RESOLVED.value
    with pytest.raises(recon.OpeningReconError):
        recon.reopen_discrepancy(db, co.id, d.id, reason="")
    recon.reopen_discrepancy(db, co.id, d.id, reason="new evidence", actor=uuid.uuid4())
    db.refresh(d)
    assert d.resolution_status == DiscrepancyStatus.REOPENED.value


# ------------------------------- dashboard ----------------------------------

def test_dashboard_and_readiness(db, co):
    tr = _wh(db, co.id, "TR", WarehouseCategory.TRADING.value)
    _layer(db, co.id, ItemType.TRADING_FABRIC.value, 2000, tr.id)
    d = recon.dashboard(db, co.id, effective_date=ED)
    assert d["controls"]["any_incomplete"] is True       # trading mapping missing
    assert d["cutover_readiness"] != "READY_FOR_CUTOVER"
    assert "inventory" in d and "trial_balance" in d


# ------------------------------- reports ------------------------------------

def test_all_17_reports_build_and_export(db, co):
    ys = _wh(db, co.id, "YS", WarehouseCategory.YARN_STORE.value)
    _layer(db, co.id, ItemType.YARN.value, 1000, ys.id)
    recon.run_reconciliation(db, co.id, recon_type=ReconType.CONSOLIDATED.value, effective_date=ED)
    assert len(reports.REPORTS) == 17
    for key in reports.REPORTS:
        rep = reports.build(db, co.id, key, effective_date=ED)
        assert "columns" in rep and "rows" in rep
    # Excel + PDF for one representative report
    rep = reports.build(db, co.id, "inventory", effective_date=ED)
    assert reports.to_excel(rep)[:2] == b"PK"
    assert reports.to_pdf(rep)[:4] == b"%PDF"


# ------------------------------ isolation -----------------------------------

def test_company_isolation(db, co):
    other = Company(code="OTHR", name="Other")
    db.add(other); db.flush()
    ys = _wh(db, co.id, "YS", WarehouseCategory.YARN_STORE.value)
    _layer(db, co.id, ItemType.YARN.value, 1000, ys.id)
    recon.run_reconciliation(db, co.id, recon_type=ReconType.INVENTORY.value, effective_date=ED)
    # other company sees no runs/discrepancies and its own inventory is empty
    assert recon.list_runs(db, other.id) == []
    assert recon.list_discrepancies(db, other.id) == []
    inv, _ = recon.compute_inventory(db, other.id, as_of=ED)
    assert inv["total_stock_value"] == "0.00"


# --------------------------------- API --------------------------------------

def test_api_run_dashboard_report(client, db, company, auth_headers):
    company.gstin = company.gstin or "27AAAPL1234C1ZV"
    ys = Warehouse(company_id=company.id, code="YS", name="YS",
                   category=WarehouseCategory.YARN_STORE.value)
    db.add(ys); db.flush()
    db.add(StockLayer(company_id=company.id, item_type=ItemType.YARN.value, item_id=uuid.uuid4(),
                      qty_in=Decimal("10"), qty_remaining=Decimal("10"), unit_cost=Decimal("1"),
                      value_remaining=Decimal("1000"), warehouse_id=ys.id,
                      received_at=_dt.datetime.now(_dt.timezone.utc)))
    db.flush()
    r = client.post("/api/opening/reconciliation/runs", headers=auth_headers,
                    json={"recon_type": "INVENTORY", "effective_date": ED.isoformat()})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "DISCREPANCY_FOUND"
    assert client.get("/api/opening/reconciliation/dashboard",
                      params={"effective_date": ED.isoformat()}, headers=auth_headers).status_code == 200
    rr = client.get("/api/opening/reconciliation/reports/inventory",
                    params={"effective_date": ED.isoformat(), "format": "excel"}, headers=auth_headers)
    assert rr.status_code == 200 and rr.content[:2] == b"PK"
    disc = client.get("/api/opening/reconciliation/discrepancies", headers=auth_headers)
    assert disc.status_code == 200 and disc.json()["items"]
