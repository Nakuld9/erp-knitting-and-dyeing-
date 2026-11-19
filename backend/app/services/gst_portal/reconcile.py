"""Purchase-register vs GSTR-2A / GSTR-2B reconciliation (Stage 6F-P5 GST).

Matches the ITC register (books purchase documents) against the latest imported
portal documents for a period and classifies every row:

  EXACT / PARTIAL / INVOICE_FORMAT_DIFF / DATE_DIFF / TAXABLE_DIFF / RATE_DIFF /
  HEAD_DIFF / MISSING_IN_BOOKS / MISSING_IN_2A / MISSING_IN_2B / DUPLICATE /
  AMENDMENT / CREDIT_NOTE / DEBIT_NOTE.

It updates each invoice's ITC status (BOOKED_NOT_IN_2B, IN_2A_NOT_IN_2B,
IN_2B_MATCHED, MISMATCH_UNDER_REVIEW, ELIGIBLE_FOR_REVIEW) with an append-only
history entry, supporting carry-forward (a prior-period invoice that appears in a
later GSTR-2B is matched to its original books invoice and presented for review).
It never auto-claims ITC and never modifies the purchase invoice or its tax values;
differences are recorded for an authorised user to resolve with a remark.
"""
from __future__ import annotations

import json
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.gst_itc import (
    GstPortalDocument,
    GstReturnType,
    ItcInvoiceStatus,
    ItcReconciliationMatch,
    ItcReconciliationRun,
    ItcStatus,
    MatchResolution,
    MatchType,
    PortalDocType,
)
from app.services.audit import record_audit
from app.services.gst_portal import importer as imp
from app.services.gst_portal.common import (
    GstItcError,
    MZERO,
    TAX_HEADS,
    m,
    norm_gstin,
    norm_invoice_no,
    now,
    period_le,
    period_valid,
    record_status,
)

# Statuses reconcile may advance; settled/claimed states are never downgraded.
_OPEN = {ItcStatus.BOOKED_NOT_IN_2B.value, ItcStatus.IN_2A_NOT_IN_2B.value,
         ItcStatus.ITC_DEFERRED.value, ItcStatus.MISMATCH_UNDER_REVIEW.value,
         ItcStatus.ELIGIBLE_FOR_REVIEW.value, ItcStatus.IN_2B_MATCHED.value}


def _doc_total_key(doc: GstPortalDocument):
    return (norm_gstin(doc.supplier_gstin), norm_invoice_no(doc.doc_number))


def _inv_key(inv: ItcInvoiceStatus):
    return (norm_gstin(inv.supplier_gstin), norm_invoice_no(inv.supplier_invoice_no))


def _amount_diff(inv: ItcInvoiceStatus, doc: GstPortalDocument, tol: Decimal) -> dict:
    diff = {}
    pairs = {"taxable": (m(inv.taxable_value), m(doc.taxable_value))}
    for h in TAX_HEADS:
        pairs[h] = (m(getattr(inv, h)), m(getattr(doc, h)))
    for head, (b, p) in pairs.items():
        if abs(b - p) > tol:
            diff[head] = {"books": str(b), "portal": str(p), "diff": str(b - p)}
    return diff


def _classify_mismatch(diff: dict) -> str:
    if "taxable" in diff:
        return MatchType.TAXABLE_DIFF.value
    # a pure rate change usually shifts every tax head proportionally while the
    # taxable value is unchanged -> report as RATE_DIFF; otherwise a head-level diff.
    if diff:
        return MatchType.HEAD_DIFF.value
    return MatchType.EXACT.value


def _present_in_2a(db, company_id, period, key) -> bool:
    imp2a = imp.latest_import(db, company_id, GstReturnType.GSTR2A.value, period)
    if imp2a is None:
        return False
    docs = db.execute(select(GstPortalDocument).where(
        GstPortalDocument.import_id == imp2a.id)).scalars().all()
    return any(_doc_total_key(d) == key for d in docs)


