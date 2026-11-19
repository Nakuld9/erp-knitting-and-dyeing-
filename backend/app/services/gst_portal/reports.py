"""GST / ITC reports + Excel/PDF export (Stage 6F-P5 GST).

Read-only reports built from imports, reconciliation runs and the ITC register:
GSTR-2A/2B vs purchase register, month-wise ITC available/deferred/mismatched/
claimed/reversed/reclaimed, ITC eligible for subsequent-period review, GST
cross-adjustments, supplier-wise missing / mismatch and period-wise unresolved
differences. Each report is a ``{title, columns, rows}`` structure that the generic
``to_excel`` / ``to_pdf`` exporters render.
"""
from __future__ import annotations

import io
import json
import uuid

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.gst_itc import (
    GstCrossAdjustment,
    ItcInvoiceStatus,
    ItcReconciliationMatch,
    ItcReconciliationRun,
    ItcReversalReclaim,
    ItcStatus,
    MatchResolution,
    MatchType,
)
from app.services.gst_portal import itc as itc_svc
from app.services.gst_portal.common import GstItcError


def _latest_run(db, company_id, return_type, period):
    return db.execute(select(ItcReconciliationRun).where(
        ItcReconciliationRun.company_id == company_id,
        ItcReconciliationRun.return_type == return_type,
        ItcReconciliationRun.return_period == period)
        .order_by(ItcReconciliationRun.run_at.desc())).scalars().first()


def _match_rows(db, run):
    out = []
    for mtc in db.execute(select(ItcReconciliationMatch).where(
            ItcReconciliationMatch.run_id == run.id)
            .order_by(ItcReconciliationMatch.match_type)).scalars().all():
        inv = db.get(ItcInvoiceStatus, mtc.invoice_status_id) if mtc.invoice_status_id else None
        out.append({
            "supplier_gstin": mtc.supplier_gstin,
            "doc_number": mtc.doc_number,
            "match_type": mtc.match_type,
            "resolution": mtc.resolution_status,
            "books_status": (inv.status if inv else None),
            "diff": (mtc.diff or ""),
            "remark": (mtc.resolution_remark or ""),
        })
    return out


_REGISTER_COLUMNS = ["supplier_gstin", "supplier_invoice_no", "invoice_date", "booked_period",
                     "matched_period", "status", "igst", "cgst", "sgst", "cess", "deferred_reason"]


def _register_rows(db, company_id, statuses=None, period=None):
    stmt = select(ItcInvoiceStatus).where(ItcInvoiceStatus.company_id == company_id)
    if statuses:
        stmt = stmt.where(ItcInvoiceStatus.status.in_(statuses))
    rows = db.execute(stmt.order_by(ItcInvoiceStatus.booked_period)).scalars().all()
    if period:
        rows = [r for r in rows if period in (r.booked_period, r.matched_period, r.claim_period)]
    return [{
        "supplier_gstin": r.supplier_gstin, "supplier_invoice_no": r.supplier_invoice_no,
        "invoice_date": (r.invoice_date.isoformat() if r.invoice_date else None),
        "booked_period": r.booked_period, "matched_period": r.matched_period, "status": r.status,
        "igst": str(r.igst), "cgst": str(r.cgst), "sgst": str(r.sgst), "cess": str(r.cess),
        "deferred_reason": r.deferred_reason,
    } for r in rows]


# ------------------------------- reports ------------------------------------

def reco_vs_books(db: Session, company_id: uuid.UUID, *, return_type: str, period: str) -> dict:
    run = _latest_run(db, company_id, return_type, period)
    rows = _match_rows(db, run) if run else []
    return {"title": f"{return_type} vs Purchase Register ({period})",
            "columns": ["supplier_gstin", "doc_number", "match_type", "resolution",
                        "books_status", "diff", "remark"],
            "rows": rows, "run_id": (str(run.id) if run else None)}


def itc_available(db, company_id, period):
    s = itc_svc.claimable_summary(db, company_id, period)
    rows = [{"bucket": k, "count": s["counts"][k], **s["amounts"][k]} for k in s["counts"]]
    return {"title": f"Month-wise ITC ({period})",
            "columns": ["bucket", "count", "igst", "cgst", "sgst", "cess"], "rows": rows}


def itc_register_report(db, company_id, statuses, period, title):
    return {"title": title, "columns": _REGISTER_COLUMNS,
            "rows": _register_rows(db, company_id, statuses, period)}


def itc_deferred(db, company_id, period):
    return itc_register_report(db, company_id, [ItcStatus.ITC_DEFERRED.value,
                               ItcStatus.IN_2A_NOT_IN_2B.value, ItcStatus.BOOKED_NOT_IN_2B.value],
                               period, f"ITC Deferred / Not in 2B ({period})")


def itc_mismatched(db, company_id, period):
    return itc_register_report(db, company_id, [ItcStatus.MISMATCH_UNDER_REVIEW.value], period,
                               f"Mismatched ITC ({period})")


def itc_eligible_review(db, company_id, period):
    return itc_register_report(db, company_id, [ItcStatus.ELIGIBLE_FOR_REVIEW.value], period,
                               f"ITC Eligible for Subsequent-Period Review ({period})")


def itc_claimed(db, company_id, period):
    return itc_register_report(db, company_id, [ItcStatus.ITC_CLAIMED.value], period,
                               f"ITC Claimed ({period})")


