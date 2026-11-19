"""Opening-balance batch API (Stage 6F-P1).

Foundation endpoints for the controlled opening-batch lifecycle: create a draft,
edit draft metadata, list, retrieve, submit, approve, close and cancel. Company
is taken from the session; factory scoping is enforced via ``authorize_factory``;
the acting user's identity is the server-resolved actor / submitter / approver.

Permissions: ``opening.manage`` for every mutating endpoint, ``opening.read`` for
the read endpoints. Request schemas are strict (``extra='forbid'``) so a client
cannot smuggle a status, company ownership, batch number or approver field — the
status is advanced ONLY through the dedicated transition endpoints. No opening
inventory or financial transaction is created anywhere in P1.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.context import FactoryAuthError, authorize_factory
from app.core.deps import get_company_db, require_permission
from app.models.core import User
from app.models.opening import OpeningBatch, OpeningBatchType
from app.models.opening_stock import OpeningImportFile, OpeningLineType, OpeningStockLine
from app.models.opening_roll import OpeningRollLine
from app.models.opening_custody import OpeningCustodyLine
from app.services import opening as svc
from app.services import opening_excel as xl
from app.services import opening_stock as stock_svc
from app.services import opening_rolls as roll_svc
from app.services import opening_custody as custody_svc

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# Batch type -> opening line type accepted by the P2 yarn/trading import endpoints.
_BATCH_LINE = {
    OpeningBatchType.YARN.value: OpeningLineType.YARN.value,
    OpeningBatchType.TRADING_FABRIC.value: OpeningLineType.TRADING_FABRIC.value,
}
# P3 manufactured-roll batch type (handled by the opening_rolls service).
_ROLL_TYPE = OpeningBatchType.MANUFACTURED_ROLLS.value
# P4 vendor-custody / vendor-held-WIP batch types (opening_custody service).
_CUSTODY_TYPES = {OpeningBatchType.VENDOR_CUSTODY.value, OpeningBatchType.WIP.value}

router = APIRouter(prefix="/api/opening", tags=["opening"])


# ------------------------------- helpers ------------------------------------

def _authz_factory(db: Session, user: User, factory_id: uuid.UUID | None) -> None:
    if factory_id is not None:
        try:
            authorize_factory(db, user, factory_id, user.company_id)
        except FactoryAuthError:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not authorized for this factory")


def _actor_name(user: User) -> str | None:
    return getattr(user, "full_name", None) or getattr(user, "name", None)


def _raise(exc: svc.OpeningBatchError):
    msg = str(exc)
    if isinstance(exc, svc.OpeningBatchNotFound):
        raise HTTPException(status.HTTP_404_NOT_FOUND, msg)
    if isinstance(exc, svc.SelfApprovalError):
        raise HTTPException(status.HTTP_409_CONFLICT, msg)
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, msg)


def _raise_any(exc: Exception):
    """Map P1 batch, P2 opening-stock and P3 opening-roll errors to HTTP codes."""
    msg = str(exc)
    if isinstance(exc, svc.OpeningBatchNotFound):
        raise HTTPException(status.HTTP_404_NOT_FOUND, msg)
    if isinstance(exc, (svc.SelfApprovalError, stock_svc.OpeningStockStateError,
                        roll_svc.OpeningRollStateError, custody_svc.OpeningCustodyStateError)):
        raise HTTPException(status.HTTP_409_CONFLICT, msg)
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, msg)


def _line_out(l: OpeningStockLine) -> dict:
    import json as _json
    return {
        "id": str(l.id), "batch_id": str(l.batch_id), "line_type": l.line_type,
        "row_number": l.row_number, "item_id": str(l.item_id) if l.item_id else None,
        "warehouse_id": str(l.warehouse_id) if l.warehouse_id else None,
        "factory_id": str(l.factory_id) if l.factory_id else None,
        "quantity": str(l.quantity), "uom_id": str(l.uom_id) if l.uom_id else None,
        "weight_kg": str(l.weight_kg) if l.weight_kg is not None else None,
        "unit_cost": str(l.unit_cost), "total_value": str(l.total_value),
        "effective_date": l.effective_date.isoformat() if l.effective_date else None,
        "source_document": l.source_document, "source_reference": l.source_reference,
        "yarn_lot_id": str(l.yarn_lot_id) if l.yarn_lot_id else None,
        "internal_lot_no": l.internal_lot_no, "lot_policy": l.lot_policy,
        "supplier_id": str(l.supplier_id) if l.supplier_id else None,
        "supplier_lot_no": l.supplier_lot_no, "legacy_unknown": l.legacy_unknown,
        "validation_status": l.validation_status,
        "validation_errors": _json.loads(l.validation_errors) if l.validation_errors else [],
        "stock_layer_id": str(l.stock_layer_id) if l.stock_layer_id else None,
        "committed_at": l.committed_at.isoformat() if l.committed_at else None,
    }


def _authz_line_factories(db: Session, user: User, import_file_id: uuid.UUID):
    """Every distinct factory referenced by the just-imported lines must be one
    the acting user may act in."""
    factory_ids = db.execute(select(OpeningStockLine.factory_id).where(
        OpeningStockLine.import_file_id == import_file_id,
        OpeningStockLine.factory_id.isnot(None)).distinct()).scalars().all()
    for fid in factory_ids:
        try:
            authorize_factory(db, user, fid, user.company_id)
        except FactoryAuthError:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "not authorized for a factory referenced in the import")


def _out(b: OpeningBatch) -> dict:
    return {
        "id": str(b.id),
        "company_id": str(b.company_id),
        "factory_id": str(b.factory_id) if b.factory_id else None,
        "batch_number": b.batch_number,
        "batch_type": b.batch_type,
        "status": b.status,
        "effective_date": b.effective_date.isoformat() if b.effective_date else None,
        "description": b.description,
        "source_document": b.source_document,
        "idempotency_key": b.idempotency_key,
        "submitted_by": str(b.submitted_by) if b.submitted_by else None,
        "submitted_at": b.submitted_at.isoformat() if b.submitted_at else None,
        "approved_by": str(b.approved_by) if b.approved_by else None,
        "approved_at": b.approved_at.isoformat() if b.approved_at else None,
        "closed_by": str(b.closed_by) if b.closed_by else None,
        "closed_at": b.closed_at.isoformat() if b.closed_at else None,
        "cancelled_by": str(b.cancelled_by) if b.cancelled_by else None,
        "cancelled_at": b.cancelled_at.isoformat() if b.cancelled_at else None,
        "cancel_reason": b.cancel_reason,
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "created_by": str(b.created_by) if b.created_by else None,
        "updated_at": b.updated_at.isoformat() if b.updated_at else None,
        "updated_by": str(b.updated_by) if b.updated_by else None,
    }


# ------------------------------- schemas ------------------------------------

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BatchCreateIn(_Strict):
    batch_type: str
    effective_date: date
    factory_id: uuid.UUID | None = None
    description: str | None = None
    source_document: str | None = None
    idempotency_key: str | None = None


class BatchUpdateIn(_Strict):
    description: str | None = None
    source_document: str | None = None


class CancelIn(_Strict):
    reason: str | None = None


# ------------------------------- endpoints ----------------------------------

@router.post("/batches", status_code=status.HTTP_201_CREATED)
def create_batch(payload: BatchCreateIn, db: Session = Depends(get_company_db),
                 user: User = Depends(require_permission("opening.manage"))):
    _authz_factory(db, user, payload.factory_id)
    try:
        batch = svc.create_batch(
            db, user.company_id, created_by=user.id, actor_name=_actor_name(user),
            **payload.model_dump())
        db.commit()
    except IntegrityError:
        db.rollback()
        # A create race on the same idempotency key: return the row that won.
        key = (payload.idempotency_key or "").strip() or None
        if key:
            existing = db.execute(select(OpeningBatch).where(
                OpeningBatch.company_id == user.company_id,
                OpeningBatch.idempotency_key == key)).scalar_one_or_none()
            if existing is not None:
                return _out(existing)
        raise HTTPException(status.HTTP_409_CONFLICT, "Opening batch could not be created (conflict)")
    except svc.OpeningBatchError as exc:
        db.rollback()
        _raise(exc)
    return _out(batch)


@router.get("/batches")
def list_batches(status_filter: str | None = None, batch_type: str | None = None,
                 factory_id: uuid.UUID | None = None, limit: int = 100, offset: int = 0,
                 db: Session = Depends(get_company_db),
                 user: User = Depends(require_permission("opening.read"))):
    rows, total = svc.list_batches(db, user.company_id, status=status_filter,
                                   batch_type=batch_type, factory_id=factory_id,
                                   limit=limit, offset=offset)
    return {"total": total, "limit": limit, "offset": offset, "items": [_out(r) for r in rows]}


@router.get("/batches/{batch_id}")
def get_batch(batch_id: uuid.UUID, db: Session = Depends(get_company_db),
              user: User = Depends(require_permission("opening.read"))):
    try:
        return _out(svc.get_batch(db, user.company_id, batch_id))
    except svc.OpeningBatchError as exc:
        _raise(exc)


@router.patch("/batches/{batch_id}")
def update_batch(batch_id: uuid.UUID, payload: BatchUpdateIn,
                 db: Session = Depends(get_company_db),
                 user: User = Depends(require_permission("opening.manage"))):
    fields = payload.model_dump(exclude_unset=True)
    try:
        batch = svc.update_draft(db, user.company_id, batch_id, updated_by=user.id,
                                 actor_name=_actor_name(user), **fields)
        db.commit()
    except svc.OpeningBatchError as exc:
        db.rollback()
        _raise(exc)
    return _out(batch)


@router.post("/batches/{batch_id}/submit")
def submit_batch(batch_id: uuid.UUID, db: Session = Depends(get_company_db),
                 user: User = Depends(require_permission("opening.manage"))):
    try:
        batch = svc.get_batch(db, user.company_id, batch_id)
        if batch.batch_type == _ROLL_TYPE:
            # P3 manufactured-roll batch: validate all roll lines, then P1 submit.
            batch = roll_svc.submit_batch(db, user.company_id, batch_id,
                                          submitted_by=user.id, actor_name=_actor_name(user))
        elif batch.batch_type in _CUSTODY_TYPES:
            # P4 vendor-custody batch: validate all custody lines, then P1 submit.
            batch = custody_svc.submit_batch(db, user.company_id, batch_id,
                                             submitted_by=user.id, actor_name=_actor_name(user))
        elif batch.batch_type in _BATCH_LINE:
            # P2 stock batch: validate all lines, then P1 DRAFT->SUBMITTED.
            batch = stock_svc.submit_batch(db, user.company_id, batch_id,
                                           submitted_by=user.id, actor_name=_actor_name(user))
        else:
            batch = svc.submit_batch(db, user.company_id, batch_id, submitted_by=user.id,
                                     actor_name=_actor_name(user))
        db.commit()
    except (svc.OpeningBatchError, stock_svc.OpeningStockError, roll_svc.OpeningRollError,
            custody_svc.OpeningCustodyError) as exc:
        db.rollback()
        _raise_any(exc)
    return _out(batch)


@router.post("/batches/{batch_id}/approve")
def approve_batch(batch_id: uuid.UUID, db: Session = Depends(get_company_db),
                  user: User = Depends(require_permission("opening.manage"))):
    committed = None
    try:
        batch = svc.get_batch(db, user.company_id, batch_id)
        if batch.batch_type == _ROLL_TYPE:
            # P3 manufactured-roll batch: P1 maker-checker approve + register rolls
            # and create opening FIFO layers (no voucher), in one transaction.
            batch, committed = roll_svc.approve_batch(db, user.company_id, batch_id,
                                                      approved_by=user.id,
                                                      actor_name=_actor_name(user))
        elif batch.batch_type in _CUSTODY_TYPES:
            # P4 vendor-custody: P1 maker-checker approve + create custody FIFO
            # layers + opening-origin JobWorkIssue (no voucher), one transaction.
            batch, committed = custody_svc.approve_batch(db, user.company_id, batch_id,
                                                         approved_by=user.id,
                                                         actor_name=_actor_name(user))
        elif batch.batch_type in _BATCH_LINE:
            # P2 stock batch: P1 maker-checker approve + create opening FIFO layers
            # (no accounting voucher), all in one transaction.
            batch, committed = stock_svc.approve_batch(db, user.company_id, batch_id,
                                                       approved_by=user.id,
                                                       actor_name=_actor_name(user))
        else:
            batch = svc.approve_batch(db, user.company_id, batch_id, approved_by=user.id,
                                      actor_name=_actor_name(user))
        db.commit()
    except IntegrityError:
        # A concurrent approval registered the same company-scoped identity first
        # (uq_yarn_lot_company_internal_no / uq_fabric_rolls_company_roll_no /
        # uq_fabric_rolls_company_legacy_label). The loser rolls back cleanly — no
        # lot/roll, no stock layer, no inventory transaction — and gets a 409.
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "a concurrent opening registered the same identity; "
                            "this batch was rolled back — revalidate and retry")
    except (svc.OpeningBatchError, stock_svc.OpeningStockError, roll_svc.OpeningRollError,
            custody_svc.OpeningCustodyError) as exc:
        db.rollback()
        _raise_any(exc)
    out = _out(batch)
    if committed is not None:
        out["lines_committed"] = committed
    return out


@router.post("/batches/{batch_id}/close")
def close_batch(batch_id: uuid.UUID, db: Session = Depends(get_company_db),
                user: User = Depends(require_permission("opening.manage"))):
    try:
        batch = svc.close_batch(db, user.company_id, batch_id, closed_by=user.id,
                                actor_name=_actor_name(user))
        db.commit()
    except svc.OpeningBatchError as exc:
        db.rollback()
        _raise(exc)
    return _out(batch)


@router.post("/batches/{batch_id}/cancel")
def cancel_batch(batch_id: uuid.UUID, payload: CancelIn | None = None,
                 db: Session = Depends(get_company_db),
                 user: User = Depends(require_permission("opening.manage"))):
    reason = payload.reason if payload is not None else None
    try:
        batch = svc.cancel_batch(db, user.company_id, batch_id, cancelled_by=user.id,
                                 reason=reason, actor_name=_actor_name(user))
        db.commit()
    except svc.OpeningBatchError as exc:
        db.rollback()
        _raise(exc)
    return _out(batch)


# ===========================================================================
# P2 — opening stock: templates, import, lines, preview, validation
# ===========================================================================

def _get_draft_stock_batch(db: Session, user: User, batch_id: uuid.UUID, line_type: str):
    """Load a batch, 404 if absent, 409 if its type does not accept ``line_type``."""
    try:
        batch = svc.get_batch(db, user.company_id, batch_id)
    except svc.OpeningBatchNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if _BATCH_LINE.get(batch.batch_type) != line_type:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"batch type '{batch.batch_type}' does not accept {line_type} lines")
    return batch


def _template_response(content: bytes, filename: str) -> Response:
    return Response(content=content, media_type=_XLSX_MEDIA,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/templates/yarn")
def template_yarn(user: User = Depends(require_permission("opening.read"))):
    return _template_response(xl.build_yarn_template(), "opening_yarn_template.xlsx")


@router.get("/templates/trading")
def template_trading(user: User = Depends(require_permission("opening.read"))):
    return _template_response(xl.build_trading_template(), "opening_trading_template.xlsx")


def _import(db: Session, user: User, batch_id: uuid.UUID, line_type: str, upload: UploadFile):
    batch = _get_draft_stock_batch(db, user, batch_id, line_type)
    content = upload.file.read()
    if not content:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "empty file")
    file_hash = hashlib.sha256(content).hexdigest()
    dup = db.execute(select(OpeningImportFile).where(
        OpeningImportFile.batch_id == batch.id,
        OpeningImportFile.file_hash == file_hash)).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "this exact file has already been imported into the batch")
    try:
        meta, rows = xl.parse_workbook(content, expected_type=line_type)
    except xl.OpeningTemplateError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    import_file = OpeningImportFile(
        company_id=batch.company_id, batch_id=batch.id, line_type=line_type,
        file_name=(upload.filename or "upload.xlsx")[:255], file_hash=file_hash, content=content,
        template_version=meta.get("template_version"), uploaded_by=user.id, uploaded_at=datetime.now(timezone.utc),
        created_by=user.id, updated_by=user.id)
    db.add(import_file)
    db.flush()
    try:
        summary = stock_svc.import_rows(db, batch, line_type=line_type, rows=rows,
                                        import_file=import_file, actor=user.id)
        _authz_line_factories(db, user, import_file.id)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except stock_svc.OpeningStockError as exc:
        db.rollback()
        _raise_any(exc)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "duplicate opening line (batch dedup constraint)")
    return summary


@router.post("/batches/{batch_id}/yarn/import", status_code=status.HTTP_201_CREATED)
def import_yarn(batch_id: uuid.UUID, file: UploadFile = File(...),
                db: Session = Depends(get_company_db),
                user: User = Depends(require_permission("opening.manage"))):
    return _import(db, user, batch_id, OpeningLineType.YARN.value, file)


@router.post("/batches/{batch_id}/trading/import", status_code=status.HTTP_201_CREATED)
def import_trading(batch_id: uuid.UUID, file: UploadFile = File(...),
                   db: Session = Depends(get_company_db),
                   user: User = Depends(require_permission("opening.manage"))):
    return _import(db, user, batch_id, OpeningLineType.TRADING_FABRIC.value, file)


@router.get("/batches/{batch_id}/lines")
def list_lines(batch_id: uuid.UUID, status_filter: str | None = None,
               line_type: str | None = None, limit: int = 100, offset: int = 0,
               db: Session = Depends(get_company_db),
               user: User = Depends(require_permission("opening.read"))):
    try:
        batch, rows, total = stock_svc.list_lines(db, user.company_id, batch_id,
                                                  status=status_filter, line_type=line_type,
                                                  limit=limit, offset=offset)
    except svc.OpeningBatchError as exc:
        _raise(exc)
    return {"batch_id": str(batch.id), "total": total, "limit": limit, "offset": offset,
            "items": [_line_out(r) for r in rows]}


@router.get("/batches/{batch_id}/rejections")
def list_rejections(batch_id: uuid.UUID, db: Session = Depends(get_company_db),
                    user: User = Depends(require_permission("opening.read"))):
    try:
        batch, rows = stock_svc.rejections(db, user.company_id, batch_id)
    except svc.OpeningBatchError as exc:
        _raise(exc)
    return {"batch_id": str(batch.id), "rejections": rows}


@router.get("/batches/{batch_id}/preview")
def preview(batch_id: uuid.UUID, db: Session = Depends(get_company_db),
            user: User = Depends(require_permission("opening.read"))):
    try:
        return stock_svc.reconciliation_preview(db, user.company_id, batch_id)
    except svc.OpeningBatchError as exc:
        _raise(exc)


@router.post("/batches/{batch_id}/validate")
def validate(batch_id: uuid.UUID, db: Session = Depends(get_company_db),
             user: User = Depends(require_permission("opening.manage"))):
    try:
        batch = svc.get_batch(db, user.company_id, batch_id)
        if batch.batch_type == _ROLL_TYPE:
            result = roll_svc.revalidate_batch(db, user.company_id, batch_id)
        elif batch.batch_type in _CUSTODY_TYPES:
            result = custody_svc.revalidate_batch(db, user.company_id, batch_id)
        else:
            result = stock_svc.revalidate_batch(db, user.company_id, batch_id)
        db.commit()
    except (svc.OpeningBatchError, stock_svc.OpeningStockError, roll_svc.OpeningRollError,
            custody_svc.OpeningCustodyError) as exc:
        db.rollback()
        _raise_any(exc)
    return result


# ===========================================================================
# P4 — vendor-custody / vendor-held-WIP opening: template, import, lines, preview
# ===========================================================================

def _custody_line_out(l: OpeningCustodyLine) -> dict:
    import json as _json
    return {
        "id": str(l.id), "batch_id": str(l.batch_id), "row_number": l.row_number,
        "line_kind": l.line_kind, "custody_type": l.custody_type,
        "registration_mode": l.registration_mode,
        "job_worker_id": str(l.job_worker_id) if l.job_worker_id else None,
        "prior_vendor_id": str(l.prior_vendor_id) if l.prior_vendor_id else None,
        "process": l.process, "source_jobwork_ref": l.source_jobwork_ref,
        "factory_id": str(l.factory_id) if l.factory_id else None,
        "erp_roll_no": l.erp_roll_no, "legacy_label": l.legacy_label,
        "roll_kind": l.roll_kind, "origin_stream": l.origin_stream,
        "fabric_roll_id": str(l.fabric_roll_id) if l.fabric_roll_id else None,
        "yarn_id": str(l.yarn_id) if l.yarn_id else None, "internal_lot_no": l.internal_lot_no,
        "yarn_lot_id": str(l.yarn_lot_id) if l.yarn_lot_id else None,
        "net_weight_kg": str(l.net_weight_kg) if l.net_weight_kg is not None else None,
        "quantity": str(l.quantity), "unit_cost": str(l.unit_cost), "total_value": str(l.total_value),
        "valuation_source": l.valuation_source, "disputed_unknown": l.disputed_unknown,
        "effective_date": l.effective_date.isoformat() if l.effective_date else None,
        "source_document": l.source_document, "source_reference": l.source_reference, "notes": l.notes,
        "validation_status": l.validation_status,
        "validation_errors": _json.loads(l.validation_errors) if l.validation_errors else [],
        "custody_warehouse_id": str(l.custody_warehouse_id) if l.custody_warehouse_id else None,
        "stock_layer_id": str(l.stock_layer_id) if l.stock_layer_id else None,
        "job_work_issue_id": str(l.job_work_issue_id) if l.job_work_issue_id else None,
        "committed_at": l.committed_at.isoformat() if l.committed_at else None,
    }


def _authz_custody_line_factories(db: Session, user: User, import_file_id: uuid.UUID):
    factory_ids = db.execute(select(OpeningCustodyLine.factory_id).where(
        OpeningCustodyLine.import_file_id == import_file_id,
        OpeningCustodyLine.factory_id.isnot(None)).distinct()).scalars().all()
    for fid in factory_ids:
        try:
            authorize_factory(db, user, fid, user.company_id)
        except FactoryAuthError:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "not authorized for a factory referenced in the import")


def _get_draft_custody_batch(db: Session, user: User, batch_id: uuid.UUID):
    try:
        batch = svc.get_batch(db, user.company_id, batch_id)
    except svc.OpeningBatchNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if batch.batch_type not in _CUSTODY_TYPES:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"batch type '{batch.batch_type}' does not accept vendor-custody lines")
    return batch


@router.get("/templates/vendor-custody")
def template_vendor_custody(user: User = Depends(require_permission("opening.read"))):
    return _template_response(xl.build_vendor_custody_template(),
                              "opening_vendor_custody_template.xlsx")


@router.post("/batches/{batch_id}/vendor-custody/import", status_code=status.HTTP_201_CREATED)
def import_vendor_custody(batch_id: uuid.UUID, file: UploadFile = File(...),
                          db: Session = Depends(get_company_db),
                          user: User = Depends(require_permission("opening.manage"))):
    batch = _get_draft_custody_batch(db, user, batch_id)
    content = file.file.read()
    if not content:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "empty file")
    file_hash = hashlib.sha256(content).hexdigest()
    dup = db.execute(select(OpeningImportFile).where(
        OpeningImportFile.batch_id == batch.id,
        OpeningImportFile.file_hash == file_hash)).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "this exact file has already been imported into the batch")
    try:
        meta, rows = xl.parse_workbook(content, expected_type=OpeningLineType.VENDOR_CUSTODY.value)
    except xl.OpeningTemplateError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    import_file = OpeningImportFile(
        company_id=batch.company_id, batch_id=batch.id,
        line_type=OpeningLineType.VENDOR_CUSTODY.value,
        file_name=(file.filename or "upload.xlsx")[:255], file_hash=file_hash, content=content,
        template_version=meta.get("template_version"), uploaded_by=user.id,
        uploaded_at=datetime.now(timezone.utc), created_by=user.id, updated_by=user.id)
    db.add(import_file)
    db.flush()
    try:
        summary = custody_svc.import_rows(db, batch, rows=rows, import_file=import_file, actor=user.id)
        _authz_custody_line_factories(db, user, import_file.id)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except custody_svc.OpeningCustodyError as exc:
        db.rollback()
        _raise_any(exc)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "duplicate opening custody line (batch dedup)")
    return summary


@router.get("/batches/{batch_id}/custody-lines")
def list_custody_lines(batch_id: uuid.UUID, status_filter: str | None = None,
                       limit: int = 100, offset: int = 0,
                       db: Session = Depends(get_company_db),
                       user: User = Depends(require_permission("opening.read"))):
    try:
        batch, rows, total = custody_svc.list_lines(db, user.company_id, batch_id,
                                                    status=status_filter, limit=limit, offset=offset)
    except svc.OpeningBatchError as exc:
        _raise(exc)
    return {"batch_id": str(batch.id), "total": total, "limit": limit, "offset": offset,
            "items": [_custody_line_out(r) for r in rows]}


@router.get("/batches/{batch_id}/custody-rejections")
def list_custody_rejections(batch_id: uuid.UUID, db: Session = Depends(get_company_db),
                            user: User = Depends(require_permission("opening.read"))):
    try:
        batch, rows = custody_svc.rejections(db, user.company_id, batch_id)
    except svc.OpeningBatchError as exc:
        _raise(exc)
    return {"batch_id": str(batch.id), "rejections": rows}


@router.get("/batches/{batch_id}/custody-preview")
def custody_preview(batch_id: uuid.UUID, db: Session = Depends(get_company_db),
                    user: User = Depends(require_permission("opening.read"))):
    try:
        return custody_svc.reconciliation_preview(db, user.company_id, batch_id)
    except svc.OpeningBatchError as exc:
        _raise(exc)


# ===========================================================================
# P3 — manufactured fabric roll opening: template, import, lines, preview
# ===========================================================================

def _roll_line_out(l: OpeningRollLine) -> dict:
    import json as _json
    return {
        "id": str(l.id), "batch_id": str(l.batch_id), "row_number": l.row_number,
        "registration_mode": l.registration_mode, "roll_kind": l.roll_kind,
        "origin_stream": l.origin_stream,
        "erp_roll_no": l.erp_roll_no, "legacy_label": l.legacy_label,
        "fabric_roll_id": str(l.fabric_roll_id) if l.fabric_roll_id else None,
        "factory_id": str(l.factory_id) if l.factory_id else None,
        "warehouse_id": str(l.warehouse_id) if l.warehouse_id else None,
        "warehouse_category": l.warehouse_category,
        "fabric_type_id": str(l.fabric_type_id) if l.fabric_type_id else None,
        "structure": l.structure, "description": l.description,
        "gsm": str(l.gsm) if l.gsm is not None else None,
        "width_cm": str(l.width_cm) if l.width_cm is not None else None,
        "colour": l.colour, "shade_id": str(l.shade_id) if l.shade_id else None,
        "dye_lot": l.dye_lot, "net_weight_kg": str(l.net_weight_kg),
        "uom_id": str(l.uom_id) if l.uom_id else None,
        "length_m": str(l.length_m) if l.length_m is not None else None,
        "unit_cost": str(l.unit_cost), "total_value": str(l.total_value),
        "valuation_source": l.valuation_source,
        "quality_status": l.quality_status, "job_ref": l.job_ref,
        "processing_ref": l.processing_ref, "source_document": l.source_document,
        "source_reference": l.source_reference,
        "physical_verification_status": l.physical_verification_status,
        "disputed_unknown": l.disputed_unknown, "notes": l.notes,
        "effective_date": l.effective_date.isoformat() if l.effective_date else None,
        "validation_status": l.validation_status,
        "validation_errors": _json.loads(l.validation_errors) if l.validation_errors else [],
        "stock_layer_id": str(l.stock_layer_id) if l.stock_layer_id else None,
        "committed_at": l.committed_at.isoformat() if l.committed_at else None,
    }


def _authz_roll_line_factories(db: Session, user: User, import_file_id: uuid.UUID):
    factory_ids = db.execute(select(OpeningRollLine.factory_id).where(
        OpeningRollLine.import_file_id == import_file_id,
        OpeningRollLine.factory_id.isnot(None)).distinct()).scalars().all()
    for fid in factory_ids:
        try:
            authorize_factory(db, user, fid, user.company_id)
        except FactoryAuthError:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "not authorized for a factory referenced in the import")


def _get_draft_roll_batch(db: Session, user: User, batch_id: uuid.UUID):
    try:
        batch = svc.get_batch(db, user.company_id, batch_id)
    except svc.OpeningBatchNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if batch.batch_type != _ROLL_TYPE:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"batch type '{batch.batch_type}' does not accept manufactured-roll lines")
    return batch


@router.get("/templates/manufactured-rolls")
def template_manufactured_rolls(user: User = Depends(require_permission("opening.read"))):
    return _template_response(xl.build_manufactured_rolls_template(),
                              "opening_manufactured_rolls_template.xlsx")


@router.post("/batches/{batch_id}/manufactured-rolls/import", status_code=status.HTTP_201_CREATED)
def import_manufactured_rolls(batch_id: uuid.UUID, file: UploadFile = File(...),
                              db: Session = Depends(get_company_db),
                              user: User = Depends(require_permission("opening.manage"))):
    batch = _get_draft_roll_batch(db, user, batch_id)
    content = file.file.read()
    if not content:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "empty file")
    file_hash = hashlib.sha256(content).hexdigest()
    dup = db.execute(select(OpeningImportFile).where(
        OpeningImportFile.batch_id == batch.id,
        OpeningImportFile.file_hash == file_hash)).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "this exact file has already been imported into the batch")
    try:
        meta, rows = xl.parse_workbook(content, expected_type=OpeningLineType.MANUFACTURED_ROLLS.value)
    except xl.OpeningTemplateError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    import_file = OpeningImportFile(
        company_id=batch.company_id, batch_id=batch.id,
        line_type=OpeningLineType.MANUFACTURED_ROLLS.value,
        file_name=(file.filename or "upload.xlsx")[:255], file_hash=file_hash, content=content,
        template_version=meta.get("template_version"), uploaded_by=user.id,
        uploaded_at=datetime.now(timezone.utc), created_by=user.id, updated_by=user.id)
    db.add(import_file)
    db.flush()
    try:
        summary = roll_svc.import_rows(db, batch, rows=rows, import_file=import_file, actor=user.id)
        _authz_roll_line_factories(db, user, import_file.id)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except roll_svc.OpeningRollError as exc:
        db.rollback()
        _raise_any(exc)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "duplicate opening roll line (batch dedup)")
    return summary


@router.get("/batches/{batch_id}/roll-lines")
def list_roll_lines(batch_id: uuid.UUID, status_filter: str | None = None,
                    limit: int = 100, offset: int = 0,
                    db: Session = Depends(get_company_db),
                    user: User = Depends(require_permission("opening.read"))):
    try:
        batch, rows, total = roll_svc.list_lines(db, user.company_id, batch_id,
                                                 status=status_filter, limit=limit, offset=offset)
    except svc.OpeningBatchError as exc:
        _raise(exc)
    return {"batch_id": str(batch.id), "total": total, "limit": limit, "offset": offset,
            "items": [_roll_line_out(r) for r in rows]}


@router.get("/batches/{batch_id}/roll-rejections")
def list_roll_rejections(batch_id: uuid.UUID, db: Session = Depends(get_company_db),
                         user: User = Depends(require_permission("opening.read"))):
    try:
        batch, rows = roll_svc.rejections(db, user.company_id, batch_id)
    except svc.OpeningBatchError as exc:
        _raise(exc)
    return {"batch_id": str(batch.id), "rejections": rows}


@router.get("/batches/{batch_id}/roll-preview")
def roll_preview(batch_id: uuid.UUID, db: Session = Depends(get_company_db),
                 user: User = Depends(require_permission("opening.read"))):
    try:
        return roll_svc.reconciliation_preview(db, user.company_id, batch_id)
    except svc.OpeningBatchError as exc:
        _raise(exc)
