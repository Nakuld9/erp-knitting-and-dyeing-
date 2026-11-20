"""Opening reconciliation reports + exports (Stage 6F-P6F).

Seventeen read-only reports derived from the P6 reconciliation compute functions and
the persisted run/discrepancy/CA tables. Each returns a ``{title, columns, rows}``
structure rendered to JSON, Excel or PDF via the shared exporters. No figures are
invented; every row traces to opening lines, stock layers, GL balances or persisted
reconciliation records. Company/factory filters flow through from the caller.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.opening_custody import OpeningCustodyLine
from app.models.opening_financial import OpeningCaDecision
from app.models.opening_reconciliation import DiscrepancyStatus, OpeningDiscrepancy
from app.models.opening_roll import OpeningRollLine
from app.models.opening_stock import OpeningLineValidation
from app.services import opening_reconciliation as recon
# Reuse the GST module's generic, already-tested exporters.
from app.services.gst_portal.reports import to_excel, to_pdf  # noqa: F401

_V_OK = (OpeningLineValidation.VALID.value, OpeningLineValidation.COMMITTED.value)


def _rep(title, columns, rows):
    return {"title": title, "columns": columns, "rows": rows}


# 1. Company Opening Summary
def company_summary(db, cid, as_of, factory_id=None):
    d = recon.dashboard(db, cid, effective_date=as_of, factory_id=factory_id)
    rows = [
        {"metric": "Opening batches", "value": d["opening_batch_count"]},
        {"metric": "Opening GL debit total", "value": d["opening_gl_debit_total"]},
        {"metric": "Inventory total stock value",
         "value": d["inventory"].get("total_stock_value")},
        {"metric": "AR sub-ledger", "value": d["customer_ar"].get("subledger_total")},
        {"metric": "AR GL control", "value": d["customer_ar"].get("gl_control")},
        {"metric": "AP sub-ledger", "value": d["supplier_ap"].get("subledger_total")},
        {"metric": "AP GL control", "value": d["supplier_ap"].get("gl_control")},
        {"metric": "Bank+cash total", "value": d["bank_cash"].get("total")},
        {"metric": "Fixed asset NBV", "value": d["fixed_asset"].get("total_nbv")},
        {"metric": "Trial balance difference", "value": d["trial_balance"].get("difference")},
        {"metric": "Unresolved discrepancies",
         "value": d["controls"]["persisted_unresolved_discrepancies"]},
        {"metric": "Pending CA decisions", "value": d["controls"]["pending_ca_decisions"]},
        {"metric": "Cutover readiness", "value": d["cutover_readiness"]},
    ]
    return _rep(f"Company Opening Summary ({as_of or 'current'})", ["metric", "value"], rows)


# 2. Factory-wise Opening Summary
def factory_summary(db, cid, as_of, factory_id=None):
    inv, _ = recon.compute_inventory(db, cid, as_of=as_of, factory_id=factory_id)
    rows = [{"factory_id": k, "inventory_value": v}
            for k, v in inv.get("factory_breakdown", {}).items()]
    return _rep(f"Factory-wise Opening Summary ({as_of or 'current'})",
                ["factory_id", "inventory_value"], rows)


# 3. Inventory Opening Reconciliation
def inventory_recon(db, cid, as_of, factory_id=None):
    inv, _ = recon.compute_inventory(db, cid, as_of=as_of, factory_id=factory_id)
    return _rep(f"Inventory Opening Reconciliation ({as_of or 'current'})",
                ["label", "account", "stock_value", "gl_value", "difference", "mapped"],
                inv.get("control_accounts", []))


# 4. Yarn Opening Reconciliation
def yarn_recon(db, cid, as_of, factory_id=None):
    inv, _ = recon.compute_inventory(db, cid, as_of=as_of, factory_id=factory_id)
    rows = [r for r in inv.get("control_accounts", []) if r.get("account") == recon._YARN_ACC]
    cats = inv.get("warehouse_categories", {})
    rows.append({"label": "YARN_STORE category", "account": "", "stock_value":
                 cats.get("YARN_STORE", {}).get("value"), "gl_value": "",
                 "difference": "", "mapped": ""})
    return _rep(f"Yarn Opening Reconciliation ({as_of or 'current'})",
                ["label", "account", "stock_value", "gl_value", "difference", "mapped"], rows)


# 5. Manufactured-Fabric Roll Reconciliation (roll-level identity preserved)
def roll_recon(db, cid, as_of, factory_id=None):
    stmt = select(OpeningRollLine).where(
        OpeningRollLine.company_id == cid, OpeningRollLine.validation_status.in_(_V_OK))
    rolls = db.execute(stmt).scalars().all()
    rows = [{"erp_roll_no": r.erp_roll_no, "legacy_label": r.legacy_label,
             "roll_kind": r.roll_kind, "net_weight_kg": str(r.net_weight_kg),
             "unit_cost": str(r.unit_cost), "total_value": str(r.total_value),
             "factory_id": str(r.factory_id) if r.factory_id else None,
             "source_batch": str(r.batch_id)} for r in rolls
            if not factory_id or r.factory_id == factory_id]
    return _rep(f"Manufactured-Fabric Roll Reconciliation ({as_of or 'current'})",
                ["erp_roll_no", "legacy_label", "roll_kind", "net_weight_kg", "unit_cost",
                 "total_value", "factory_id", "source_batch"], rows)


# 6. Trading-Fabric Reconciliation (aggregate; no roll ids)
def trading_recon(db, cid, as_of, factory_id=None):
    inv, _ = recon.compute_inventory(db, cid, as_of=as_of, factory_id=factory_id)
    rows = [r for r in inv.get("control_accounts", []) if "Trading" in (r.get("label") or "")]
    cats = inv.get("warehouse_categories", {})
    rows.append({"label": "TRADING category (aggregate)", "account": "",
                 "stock_value": cats.get("TRADING", {}).get("value"),
                 "gl_value": "", "difference": "", "mapped": ""})
    return _rep(f"Trading-Fabric Reconciliation ({as_of or 'current'})",
                ["label", "account", "stock_value", "gl_value", "difference", "mapped"], rows)


# 7. Vendor-Custody Reconciliation (counted once)
def custody_recon(db, cid, as_of, factory_id=None):
    stmt = select(OpeningCustodyLine).where(
        OpeningCustodyLine.company_id == cid, OpeningCustodyLine.validation_status.in_(_V_OK))
    lines = db.execute(stmt).scalars().all()
    rows = [{"line_kind": l.line_kind, "custody_type": l.custody_type,
             "vendor": str(l.job_worker_id) if l.job_worker_id else None,
             "quantity": str(l.quantity), "net_weight_kg":
             str(l.net_weight_kg) if l.net_weight_kg is not None else None,
             "total_value": str(l.total_value), "source_batch": str(l.batch_id)}
            for l in lines if not factory_id or l.factory_id == factory_id]
    return _rep(f"Vendor-Custody Reconciliation ({as_of or 'current'})",
                ["line_kind", "custody_type", "vendor", "quantity", "net_weight_kg",
                 "total_value", "source_batch"], rows)


# 8. Customer AR Reconciliation
def ar_recon(db, cid, as_of, factory_id=None):
    s, _ = recon.compute_ar(db, cid, as_of=as_of)
    rows = s.get("invoice_detail", [])
    return _rep(f"Customer AR Reconciliation ({as_of or 'current'})",
                ["party_id", "kind", "invoice_no", "invoice_date", "outstanding", "ageing"], rows)


# 9. Supplier AP Reconciliation
def ap_recon(db, cid, as_of, factory_id=None):
    s, _ = recon.compute_ap(db, cid, as_of=as_of)
    rows = s.get("invoice_detail", [])
    return _rep(f"Supplier AP Reconciliation ({as_of or 'current'})",
                ["party_id", "kind", "invoice_no", "invoice_date", "outstanding", "ageing"], rows)


# 10. Bank & Cash Reconciliation
def bank_recon(db, cid, as_of, factory_id=None):
    s, _ = recon.compute_bank_cash(db, cid, as_of=as_of)
    return _rep(f"Bank & Cash Reconciliation ({as_of or 'current'})",
                ["account_code", "is_cash", "book_balance", "statement_balance",
                 "outstanding_cheques", "deposits_in_transit", "adjusted_statement",
                 "recon_difference", "gl_balance", "reconciled"], s.get("lines", []))


# 11. Fixed Asset Reconciliation
def fa_recon(db, cid, as_of, factory_id=None):
    s, _ = recon.compute_fixed_asset(db, cid, as_of=as_of)
    return _rep(f"Fixed Asset Reconciliation ({as_of or 'current'})",
                ["asset_code", "category", "cost", "accumulated_depreciation", "nbv",
                 "cost_account", "accum_account"], s.get("assets", []))


# 12. GST & Statutory Reconciliation
def gst_statutory_recon(db, cid, as_of, factory_id=None):
    s, _ = recon.compute_gst_statutory(db, cid, as_of=as_of)
    return _rep(f"GST & Statutory Reconciliation ({as_of or 'current'})",
                ["account", "opening_net", "gl_balance", "difference"],
                s.get("statutory_opening", []))


# 13. Trial Balance Reconciliation
def tb_recon(db, cid, as_of, factory_id=None):
    s, _ = recon.compute_trial_balance(db, cid, as_of=as_of)
    rows = [{"metric": "Total debit", "value": s["total_debit"]},
            {"metric": "Total credit", "value": s["total_credit"]},
            {"metric": "Difference", "value": s["difference"]},
            {"metric": "Balanced", "value": s["balanced"]}]
    return _rep(f"Trial Balance Reconciliation ({as_of or 'current'})", ["metric", "value"], rows)


# 14. Opening Exceptions Report (all live discrepancies across categories)
def exceptions_report(db, cid, as_of, factory_id=None):
    rows = []
    for t, fn in recon._COMPUTE.items():
        _, discs = fn(db, cid, as_of=as_of, factory_id=factory_id)
        for d in discs:
            rows.append({"category": d["recon_category"],
                         "account_or_item": d.get("account_code") or d.get("inventory_category"),
                         "expected": str(d["expected_value"]), "actual": str(d["actual_value"]),
                         "difference": str(d["difference"]), "explanation": d.get("explanation")})
    return _rep(f"Opening Exceptions Report ({as_of or 'current'})",
                ["category", "account_or_item", "expected", "actual", "difference", "explanation"],
                rows)


# 15. Unresolved Discrepancy Report (persisted)
def unresolved_report(db, cid, as_of, factory_id=None):
    stmt = select(OpeningDiscrepancy).where(
        OpeningDiscrepancy.company_id == cid,
        OpeningDiscrepancy.resolution_status.in_(
            (DiscrepancyStatus.UNRESOLVED.value, DiscrepancyStatus.UNDER_REVIEW.value,
             DiscrepancyStatus.REOPENED.value)))
    if factory_id:
        stmt = stmt.where(OpeningDiscrepancy.factory_id == factory_id)
    rows = [{"category": d.recon_category,
             "account_or_item": d.account_code or d.inventory_category,
             "difference": str(d.difference), "status": d.resolution_status,
             "explanation": d.explanation, "discrepancy_key": d.discrepancy_key}
            for d in db.execute(stmt).scalars().all()]
    return _rep(f"Unresolved Discrepancy Report ({as_of or 'current'})",
                ["category", "account_or_item", "difference", "status", "explanation",
                 "discrepancy_key"], rows)


# 16. CA Approval Status Report
def ca_status_report(db, cid, as_of, factory_id=None):
    rows = [{"decision_key": c.decision_key, "title": c.title, "status": c.status,
             "reviewer": c.reviewer_name, "is_required": c.is_required}
            for c in db.execute(select(OpeningCaDecision).where(
                OpeningCaDecision.company_id == cid)).scalars().all()]
    return _rep(f"CA Approval Status Report ({as_of or 'current'})",
                ["decision_key", "title", "status", "reviewer", "is_required"], rows)


# 17. Opening Cutover Readiness Report
def cutover_readiness_report(db, cid, as_of, factory_id=None):
    d = recon.dashboard(db, cid, effective_date=as_of, factory_id=factory_id)
    c = d["controls"]
    rows = [{"metric": "Cutover readiness", "value": d["cutover_readiness"]},
            {"metric": "Any incomplete", "value": c["any_incomplete"]},
            {"metric": "Live discrepancies", "value": c["live_discrepancies"]},
            {"metric": "Unresolved discrepancies", "value": c["persisted_unresolved_discrepancies"]},
            {"metric": "Pending CA decisions", "value": c["pending_ca_decisions"]},
            {"metric": "Note", "value": d["cutover_note"]}]
    return _rep(f"Opening Cutover Readiness Report ({as_of or 'current'})",
                ["metric", "value"], rows)


REPORTS = {
    "company_summary": company_summary,
    "factory_summary": factory_summary,
    "inventory": inventory_recon,
    "yarn": yarn_recon,
    "manufactured_roll": roll_recon,
    "trading_fabric": trading_recon,
    "vendor_custody": custody_recon,
    "ar": ar_recon,
    "ap": ap_recon,
    "bank_cash": bank_recon,
    "fixed_asset": fa_recon,
    "gst_statutory": gst_statutory_recon,
    "trial_balance": tb_recon,
    "exceptions": exceptions_report,
    "unresolved": unresolved_report,
    "ca_status": ca_status_report,
    "cutover_readiness": cutover_readiness_report,
}


def build(db: Session, company_id: uuid.UUID, report_key: str, *, effective_date: date | None = None,
          factory_id: uuid.UUID | None = None) -> dict:
    fn = REPORTS.get(report_key)
    if fn is None:
        raise recon.OpeningReconError(f"unknown report '{report_key}'; available: {sorted(REPORTS)}")
    return fn(db, company_id, effective_date, factory_id)