def itc_reversed_reclaimed(db, company_id, period=None):
    stmt = select(ItcReversalReclaim).where(ItcReversalReclaim.company_id == company_id)
    if period:
        stmt = stmt.where(ItcReversalReclaim.tax_period == period)
    rows = []
    for r in db.execute(stmt.order_by(ItcReversalReclaim.recorded_at)).scalars().all():
        rows.append({"action": r.action, "tax_period": r.tax_period, "reference": r.reference,
                     "igst": str(r.igst), "cgst": str(r.cgst), "sgst": str(r.sgst),
                     "cess": str(r.cess), "is_partial": r.is_partial,
                     "posted": bool(r.voucher_id)})
    return {"title": f"ITC Reversed / Reclaimed ({period or 'all'})",
            "columns": ["action", "tax_period", "reference", "igst", "cgst", "sgst", "cess",
                        "is_partial", "posted"], "rows": rows}


def cross_adjustments_report(db, company_id, period=None):
    stmt = select(GstCrossAdjustment).where(GstCrossAdjustment.company_id == company_id)
    if period:
        stmt = stmt.where(GstCrossAdjustment.tax_period == period)
    rows = []
    for r in db.execute(stmt.order_by(GstCrossAdjustment.tax_period)).scalars().all():
        prop = json.loads(r.proposal) if r.proposal else {}
        rows.append({"tax_period": r.tax_period, "status": r.status,
                     "entries": json.dumps(prop.get("entries", [])),
                     "posted_voucher": (str(r.posted_voucher_id) if r.posted_voucher_id else None)})
    return {"title": f"GST Cross-Adjustments ({period or 'all'})",
            "columns": ["tax_period", "status", "entries", "posted_voucher"], "rows": rows}


def _supplier_matches(db, company_id, period, match_types):
    run2b = _latest_run(db, company_id, "GSTR2B", period)
    if run2b is None:
        return []
    agg: dict = {}
    for mtc in db.execute(select(ItcReconciliationMatch).where(
            ItcReconciliationMatch.run_id == run2b.id,
            ItcReconciliationMatch.match_type.in_(match_types))).scalars().all():
        g = mtc.supplier_gstin or "(unknown)"
        agg.setdefault(g, {"supplier_gstin": g, "count": 0})
        agg[g]["count"] += 1
    return list(agg.values())


def supplier_missing(db, company_id, period):
    return {"title": f"Supplier-wise Missing Invoices ({period})",
            "columns": ["supplier_gstin", "count"],
            "rows": _supplier_matches(db, company_id, period,
                                      [MatchType.MISSING_IN_2B.value, MatchType.MISSING_IN_BOOKS.value])}


def supplier_mismatch(db, company_id, period):
    return {"title": f"Supplier-wise Mismatch ({period})",
            "columns": ["supplier_gstin", "count"],
            "rows": _supplier_matches(db, company_id, period,
                                      [MatchType.TAXABLE_DIFF.value, MatchType.HEAD_DIFF.value,
                                       MatchType.RATE_DIFF.value])}


def period_unresolved(db, company_id, period):
    run2b = _latest_run(db, company_id, "GSTR2B", period)
    rows = []
    if run2b is not None:
        for mtc in db.execute(select(ItcReconciliationMatch).where(
                ItcReconciliationMatch.run_id == run2b.id,
                ItcReconciliationMatch.resolution_status == MatchResolution.UNRESOLVED.value)
                .order_by(ItcReconciliationMatch.match_type)).scalars().all():
            rows.append({"supplier_gstin": mtc.supplier_gstin, "doc_number": mtc.doc_number,
                         "match_type": mtc.match_type})
    return {"title": f"Period-wise Unresolved Differences ({period})",
            "columns": ["supplier_gstin", "doc_number", "match_type"], "rows": rows}


# ------------------------------- exporters ----------------------------------

def to_excel(report: dict) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = (report.get("title") or "Report")[:31]
    ws.append([report.get("title") or "Report"])
    cols = report["columns"]
    ws.append(cols)
    for row in report["rows"]:
        ws.append([row.get(c) for c in cols])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_pdf(report: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=10 * mm, rightMargin=10 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm)
    styles = getSampleStyleSheet()
    cols = report["columns"]
    data = [cols] + [[str(row.get(c) if row.get(c) is not None else "") for c in cols]
                     for row in report["rows"]]
    story = [Paragraph(report.get("title") or "Report", styles["Title"]), Spacer(1, 6)]
    if len(data) == 1:
        story.append(Paragraph("No rows.", styles["Normal"]))
    else:
        tbl = Table(data, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#30475e")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
        ]))
        story.append(tbl)
    doc.build(story)
    return buf.getvalue()


_REPORTS = {
    "2a_vs_books": lambda db, c, p: reco_vs_books(db, c, return_type="GSTR2A", period=p),
    "2b_vs_books": lambda db, c, p: reco_vs_books(db, c, return_type="GSTR2B", period=p),
    "itc_available": itc_available,
    "itc_deferred": itc_deferred,
    "itc_mismatched": itc_mismatched,
    "itc_eligible_review": itc_eligible_review,
    "itc_claimed": itc_claimed,
    "itc_reversed_reclaimed": itc_reversed_reclaimed,
    "cross_adjustments": cross_adjustments_report,
    "supplier_missing": supplier_missing,
    "supplier_mismatch": supplier_mismatch,
    "period_unresolved": period_unresolved,
}


def build_report(db: Session, company_id: uuid.UUID, report_key: str, period: str) -> dict:
    fn = _REPORTS.get(report_key)
    if fn is None:
        raise GstItcError(f"unknown report '{report_key}'; available: {sorted(_REPORTS)}")
    return fn(db, company_id, period)
