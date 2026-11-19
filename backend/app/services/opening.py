"""Opening-balance batch services (Stage 6F-P1).

The foundation lifecycle for :class:`OpeningBatch`: create a draft, edit draft
metadata, list, retrieve, submit, approve, close and cancel. Every state change
goes through one of these authorized functions; the API never writes a status
column directly.

P1 scope guard (enforced by construction and asserted by tests): these functions
create ONLY ``OpeningBatch`` rows and ``AuditLog`` rows. They NEVER create a
stock layer, FabricRoll, YarnLot, vendor-custody record, GL voucher or inventory
movement; NEVER call ``inventory.receive()`` / ``inventory.consume()`` or the
posting engine; and store NO opening quantities or values. There is no POSTED
state.

Rules enforced here:

* Only a DRAFT batch's metadata may be edited.
* Lifecycle is strictly DRAFT -> SUBMITTED -> APPROVED -> CLOSED; CANCELLED is
  reachable only from DRAFT or SUBMITTED. Terminal batches (CLOSED / CANCELLED)
  can never be edited or reopened.
* Maker-checker: the user who submitted a batch may not approve it.
* Company scoping on every lookup; a factory (when set) must belong to the
  company.
* The actor, company, factory and approver identities are resolved server-side
  by the caller (the route) — never taken from the client payload.
* Idempotent: re-submitting / approving / closing a batch already in that target
  state is a no-op; a create retry carrying the same idempotency key returns the
  same batch instead of minting a second one.
* Flush-only: the route owns the single transaction / commit.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.core import Branch
from app.models.opening import (
    OPENING_BATCH_TYPES,
    OpeningBatch,
    OpeningBatchStatus,
    OpeningBatchType,
)
from app.services.accounting.posting_engine import fiscal_year_for
from app.services.audit import record_audit
from app.services.sequences import next_document_number

# Sentinel so partial updates can tell "field omitted" from "explicit None".
_UNSET: object = object()

_OPENING_DOC_TYPE = "OPENBATCH"
_OPENING_PREFIX = "OB-"


class OpeningBatchError(ValueError):
    """An invalid opening-batch request (bad reference, bad state, bad value)."""


class OpeningBatchNotFound(OpeningBatchError):
    """The requested opening batch does not exist in the active company."""


class SelfApprovalError(OpeningBatchError):
    """The user who submitted a batch attempted to approve their own batch."""


# ------------------------------- helpers ------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(value: str | None, maxlen: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:maxlen] if text else None


def _normalize_type(batch_type) -> str:
    if isinstance(batch_type, OpeningBatchType):
        return batch_type.value
    val = str(batch_type or "").strip().upper()
    if val not in OPENING_BATCH_TYPES:
        raise OpeningBatchError(f"Unknown opening batch type '{batch_type}'")
    return val


def _require_factory(db: Session, company_id: uuid.UUID, factory_id: uuid.UUID | None) -> None:
    """A null factory means company-wide; when set it must be in the company."""
    if factory_id is None:
        return
    branch = db.get(Branch, factory_id)
    if branch is None or branch.company_id != company_id:
        raise OpeningBatchError("Factory is not in the active company")


def _audit(db: Session, actor_id, actor_name, action: str, batch: OpeningBatch,
           extra: dict | None = None) -> None:
    detail = {
        "company_id": str(batch.company_id),
        "factory_id": str(batch.factory_id) if batch.factory_id else None,
        "batch_id": str(batch.id),
        "batch_number": batch.batch_number,
        "batch_type": batch.batch_type,
        "status": batch.status,
    }
    if extra:
        detail.update(extra)
    record_audit(db, action=action, entity="opening_batch", entity_id=str(batch.id),
                 actor_id=actor_id, actor_name=actor_name, detail=detail)


def _lock(db: Session, company_id: uuid.UUID, batch_id: uuid.UUID) -> OpeningBatch:
    """Fetch a batch with a row lock on PostgreSQL (no-op on SQLite) so
    concurrent transitions serialize. Company-scoped."""
    stmt = select(OpeningBatch).where(OpeningBatch.id == batch_id)
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update()
    batch = db.execute(stmt).scalar_one_or_none()
    if batch is None or batch.company_id != company_id:
        raise OpeningBatchNotFound("Opening batch not found in the active company")
    return batch


# ------------------------------- lifecycle ----------------------------------

def create_batch(db: Session, company_id: uuid.UUID, *, batch_type, effective_date: date,
                 factory_id: uuid.UUID | None = None, description: str | None = None,
                 source_document: str | None = None, idempotency_key: str | None = None,
                 created_by: uuid.UUID | None = None, actor_name: str | None = None) -> OpeningBatch:
    """Create a DRAFT opening batch. Mints a company-scoped, gap-free batch
    number. Creates NO opening transaction of any kind."""
    bt = _normalize_type(batch_type)
    if effective_date is None:
        raise OpeningBatchError("Effective opening date is required")
    _require_factory(db, company_id, factory_id)

    key = _clean(idempotency_key, 80)
    if key is not None:
        existing = db.execute(select(OpeningBatch).where(
            OpeningBatch.company_id == company_id,
            OpeningBatch.idempotency_key == key)).scalar_one_or_none()
        if existing is not None:
            # Idempotent create: the same request returns the same batch, unchanged.
            return existing

    number = next_document_number(db, company_id=company_id, doc_type=_OPENING_DOC_TYPE,
                                  fiscal_year=fiscal_year_for(effective_date), prefix=_OPENING_PREFIX)
    batch = OpeningBatch(
        company_id=company_id, factory_id=factory_id, batch_number=number, batch_type=bt,
        effective_date=effective_date, description=_clean(description, 300),
        source_document=_clean(source_document, 120), status=OpeningBatchStatus.DRAFT.value,
        idempotency_key=key, created_by=created_by, updated_by=created_by)
    db.add(batch)
    db.flush()
    _audit(db, created_by, actor_name, "OPENING_BATCH_CREATE", batch,
           {"after_status": OpeningBatchStatus.DRAFT.value})
    return batch


def get_batch(db: Session, company_id: uuid.UUID, batch_id: uuid.UUID) -> OpeningBatch:
    batch = db.get(OpeningBatch, batch_id)
    if batch is None or batch.company_id != company_id:
        raise OpeningBatchNotFound("Opening batch not found in the active company")
    return batch


def list_batches(db: Session, company_id: uuid.UUID, *, status: str | None = None,
                 batch_type: str | None = None, factory_id: uuid.UUID | None = None,
                 limit: int = 100, offset: int = 0):
    stmt = select(OpeningBatch).where(OpeningBatch.company_id == company_id)
    if status:
        stmt = stmt.where(OpeningBatch.status == status)
    if batch_type:
        stmt = stmt.where(OpeningBatch.batch_type == batch_type)
    if factory_id:
        stmt = stmt.where(OpeningBatch.factory_id == factory_id)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = db.execute(stmt.order_by(OpeningBatch.effective_date.desc(), OpeningBatch.id)
                      .limit(max(1, min(limit, 200))).offset(max(0, offset))).scalars().all()
    return rows, total


def update_draft(db: Session, company_id: uuid.UUID, batch_id: uuid.UUID, *,
                 description=_UNSET, source_document=_UNSET,
                 updated_by: uuid.UUID | None = None, actor_name: str | None = None) -> OpeningBatch:
    """Edit safe metadata on a DRAFT batch only. Never changes company, factory,
    type, batch number or status; those are immutable after creation."""
    batch = _lock(db, company_id, batch_id)
    if batch.status != OpeningBatchStatus.DRAFT.value:
        raise OpeningBatchError(f"Only a DRAFT opening batch can be edited (is {batch.status})")
    before = {"description": batch.description, "source_document": batch.source_document}
    if description is not _UNSET:
        batch.description = _clean(description, 300)
    if source_document is not _UNSET:
        batch.source_document = _clean(source_document, 120)
    batch.updated_by = updated_by
    db.flush()
    _audit(db, updated_by, actor_name, "OPENING_BATCH_UPDATE", batch,
           {"before": before,
            "after": {"description": batch.description, "source_document": batch.source_document}})
    return batch


def submit_batch(db: Session, company_id: uuid.UUID, batch_id: uuid.UUID, *,
                 submitted_by: uuid.UUID | None = None, actor_name: str | None = None) -> OpeningBatch:
    """DRAFT -> SUBMITTED. Idempotent when already SUBMITTED."""
    batch = _lock(db, company_id, batch_id)
    if batch.status == OpeningBatchStatus.SUBMITTED.value:
        return batch
    if batch.status != OpeningBatchStatus.DRAFT.value:
        raise OpeningBatchError(f"Only a DRAFT opening batch can be submitted (is {batch.status})")
    _require_factory(db, company_id, batch.factory_id)
    batch.status = OpeningBatchStatus.SUBMITTED.value
    batch.submitted_by = submitted_by
    batch.submitted_at = _now()
    batch.updated_by = submitted_by
    db.flush()
    _audit(db, submitted_by, actor_name, "OPENING_BATCH_SUBMIT", batch,
           {"before_status": OpeningBatchStatus.DRAFT.value,
            "after_status": OpeningBatchStatus.SUBMITTED.value})
    return batch


def approve_batch(db: Session, company_id: uuid.UUID, batch_id: uuid.UUID, *,
                  approved_by: uuid.UUID | None = None, actor_name: str | None = None) -> OpeningBatch:
    """SUBMITTED -> APPROVED. Maker-checker: the submitter cannot approve their
    own batch. Idempotent when already APPROVED. Creates NO opening transaction."""
    batch = _lock(db, company_id, batch_id)
    if batch.status == OpeningBatchStatus.APPROVED.value:
        return batch
    if batch.status != OpeningBatchStatus.SUBMITTED.value:
        raise OpeningBatchError(f"Only a SUBMITTED opening batch can be approved (is {batch.status})")
    if approved_by is None:
        raise OpeningBatchError("An approver identity is required to approve an opening batch")
    if batch.submitted_by is not None and approved_by == batch.submitted_by:
        raise SelfApprovalError("The user who submitted an opening batch cannot approve it")
    batch.status = OpeningBatchStatus.APPROVED.value
    batch.approved_by = approved_by
    batch.approved_at = _now()
    batch.updated_by = approved_by
    db.flush()
    _audit(db, approved_by, actor_name, "OPENING_BATCH_APPROVE", batch,
           {"before_status": OpeningBatchStatus.SUBMITTED.value,
            "after_status": OpeningBatchStatus.APPROVED.value,
            "submitted_by": str(batch.submitted_by) if batch.submitted_by else None})
    return batch


def close_batch(db: Session, company_id: uuid.UUID, batch_id: uuid.UUID, *,
                closed_by: uuid.UUID | None = None, actor_name: str | None = None) -> OpeningBatch:
    """APPROVED -> CLOSED (terminal). Idempotent when already CLOSED."""
    batch = _lock(db, company_id, batch_id)
    if batch.status == OpeningBatchStatus.CLOSED.value:
        return batch
    if batch.status != OpeningBatchStatus.APPROVED.value:
        raise OpeningBatchError(f"Only an APPROVED opening batch can be closed (is {batch.status})")
    batch.status = OpeningBatchStatus.CLOSED.value
    batch.closed_by = closed_by
    batch.closed_at = _now()
    batch.updated_by = closed_by
    db.flush()
    _audit(db, closed_by, actor_name, "OPENING_BATCH_CLOSE", batch,
           {"before_status": OpeningBatchStatus.APPROVED.value,
            "after_status": OpeningBatchStatus.CLOSED.value})
    return batch


def cancel_batch(db: Session, company_id: uuid.UUID, batch_id: uuid.UUID, *,
                 cancelled_by: uuid.UUID | None = None, reason: str | None = None,
                 actor_name: str | None = None) -> OpeningBatch:
    """Cancel a batch that has not yet been approved (DRAFT or SUBMITTED only).
    Idempotent when already CANCELLED. An APPROVED or CLOSED batch can never be
    cancelled or reopened."""
    batch = _lock(db, company_id, batch_id)
    if batch.status == OpeningBatchStatus.CANCELLED.value:
        return batch
    if batch.status not in (OpeningBatchStatus.DRAFT.value, OpeningBatchStatus.SUBMITTED.value):
        raise OpeningBatchError(
            f"An opening batch can only be cancelled while DRAFT or SUBMITTED (is {batch.status})")
    before = batch.status
    batch.status = OpeningBatchStatus.CANCELLED.value
    batch.cancelled_by = cancelled_by
    batch.cancelled_at = _now()
    batch.cancel_reason = _clean(reason, 300)
    batch.updated_by = cancelled_by
    db.flush()
    _audit(db, cancelled_by, actor_name, "OPENING_BATCH_CANCEL", batch,
           {"before_status": before, "after_status": OpeningBatchStatus.CANCELLED.value,
            "reason": batch.cancel_reason})
    return batch