def reconcile_period(db: Session, company_id: uuid.UUID, *, gstin: str | None,
                     return_type: str, return_period: str, actor: uuid.UUID | None = None,
                     tol: Decimal | str = "1") -> dict:
    if return_type not in (GstReturnType.GSTR2A.value, GstReturnType.GSTR2B.value):
        raise GstItcError("reconciliation is defined for GSTR2A or GSTR2B")
    if not period_valid(return_period):
        raise GstItcError("return_period must be MMYYYY")
    tol = m(tol)
    is_2b = return_type == GstReturnType.GSTR2B.value
    gstin = imp.resolve_company_gstin(db, company_id, gstin)

    import_row = imp.latest_import(db, company_id, return_type, return_period)
    if import_row is None:
        raise GstItcError(f"no {return_type} import found for period {return_period}; import it first")
    portal_docs = db.execute(select(GstPortalDocument).where(
        GstPortalDocument.import_id == import_row.id)).scalars().all()

    run = ItcReconciliationRun(
        company_id=company_id, gstin=gstin, return_type=return_type, return_period=return_period,
        import_id=import_row.id, run_by=actor, run_at=now(), created_by=actor, updated_by=actor)
    db.add(run)
    db.flush()

    # Index portal docs by (gstin, normalised number); keep lists for duplicate detection.
    portal_index: dict = {}
    for d in portal_docs:
        portal_index.setdefault(_doc_total_key(d), []).append(d)
    matched_doc_ids: set = set()

    # Candidate books rows: open invoices booked in this period or carried forward
    # from an earlier period (so a deferred invoice can match a later 2B).
    books_rows = db.execute(select(ItcInvoiceStatus).where(
        ItcInvoiceStatus.company_id == company_id).order_by(
        ItcInvoiceStatus.booked_period, ItcInvoiceStatus.created_at)).scalars().all()
    candidates = [r for r in books_rows
                  if r.status in _OPEN and period_le(r.booked_period, return_period)]

    counts: dict = {}
    seen_books_keys: set = set()

    def _match(inv, doc, match_type, diff=None, resolution=None):
        counts[match_type] = counts.get(match_type, 0) + 1
        db.add(ItcReconciliationMatch(
            company_id=company_id, run_id=run.id, return_period=return_period,
            invoice_status_id=(inv.id if inv else None),
            portal_document_id=(doc.id if doc else None),
            supplier_gstin=norm_gstin(inv.supplier_gstin if inv else (doc.supplier_gstin if doc else None)),
            doc_number=(inv.supplier_invoice_no if inv else (doc.doc_number if doc else None)),
            match_type=match_type, diff=(json.dumps(diff) if diff else None),
            resolution_status=(resolution or (MatchResolution.RESOLVED.value
                               if match_type == MatchType.EXACT.value
                               else MatchResolution.UNRESOLVED.value)),
            created_by=actor, updated_by=actor))

    for inv in candidates:
        key = _inv_key(inv)
        if key in seen_books_keys:
            _match(inv, None, MatchType.DUPLICATE.value, resolution=MatchResolution.UNRESOLVED.value)
            continue
        seen_books_keys.add(key)

        docs = [d for d in portal_index.get(key, []) if d.id not in matched_doc_ids]
        if docs:
            doc = docs[0]
            matched_doc_ids.add(doc.id)
            diff = _amount_diff(inv, doc, tol)
            fmt_diff = (norm_invoice_no(inv.supplier_invoice_no) == norm_invoice_no(doc.doc_number)
                        and (inv.supplier_invoice_no or "") != (doc.doc_number or ""))
            date_diff = (inv.invoice_date is not None and doc.doc_date is not None
                         and inv.invoice_date != doc.doc_date)
            inv.matched_document_id = doc.id
            if doc.is_amendment:
                inv.matched_period = return_period
                if inv.status in _OPEN:
                    record_status(db, inv, ItcStatus.MISMATCH_UNDER_REVIEW.value,
                                  period=return_period, reason="matched an amendment document",
                                  reference=import_row.file_hash, actor=actor)
                    inv.deferred_reason = "amendment in portal — review"
                _match(inv, doc, MatchType.AMENDMENT.value, diff or None)
            elif doc.doc_type in (PortalDocType.CN.value, PortalDocType.DN.value):
                mt = (MatchType.CREDIT_NOTE.value if doc.doc_type == PortalDocType.CN.value
                      else MatchType.DEBIT_NOTE.value)
                inv.matched_period = return_period
                _match(inv, doc, mt, diff or None)
            elif diff:
                inv.matched_period = return_period
                inv.deferred_reason = "mismatch vs portal — review"
                if inv.status in _OPEN and inv.status != ItcStatus.IN_2B_MATCHED.value:
                    record_status(db, inv, ItcStatus.MISMATCH_UNDER_REVIEW.value,
                                  period=return_period, reason="amount mismatch vs portal",
                                  reference=import_row.file_hash, actor=actor)
                _match(inv, doc, _classify_mismatch(diff), diff)
            else:
                inv.matched_period = return_period
                if is_2b:
                    if inv.booked_period == return_period:
                        target = ItcStatus.IN_2B_MATCHED.value
                    else:
                        target = ItcStatus.ELIGIBLE_FOR_REVIEW.value
                        inv.eligibility_note = (
                            f"appeared in GSTR-2B {return_period} (booked {inv.booked_period}); "
                            "re-evaluate eligibility and statutory time limit before claim")
                    if inv.status != target and inv.status in _OPEN:
                        record_status(db, inv, target, period=return_period,
                                      reason="matched in GSTR-2B", reference=import_row.file_hash,
                                      actor=actor)
                else:
                    if inv.status == ItcStatus.BOOKED_NOT_IN_2B.value:
                        record_status(db, inv, ItcStatus.IN_2A_NOT_IN_2B.value, period=return_period,
                                      reason="matched in GSTR-2A", actor=actor)
                mt = (MatchType.INVOICE_FORMAT_DIFF.value if fmt_diff else
                      MatchType.DATE_DIFF.value if date_diff else MatchType.EXACT.value)
                _match(inv, doc, mt, diff or None)
        else:
            # Not present in this period's portal statement.
            if is_2b:
                if inv.status in _OPEN and inv.status not in (
                        ItcStatus.IN_2A_NOT_IN_2B.value, ItcStatus.ITC_DEFERRED.value):
                    if _present_in_2a(db, company_id, return_period, key):
                        record_status(db, inv, ItcStatus.IN_2A_NOT_IN_2B.value, period=return_period,
                                      reason="in GSTR-2A but not GSTR-2B", actor=actor)
                        inv.deferred_reason = "in 2A not in 2B — ITC not claimable this period"
                _match(inv, None, MatchType.MISSING_IN_2B.value,
                       resolution=MatchResolution.UNRESOLVED.value)
            else:
                _match(inv, None, MatchType.MISSING_IN_2A.value,
                       resolution=MatchResolution.UNRESOLVED.value)

    # Portal documents with no books match -> missing in books.
    for d in portal_docs:
        if d.id in matched_doc_ids:
            continue
        _match(None, d, MatchType.MISSING_IN_BOOKS.value,
               resolution=MatchResolution.UNRESOLVED.value)

    for inv in candidates:
        inv.last_reco_run_id = run.id

    summary = {"return_type": return_type, "return_period": return_period,
               "portal_documents": len(portal_docs), "books_candidates": len(candidates),
               "counts": counts}
    run.summary = json.dumps(summary, default=str)
    db.flush()
    record_audit(db, action="GST_ITC_RECONCILED", entity="gst_itc_reco_run",
                 entity_id=str(run.id), actor_id=actor,
                 detail={"company_id": str(company_id), "gstin": gstin,
                         "return_type": return_type, "return_period": return_period,
                         "counts": counts})
    return {"run_id": str(run.id), **summary}


