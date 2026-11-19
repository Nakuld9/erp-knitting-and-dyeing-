"""GST portal / ITC reconciliation & adjustment API (Stage 6F-P5 GST).

Endpoints for: portal connector status + templates; GSTR-2A/2B & electronic-ledger
import (download via authorised connector, or manual JSON/Excel upload) with history
and original-file retrieval; purchase-register reconciliation + difference resolution;
the ITC tracking register and lifecycle (defer / claim / ineligible / reversal /
reclaim); versioned GST policy + CA-gated credit cross-utilisation; electronic-ledger
reconciliation; and the report set with Excel/PDF export.

Permissions: ``gst.read`` for reads, ``gst.itc_manage`` for import/reconcile/tag/
resolve, ``gst.itc_post`` for CA-gated posting (policy approval, ITC reversal/reclaim
GL posting, cross-utilisation posting). Company is taken from the session, so GST data
never crosses legal companies. Statutory posting stays disabled unless an explicit
``authorize=True`` is supplied.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.deps import get_company_db, require_permission
from app.models.core import User
from app.models.gst_itc import GstImportSource
from app.services.gst_portal import books as books_svc
from app.services.gst_portal import connector as conn
from app.services.gst_portal import importer as imp
from app.services.gst_portal import itc as itc_svc
from app.services.gst_portal import ledger_recon as ledger_svc
from app.services.gst_portal import reconcile as reco_svc
from app.services.gst_portal import reports as reports_svc
from app.services.gst_portal import utilisation as util_svc
from app.services.gst_portal.common import GstItcError, GstItcStateError

router = APIRouter(prefix="/api/gst", tags=["gst-itc"])

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ------------------------------- helpers ------------------------------------

def _actor_name(user: User):
    return getattr(user, "full_name", None) or getattr(user, "name", None)


def _raise(exc: Exception):
    msg = str(exc)
    if isinstance(exc, imp.DuplicateImport):
        raise HTTPException(status.HTTP_409_CONFLICT, msg)
    if isinstance(exc, conn.ConnectorUnavailable):
        raise HTTPException(status.HTTP_409_CONFLICT, msg)
    if isinstance(exc, (GstItcStateError, util_svc.UtilisationPostingBlocked,
                        util_svc.ProhibitedUtilisation)):
        raise HTTPException(status.HTTP_409_CONFLICT, msg)
    if msg.strip().lower().endswith("not found"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, msg)
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, msg)


def _row(obj) -> dict:
    out = {}
    for col in obj.__table__.columns:
        if col.name == "raw_content":
            continue  # never dump the retained original bytes in a JSON row
        v = getattr(obj, col.name)
        if v is None:
            out[col.name] = None
        elif isinstance(v, uuid.UUID):
            out[col.name] = str(v)
        elif isinstance(v, (date, datetime)):
            out[col.name] = v.isoformat()
        else:
            out[col.name] = str(v) if type(v).__name__ == "Decimal" else v
    return out


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------ connector -----------------------------------

@router.get("/connector/status")
def connector_status(user: User = Depends(require_permission("gst.read"))):
    return conn.connector_status()


@router.get("/templates/gst-import")
def gst_template(return_type: str = "GSTR2B",
                 user: User = Depends(require_permission("gst.read"))):
    content = imp.build_gst_doc_template(return_type)
    return Response(content=content, media_type=_XLSX, headers={
        "Content-Disposition": f'attachment; filename="gst_import_{return_type}.xlsx"'})


# -------------------------------- imports -----------------------------------

class DownloadIn(_Strict):
    financial_year: str
    return_period: str
    return_type: str
    gstin: str | None = None


@router.post("/imports/download", status_code=status.HTTP_201_CREATED)
def download_import(payload: DownloadIn, db: Session = Depends(get_company_db),
                    user: User = Depends(require_permission("gst.itc_manage"))):
    try:
        res = imp.download_and_import(
            db, user.company_id, gstin=payload.gstin, financial_year=payload.financial_year,
            return_period=payload.return_period, return_type=payload.return_type, actor=user.id)
        db.commit()
    except (imp.GstPortalError, conn.ConnectorUnavailable) as exc:
        db.rollback()
        _raise(exc)
    return res


@router.post("/imports/upload", status_code=status.HTTP_201_CREATED)
def upload_import(financial_year: str = Query(...), return_period: str = Query(...),
                  return_type: str = Query(...), source: str = Query("MANUAL_JSON"),
                  gstin: str | None = Query(None), file: UploadFile = File(...),
                  db: Session = Depends(get_company_db),
                  user: User = Depends(require_permission("gst.itc_manage"))):
    content = file.file.read()
    if source not in {s.value for s in GstImportSource}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"invalid source '{source}'")
    try:
        res = imp.import_return(
            db, user.company_id, gstin=gstin, financial_year=financial_year,
            return_period=return_period, return_type=return_type, content=content,
            file_name=file.filename or "upload", source=source, actor=user.id)
        db.commit()
    except imp.GstPortalError as exc:
        db.rollback()
        _raise(exc)
    return res


@router.get("/imports")
def list_imports(return_type: str | None = None, return_period: str | None = None,
                 limit: int = 100, offset: int = 0, db: Session = Depends(get_company_db),
                 user: User = Depends(require_permission("gst.read"))):
    rows = imp.list_imports(db, user.company_id, return_type=return_type,
                            return_period=return_period, limit=limit, offset=offset)
    return {"items": [_row(r) for r in rows]}


@router.get("/imports/{import_id}")
def get_import(import_id: uuid.UUID, db: Session = Depends(get_company_db),
               user: User = Depends(require_permission("gst.read"))):
    try:
        return _row(imp.get_import(db, user.company_id, import_id))
    except imp.GstPortalError as exc:
        _raise(exc)


@router.get("/imports/{import_id}/file")
def get_import_file(import_id: uuid.UUID, db: Session = Depends(get_company_db),
                    user: User = Depends(require_permission("gst.read"))):
    try:
        row = imp.get_import(db, user.company_id, import_id)
    except imp.GstPortalError as exc:
        _raise(exc)
    media = _XLSX if row.source == GstImportSource.MANUAL_EXCEL.value else "application/json"
    return Response(content=row.raw_content, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{row.file_name}"'})


@router.get("/imports/{import_id}/documents")
def import_documents(import_id: uuid.UUID, db: Session = Depends(get_company_db),
                     user: User = Depends(require_permission("gst.read"))):
    from sqlalchemy import select

    from app.models.gst_itc import GstElectronicLedgerBalance, GstPortalDocument
    try:
        row = imp.get_import(db, user.company_id, import_id)
    except imp.GstPortalError as exc:
        _raise(exc)
    docs = db.execute(select(GstPortalDocument).where(
        GstPortalDocument.import_id == row.id)).scalars().all()
    ledg = db.execute(select(GstElectronicLedgerBalance).where(
        GstElectronicLedgerBalance.import_id == row.id)).scalars().all()
    return {"import_id": str(row.id), "documents": [_row(d) for d in docs],
            "ledger_balances": [_row(b) for b in ledg]}


# ------------------------------ books capture -------------------------------

class CaptureIn(_Strict):
    supplier_gstin: str
    supplier_invoice_no: str
    invoice_date: date | None = None
    booked_period: str
    taxable_value: float | str = 0
    igst: float | str = 0
    cgst: float | str = 0
    sgst: float | str = 0
    cess: float | str = 0
    doc_type: str = "INV"
    gstin: str | None = None


@router.post("/itc/capture", status_code=status.HTTP_201_CREATED)
def capture_invoice(payload: CaptureIn, db: Session = Depends(get_company_db),
                    user: User = Depends(require_permission("gst.itc_manage"))):
    try:
        gstin = imp.resolve_company_gstin(db, user.company_id, payload.gstin)
        inv = books_svc.capture_purchase(
            db, user.company_id, gstin=gstin, supplier_gstin=payload.supplier_gstin,
            supplier_invoice_no=payload.supplier_invoice_no, invoice_date=payload.invoice_date,
            booked_period=payload.booked_period, taxable_value=payload.taxable_value,
            igst=payload.igst, cgst=payload.cgst, sgst=payload.sgst, cess=payload.cess,
            doc_type=payload.doc_type, actor=user.id)
        db.commit()
    except (GstItcError, imp.GstPortalError) as exc:
        db.rollback()
        _raise(exc)
    return _row(inv)


class CaptureRegisterIn(_Strict):
    period: str
    gstin: str | None = None


@router.post("/itc/capture-from-register")
def capture_from_register(payload: CaptureRegisterIn, db: Session = Depends(get_company_db),
                          user: User = Depends(require_permission("gst.itc_manage"))):
    try:
        gstin = imp.resolve_company_gstin(db, user.company_id, payload.gstin)
        res = books_svc.capture_from_purchase_register(
            db, user.company_id, gstin=gstin, period=payload.period, actor=user.id)
        db.commit()
    except (GstItcError, imp.GstPortalError) as exc:
        db.rollback()
        _raise(exc)
    return res


# ------------------------------ reconciliation ------------------------------

class ReconcileIn(_Strict):
    return_type: str
    return_period: str
    gstin: str | None = None
    tol: str | None = None


@router.post("/reconcile")
def reconcile(payload: ReconcileIn, db: Session = Depends(get_company_db),
              user: User = Depends(require_permission("gst.itc_manage"))):
    try:
        res = reco_svc.reconcile_period(
            db, user.company_id, gstin=payload.gstin, return_type=payload.return_type,
            return_period=payload.return_period, actor=user.id, tol=payload.tol or "1")
        db.commit()
    except GstItcError as exc:
        db.rollback()
        _raise(exc)
    return res


@router.get("/reconcile/runs/{run_id}/matches")
def list_matches(run_id: uuid.UUID, match_type: str | None = None, unresolved_only: bool = False,
                 db: Session = Depends(get_company_db),
                 user: User = Depends(require_permission("gst.read"))):
    try:
        run, rows = reco_svc.list_matches(db, user.company_id, run_id, match_type=match_type,
                                          unresolved_only=unresolved_only)
    except GstItcError as exc:
        _raise(exc)
    return {"run_id": str(run.id), "items": [_row(r) for r in rows]}


class ResolveIn(_Strict):
    resolution: str
    remark: str


@router.post("/reconcile/matches/{match_id}/resolve")
def resolve_match(match_id: uuid.UUID, payload: ResolveIn, db: Session = Depends(get_company_db),
                  user: User = Depends(require_permission("gst.itc_manage"))):
    try:
        mt = reco_svc.resolve_match(db, user.company_id, match_id, resolution=payload.resolution,
                                    remark=payload.remark, actor=user.id)
        db.commit()
    except GstItcError as exc:
        db.rollback()
        _raise(exc)
    return _row(mt)


# ------------------------------- ITC register -------------------------------

@router.get("/itc/register")
def itc_register(status_filter: str | None = None, period: str | None = None,
                 limit: int = 200, offset: int = 0, db: Session = Depends(get_company_db),
                 user: User = Depends(require_permission("gst.read"))):
    rows, total = itc_svc.register(db, user.company_id, status=status_filter, period=period,
                                   limit=limit, offset=offset)
    return {"total": total, "items": [_row(r) for r in rows]}


@router.get("/itc/claimable")
def itc_claimable(period: str, db: Session = Depends(get_company_db),
                  user: User = Depends(require_permission("gst.read"))):
    return itc_svc.claimable_summary(db, user.company_id, period)


@router.get("/itc/invoices/{invoice_id}/history")
def itc_history(invoice_id: uuid.UUID, db: Session = Depends(get_company_db),
                user: User = Depends(require_permission("gst.read"))):
    try:
        inv, rows = itc_svc.history(db, user.company_id, invoice_id)
    except GstItcError as exc:
        _raise(exc)
    return {"invoice_id": str(inv.id), "status": inv.status, "history": [_row(r) for r in rows]}


@router.get("/itc/invoices/{invoice_id}/reversals")
def itc_reversals(invoice_id: uuid.UUID, db: Session = Depends(get_company_db),
                  user: User = Depends(require_permission("gst.read"))):
    try:
        inv, rows, net = itc_svc.reversal_history(db, user.company_id, invoice_id)
    except GstItcError as exc:
        _raise(exc)
    return {"invoice_id": str(inv.id), "net_reversed": {k: str(v) for k, v in net.items()},
            "entries": [_row(r) for r in rows]}


class ReasonIn(_Strict):
    reason: str
    period: str | None = None


@router.post("/itc/invoices/{invoice_id}/defer")
def itc_defer(invoice_id: uuid.UUID, payload: ReasonIn, db: Session = Depends(get_company_db),
              user: User = Depends(require_permission("gst.itc_manage"))):
    try:
        inv = itc_svc.defer_itc(db, user.company_id, invoice_id, reason=payload.reason,
                                period=payload.period, actor=user.id)
        db.commit()
    except GstItcError as exc:
        db.rollback()
        _raise(exc)
    return _row(inv)


@router.post("/itc/invoices/{invoice_id}/ineligible")
def itc_ineligible(invoice_id: uuid.UUID, payload: ReasonIn, db: Session = Depends(get_company_db),
                   user: User = Depends(require_permission("gst.itc_manage"))):
    try:
        inv = itc_svc.mark_ineligible(db, user.company_id, invoice_id, reason=payload.reason,
                                      actor=user.id)
        db.commit()
    except GstItcError as exc:
        db.rollback()
        _raise(exc)
    return _row(inv)


class ClaimIn(_Strict):
    period: str


@router.post("/itc/invoices/{invoice_id}/claim")
def itc_claim(invoice_id: uuid.UUID, payload: ClaimIn, db: Session = Depends(get_company_db),
              user: User = Depends(require_permission("gst.itc_manage"))):
    try:
        inv = itc_svc.claim_itc(db, user.company_id, invoice_id, period=payload.period,
                                actor=user.id)
        db.commit()
    except GstItcError as exc:
        db.rollback()
        _raise(exc)
    return _row(inv)


class ReverseIn(_Strict):
    tax_period: str
    reference: str
    igst: float | str = 0
    cgst: float | str = 0
    sgst: float | str = 0
    cess: float | str = 0
    reason: str | None = None
    is_partial: bool = False
    post: bool = False
    authorize: bool = False


@router.post("/itc/invoices/{invoice_id}/reverse")
def itc_reverse(invoice_id: uuid.UUID, payload: ReverseIn, db: Session = Depends(get_company_db),
                user: User = Depends(require_permission("gst.itc_post"))):
    try:
        rr = itc_svc.reverse_itc(
            db, user.company_id, invoice_id, tax_period=payload.tax_period,
            reference=payload.reference, igst=payload.igst, cgst=payload.cgst, sgst=payload.sgst,
            cess=payload.cess, reason=payload.reason, is_partial=payload.is_partial,
            post=payload.post, authorize=payload.authorize, actor=user.id)
        db.commit()
    except GstItcError as exc:
        db.rollback()
        _raise(exc)
    return _row(rr)


class ReclaimIn(_Strict):
    tax_period: str
    reference: str
    igst: float | str = 0
    cgst: float | str = 0
    sgst: float | str = 0
    cess: float | str = 0
    reason: str | None = None
    linked_reversal_id: uuid.UUID | None = None
    post: bool = False
    authorize: bool = False


@router.post("/itc/invoices/{invoice_id}/reclaim")
def itc_reclaim(invoice_id: uuid.UUID, payload: ReclaimIn, db: Session = Depends(get_company_db),
                user: User = Depends(require_permission("gst.itc_post"))):
    try:
        rr = itc_svc.reclaim_itc(
            db, user.company_id, invoice_id, tax_period=payload.tax_period,
            reference=payload.reference, igst=payload.igst, cgst=payload.cgst, sgst=payload.sgst,
            cess=payload.cess, reason=payload.reason, linked_reversal_id=payload.linked_reversal_id,
            post=payload.post, authorize=payload.authorize, actor=user.id)
        db.commit()
    except GstItcError as exc:
        db.rollback()
        _raise(exc)
    return _row(rr)


# ------------------------------- policy -------------------------------------

class PolicyIn(_Strict):
    rules: dict | None = None
    title: str | None = None
    effective_from: date | None = None


@router.post("/policy", status_code=status.HTTP_201_CREATED)
def create_policy(payload: PolicyIn, db: Session = Depends(get_company_db),
                  user: User = Depends(require_permission("gst.itc_manage"))):
    try:
        pol = util_svc.create_policy(db, user.company_id, rules=payload.rules, title=payload.title,
                                     effective_from=payload.effective_from, actor=user.id)
        db.commit()
    except util_svc.GstUtilisationError as exc:
        db.rollback()
        _raise(exc)
    return _row(pol)


class ApproverIn(_Strict):
    approver_name: str


@router.post("/policy/{policy_id}/approve")
def approve_policy(policy_id: uuid.UUID, payload: ApproverIn, db: Session = Depends(get_company_db),
                   user: User = Depends(require_permission("gst.itc_post"))):
    try:
        pol = util_svc.approve_policy(db, user.company_id, policy_id,
                                      approver_name=payload.approver_name, actor=user.id)
        db.commit()
    except util_svc.GstUtilisationError as exc:
        db.rollback()
        _raise(exc)
    return _row(pol)


@router.get("/policy/active")
def active_policy(db: Session = Depends(get_company_db),
                  user: User = Depends(require_permission("gst.read"))):
    pol = util_svc.active_policy(db, user.company_id)
    return _row(pol) if pol is not None else {"active": None}


# ---------------------------- cross-utilisation -----------------------------

class ProposeIn(_Strict):
    tax_period: str
    available: dict
    liability: dict


@router.post("/utilisation/propose")
def propose_utilisation(payload: ProposeIn, db: Session = Depends(get_company_db),
                        user: User = Depends(require_permission("gst.itc_manage"))):
    try:
        res = util_svc.propose_cross_adjustment(
            db, user.company_id, tax_period=payload.tax_period, available=payload.available,
            liability=payload.liability, actor=user.id)
        db.commit()
    except util_svc.GstUtilisationError as exc:
        db.rollback()
        _raise(exc)
    return res


@router.post("/utilisation/{adj_id}/approve")
def approve_utilisation(adj_id: uuid.UUID, payload: ApproverIn, db: Session = Depends(get_company_db),
                        user: User = Depends(require_permission("gst.itc_post"))):
    try:
        rec = util_svc.approve_cross_adjustment(db, user.company_id, adj_id,
                                                approver_name=payload.approver_name, actor=user.id)
        db.commit()
    except util_svc.GstUtilisationError as exc:
        db.rollback()
        _raise(exc)
    return _row(rec)


class AuthorizeIn(_Strict):
    authorize: bool = False


@router.post("/utilisation/{adj_id}/post")
def post_utilisation(adj_id: uuid.UUID, payload: AuthorizeIn, db: Session = Depends(get_company_db),
                     user: User = Depends(require_permission("gst.itc_post"))):
    try:
        voucher, n = util_svc.post_cross_adjustment(
            db, user.company_id, adj_id, authorize=payload.authorize, posted_by=user.id,
            actor_name=_actor_name(user))
        db.commit()
    except util_svc.GstUtilisationError as exc:
        db.rollback()
        _raise(exc)
    return {"cross_adjustment_id": str(adj_id), "voucher_number": voucher.number,
            "posting_reference": voucher.posting_reference, "lines_posted": n}


@router.get("/utilisation")
def list_utilisation(tax_period: str | None = None, db: Session = Depends(get_company_db),
                     user: User = Depends(require_permission("gst.read"))):
    rows = util_svc.list_cross_adjustments(db, user.company_id, tax_period=tax_period)
    return {"items": [_row(r) for r in rows]}


# --------------------------- ledger reconciliation --------------------------

@router.get("/ledger-recon")
def ledger_recon(ledger_type: str, period: str, db: Session = Depends(get_company_db),
                 user: User = Depends(require_permission("gst.read"))):
    try:
        return ledger_svc.ledger_reconciliation(db, user.company_id, ledger_type=ledger_type,
                                                 period=period)
    except GstItcError as exc:
        _raise(exc)


# -------------------------------- reports -----------------------------------

@router.get("/reports/{report_key}")
def report(report_key: str, period: str, format: str = "json",
           db: Session = Depends(get_company_db),
           user: User = Depends(require_permission("gst.read"))):
    try:
        rep = reports_svc.build_report(db, user.company_id, report_key, period)
    except GstItcError as exc:
        _raise(exc)
    if format == "json":
        return rep
    if format == "excel":
        return Response(content=reports_svc.to_excel(rep), media_type=_XLSX, headers={
            "Content-Disposition": f'attachment; filename="{report_key}_{period}.xlsx"'})
    if format == "pdf":
        return Response(content=reports_svc.to_pdf(rep), media_type="application/pdf", headers={
            "Content-Disposition": f'attachment; filename="{report_key}_{period}.pdf"'})
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "format must be json, excel or pdf")
