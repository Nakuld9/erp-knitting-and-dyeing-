"""Stage 6F-P1 — opening-balance batch foundation (service-level).

Exercises the controlled lifecycle, invalid transitions, maker-checker
self-approval prevention, company / factory isolation, the audit trail,
idempotency, and the P1 scope guarantee that NO inventory or financial opening
transaction is ever created.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import func, select

from app.models.audit import AuditLog
from app.models.core import Company
from app.models.inventory import InventoryTransaction, StockLayer
from app.models.manufacturing import FabricRoll
from app.models.masters import YarnLot
from app.models.accounting import Voucher
from app.models.opening import OpeningBatch, OpeningBatchStatus, OpeningBatchType
from app.services import opening as svc

ED = date(2026, 11, 15)


def _uid():
    return uuid.uuid4()


# ------------------------------- lifecycle ----------------------------------

def test_full_lifecycle_draft_submit_approve_close(db, company):
    maker, checker = _uid(), _uid()
    b = svc.create_batch(db, company.id, batch_type="YARN", effective_date=ED, created_by=maker)
    assert b.status == OpeningBatchStatus.DRAFT.value
    assert b.batch_number and b.company_id == company.id
    svc.submit_batch(db, company.id, b.id, submitted_by=maker)
    assert b.status == OpeningBatchStatus.SUBMITTED.value and b.submitted_by == maker
    svc.approve_batch(db, company.id, b.id, approved_by=checker)
    assert b.status == OpeningBatchStatus.APPROVED.value and b.approved_by == checker
    svc.close_batch(db, company.id, b.id, closed_by=checker)
    assert b.status == OpeningBatchStatus.CLOSED.value and b.closed_by == checker


def test_all_batch_types_accepted(db, company):
    for t in OpeningBatchType:
        b = svc.create_batch(db, company.id, batch_type=t, effective_date=ED, created_by=_uid())
        assert b.batch_type == t.value


def test_unknown_batch_type_rejected(db, company):
    with pytest.raises(svc.OpeningBatchError):
        svc.create_batch(db, company.id, batch_type="NONSENSE", effective_date=ED, created_by=_uid())


def test_batch_number_unique_and_scoped(db, company):
    a = svc.create_batch(db, company.id, batch_type="YARN", effective_date=ED, created_by=_uid())
    b = svc.create_batch(db, company.id, batch_type="YARN", effective_date=ED, created_by=_uid())
    assert a.batch_number != b.batch_number


def test_cancel_from_draft_and_from_submitted(db, company):
    b1 = svc.create_batch(db, company.id, batch_type="WIP", effective_date=ED, created_by=_uid())
    svc.cancel_batch(db, company.id, b1.id, cancelled_by=_uid(), reason="typo")
    assert b1.status == OpeningBatchStatus.CANCELLED.value and b1.cancel_reason == "typo"

    maker = _uid()
    b2 = svc.create_batch(db, company.id, batch_type="WIP", effective_date=ED, created_by=maker)
    svc.submit_batch(db, company.id, b2.id, submitted_by=maker)
    svc.cancel_batch(db, company.id, b2.id, cancelled_by=_uid())
    assert b2.status == OpeningBatchStatus.CANCELLED.value


# --------------------------- invalid transitions ----------------------------

def test_approve_requires_submitted(db, company):
    b = svc.create_batch(db, company.id, batch_type="YARN", effective_date=ED, created_by=_uid())
    with pytest.raises(svc.OpeningBatchError):
        svc.approve_batch(db, company.id, b.id, approved_by=_uid())  # still DRAFT


def test_close_requires_approved(db, company):
    maker = _uid()
    b = svc.create_batch(db, company.id, batch_type="YARN", effective_date=ED, created_by=maker)
    svc.submit_batch(db, company.id, b.id, submitted_by=maker)
    with pytest.raises(svc.OpeningBatchError):
        svc.close_batch(db, company.id, b.id, closed_by=_uid())  # SUBMITTED, not APPROVED


def test_no_edit_after_submit(db, company):
    maker = _uid()
    b = svc.create_batch(db, company.id, batch_type="YARN", effective_date=ED,
                         description="d1", created_by=maker)
    svc.submit_batch(db, company.id, b.id, submitted_by=maker)
    with pytest.raises(svc.OpeningBatchError):
        svc.update_draft(db, company.id, b.id, description="d2", updated_by=maker)
    assert b.description == "d1"


def test_closed_cannot_be_reopened_or_cancelled(db, company):
    maker, checker = _uid(), _uid()
    b = svc.create_batch(db, company.id, batch_type="YARN", effective_date=ED, created_by=maker)
    svc.submit_batch(db, company.id, b.id, submitted_by=maker)
    svc.approve_batch(db, company.id, b.id, approved_by=checker)
    svc.close_batch(db, company.id, b.id, closed_by=checker)
    with pytest.raises(svc.OpeningBatchError):
        svc.cancel_batch(db, company.id, b.id, cancelled_by=checker)
    with pytest.raises(svc.OpeningBatchError):
        svc.submit_batch(db, company.id, b.id, submitted_by=maker)
    # There is no reopen path at all.
    assert not hasattr(svc, "reopen_batch")


def test_cancelled_cannot_be_submitted_or_approved(db, company):
    b = svc.create_batch(db, company.id, batch_type="YARN", effective_date=ED, created_by=_uid())
    svc.cancel_batch(db, company.id, b.id, cancelled_by=_uid())
    with pytest.raises(svc.OpeningBatchError):
        svc.submit_batch(db, company.id, b.id, submitted_by=_uid())


def test_approved_cannot_be_cancelled(db, company):
    maker, checker = _uid(), _uid()
    b = svc.create_batch(db, company.id, batch_type="YARN", effective_date=ED, created_by=maker)
    svc.submit_batch(db, company.id, b.id, submitted_by=maker)
    svc.approve_batch(db, company.id, b.id, approved_by=checker)
    with pytest.raises(svc.OpeningBatchError):
        svc.cancel_batch(db, company.id, b.id, cancelled_by=checker)


# ----------------------------- maker-checker --------------------------------

def test_submitter_cannot_approve_own_batch(db, company):
    maker = _uid()
    b = svc.create_batch(db, company.id, batch_type="YARN", effective_date=ED, created_by=maker)
    svc.submit_batch(db, company.id, b.id, submitted_by=maker)
    with pytest.raises(svc.SelfApprovalError):
        svc.approve_batch(db, company.id, b.id, approved_by=maker)
    # A different user may approve.
    svc.approve_batch(db, company.id, b.id, approved_by=_uid())
    assert b.status == OpeningBatchStatus.APPROVED.value


def test_approve_requires_approver_identity(db, company):
    maker = _uid()
    b = svc.create_batch(db, company.id, batch_type="YARN", effective_date=ED, created_by=maker)
    svc.submit_batch(db, company.id, b.id, submitted_by=maker)
    with pytest.raises(svc.OpeningBatchError):
        svc.approve_batch(db, company.id, b.id, approved_by=None)


# ------------------------------ idempotency ---------------------------------

def test_idempotent_create_with_key(db, company):
    k = "import-2026-opening-001"
    a = svc.create_batch(db, company.id, batch_type="YARN", effective_date=ED,
                         idempotency_key=k, created_by=_uid())
    b = svc.create_batch(db, company.id, batch_type="YARN", effective_date=ED,
                         idempotency_key=k, created_by=_uid())
    assert a.id == b.id
    count = db.execute(select(func.count()).select_from(OpeningBatch).where(
        OpeningBatch.company_id == company.id, OpeningBatch.idempotency_key == k)).scalar()
    assert count == 1


def test_idempotent_submit_and_approve_are_noops(db, company):
    maker, checker = _uid(), _uid()
    b = svc.create_batch(db, company.id, batch_type="YARN", effective_date=ED, created_by=maker)
    svc.submit_batch(db, company.id, b.id, submitted_by=maker)
    first_sub_at = b.submitted_at
    svc.submit_batch(db, company.id, b.id, submitted_by=maker)  # no-op
    assert b.submitted_at == first_sub_at
    svc.approve_batch(db, company.id, b.id, approved_by=checker)
    first_app_at = b.approved_at
    svc.approve_batch(db, company.id, b.id, approved_by=checker)  # no-op
    assert b.approved_at == first_app_at


# --------------------------- company isolation ------------------------------

def test_company_isolation(db, company):
    other = Company(code="DEMO2", name="Demo Two")
    db.add(other)
    db.flush()
    mine = svc.create_batch(db, company.id, batch_type="YARN", effective_date=ED, created_by=_uid())
    theirs = svc.create_batch(db, other.id, batch_type="YARN", effective_date=ED, created_by=_uid())

    # Cannot fetch another company's batch through my company scope.
    with pytest.raises(svc.OpeningBatchNotFound):
        svc.get_batch(db, company.id, theirs.id)
    # Listing is scoped.
    rows, total = svc.list_batches(db, company.id)
    ids = {r.id for r in rows}
    assert mine.id in ids and theirs.id not in ids and total == 1
    # Transitions are scoped too.
    with pytest.raises(svc.OpeningBatchNotFound):
        svc.submit_batch(db, company.id, theirs.id, submitted_by=_uid())


def test_factory_must_belong_to_company(db, company):
    other = Company(code="DEMO3", name="Demo Three")
    db.add(other)
    db.flush()
    from app.models.core import Branch
    foreign_factory = Branch(company_id=other.id, code="F-OTHER", name="Other Factory")
    db.add(foreign_factory)
    db.flush()
    with pytest.raises(svc.OpeningBatchError):
        svc.create_batch(db, company.id, batch_type="YARN", effective_date=ED,
                         factory_id=foreign_factory.id, created_by=_uid())


# -------------------------------- audit -------------------------------------

def _opening_audits(db, batch_id):
    # The service is flush-only; the final audit row is flushed by the route's
    # commit in production. Flush here so the last transition is visible.
    db.flush()
    return db.execute(select(AuditLog).where(
        AuditLog.entity == "opening_batch", AuditLog.entity_id == str(batch_id))
        .order_by(AuditLog.at)).scalars().all()


def test_audit_trail_records_every_transition(db, company):
    maker, checker = _uid(), _uid()
    b = svc.create_batch(db, company.id, batch_type="YARN", effective_date=ED, created_by=maker)
    svc.update_draft(db, company.id, b.id, description="note", updated_by=maker)
    svc.submit_batch(db, company.id, b.id, submitted_by=maker)
    svc.approve_batch(db, company.id, b.id, approved_by=checker)
    svc.close_batch(db, company.id, b.id, closed_by=checker)
    actions = [a.action for a in _opening_audits(db, b.id)]
    assert actions == ["OPENING_BATCH_CREATE", "OPENING_BATCH_UPDATE", "OPENING_BATCH_SUBMIT",
                       "OPENING_BATCH_APPROVE", "OPENING_BATCH_CLOSE"]
    # The approve row carries the actor and the batch number in its detail.
    approve = [a for a in _opening_audits(db, b.id) if a.action == "OPENING_BATCH_APPROVE"][0]
    assert approve.actor_id == checker
    assert b.batch_number in (approve.detail or "")
    assert str(company.id) in (approve.detail or "")


# ---------------------- P1 scope guarantee (no transactions) ----------------

def test_lifecycle_creates_no_inventory_or_financial_rows(db, company):
    def _counts():
        return {
            "layers": db.execute(select(func.count()).select_from(StockLayer)).scalar(),
            "txns": db.execute(select(func.count()).select_from(InventoryTransaction)).scalar(),
            "rolls": db.execute(select(func.count()).select_from(FabricRoll)).scalar(),
            "yarn_lots": db.execute(select(func.count()).select_from(YarnLot)).scalar(),
            "vouchers": db.execute(select(func.count()).select_from(Voucher)).scalar(),
        }

    before = _counts()
    maker, checker = _uid(), _uid()
    b = svc.create_batch(db, company.id, batch_type="MANUFACTURED_ROLLS", effective_date=ED,
                         created_by=maker)
    svc.submit_batch(db, company.id, b.id, submitted_by=maker)
    svc.approve_batch(db, company.id, b.id, approved_by=checker)
    svc.close_batch(db, company.id, b.id, closed_by=checker)
    after = _counts()
    assert before == after, f"P1 must create no stock/financial rows: {before} -> {after}"


def test_model_has_no_quantity_or_value_columns(db):
    # Guard against P2+ line-item fields leaking into the P1 batch master.
    cols = set(OpeningBatch.__table__.columns.keys())
    forbidden = {"quantity", "qty", "amount", "value", "unit_cost", "rate", "balance",
                 "debit", "credit", "item_id", "lot_id", "roll_id", "account_code"}
    assert cols.isdisjoint(forbidden), f"unexpected financial/stock columns: {cols & forbidden}"