def list_matches(db: Session, company_id: uuid.UUID, run_id: uuid.UUID, *,
                 match_type: str | None = None, unresolved_only: bool = False):
    run = db.get(ItcReconciliationRun, run_id)
    if run is None or run.company_id != company_id:
        raise GstItcError("reconciliation run not found")
    stmt = select(ItcReconciliationMatch).where(ItcReconciliationMatch.run_id == run_id)
    if match_type:
        stmt = stmt.where(ItcReconciliationMatch.match_type == match_type)
    if unresolved_only:
        stmt = stmt.where(ItcReconciliationMatch.resolution_status == MatchResolution.UNRESOLVED.value)
    return run, db.execute(stmt.order_by(ItcReconciliationMatch.match_type)).scalars().all()


def resolve_match(db: Session, company_id: uuid.UUID, match_id: uuid.UUID, *, resolution: str,
                  remark: str, actor: uuid.UUID | None = None) -> ItcReconciliationMatch:
    """Authorised resolution of a reconciliation difference WITH a remark. Records
    the decision only — it never alters the purchase invoice or its tax values."""
    mt = db.get(ItcReconciliationMatch, match_id)
    if mt is None or mt.company_id != company_id:
        raise GstItcError("match not found")
    if resolution not in {r.value for r in MatchResolution}:
        raise GstItcError("invalid resolution")
    if not (remark or "").strip():
        raise GstItcError("a remark is required to resolve a difference")
    mt.resolution_status = resolution
    mt.resolution_remark = remark.strip()[:500]
    mt.resolved_by = actor
    mt.resolved_at = now()
    db.flush()
    record_audit(db, action="GST_ITC_MATCH_RESOLVED", entity="gst_itc_reco_match",
                 entity_id=str(mt.id), actor_id=actor,
                 detail={"company_id": str(company_id), "resolution": resolution,
                         "remark": mt.resolution_remark, "match_type": mt.match_type})
    return mt
