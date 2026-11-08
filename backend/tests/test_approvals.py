"""Maker-checker approvals, demonstrated on supplier payments.

A payment below the rule threshold posts at once; at/above it is held until the
required approvals arrive. The requester can never approve their own request,
and only an APPROVED request posts the payment voucher.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.models.approvals import (
    ApprovalRule,
    ApprovalStatus,
    PaymentApproval,
    PaymentStatus,
)
from app.models.core import User
from app.services import approvals, payments


def _user(db, company, username):
    u = User(company_id=company["company_id"], username=username, email=f"{username}@demo.local",
             hashed_password="x", is_active=True)
    db.add(u); db.flush()
    return u


def _rule(db, company, *, min_amount="100000", approvals_required=1):
    r = ApprovalRule(company_id=company["company_id"], doc_type="PAYMENT",
                     min_amount=Decimal(min_amount), approvals_required=approvals_required)
    db.add(r); db.flush()
    return r


def _request(db, demo, maker, amount):
    return payments.request_payment(
        db, company_id=demo["company_id"], supplier_id=demo["supplier_id"],
        bank_account_id=demo["bank_account_id"], amount=Decimal(amount), requested_by=maker.id)


def test_below_threshold_pays_immediately(db, demo):
    _rule(db, demo, min_amount="100000")
    maker = _user(db, demo, "maker1")
    res = _request(db, demo, maker, "50000")
    db.commit()
    assert res["approval_required"] is False and res["status"] == "PAID"
    assert res["voucher_number"]


def test_above_threshold_holds_for_approval(db, demo):
    _rule(db, demo, min_amount="100000")
    maker = _user(db, demo, "maker2")
    res = _request(db, demo, maker, "150000")
    db.commit()
    assert res["approval_required"] is True
    pay = db.get(PaymentApproval, __import__("uuid").UUID(res["payment_id"]))
    assert pay.status == PaymentStatus.PENDING and pay.posting_reference is None


def test_approval_posts_payment(db, demo):
    _rule(db, demo, min_amount="100000")
    maker = _user(db, demo, "maker3"); checker = _user(db, demo, "checker3")
    res = _request(db, demo, maker, "150000")
    from app.models.approvals import ApprovalRequest
    req = db.get(ApprovalRequest, __import__("uuid").UUID(res["approval_request_id"]))
    out = approvals.approve(db, req, approver_id=checker.id)
    assert out["completed"] is True
    voucher = payments.post_approved_payment(db, req, created_by=checker.id)
    db.commit()
    assert voucher.total_debit == voucher.total_credit == Decimal("150000.00")
    pay = db.get(PaymentApproval, req.doc_id)
    assert pay.status == PaymentStatus.PAID


def test_self_approval_is_blocked(db, demo):
    _rule(db, demo, min_amount="100000")
    maker = _user(db, demo, "maker4")
    res = _request(db, demo, maker, "150000")
    from app.models.approvals import ApprovalRequest
    req = db.get(ApprovalRequest, __import__("uuid").UUID(res["approval_request_id"]))
    try:
        approvals.approve(db, req, approver_id=maker.id)
        assert False, "expected ApprovalError"
    except approvals.ApprovalError:
        db.rollback()


def test_reject_blocks_payment(db, demo):
    _rule(db, demo, min_amount="100000")
    maker = _user(db, demo, "maker5"); checker = _user(db, demo, "checker5")
    res = _request(db, demo, maker, "150000")
    from app.models.approvals import ApprovalRequest
    req = db.get(ApprovalRequest, __import__("uuid").UUID(res["approval_request_id"]))
    approvals.reject(db, req, approver_id=checker.id, note="Budget exceeded")
    payments.reject_payment(db, req)
    db.commit()
    assert req.status == ApprovalStatus.REJECTED
    pay = db.get(PaymentApproval, req.doc_id)
    assert pay.status == PaymentStatus.REJECTED and pay.posting_reference is None


def test_two_approvals_required(db, demo):
    _rule(db, demo, min_amount="100000", approvals_required=2)
    maker = _user(db, demo, "maker6")
    c1 = _user(db, demo, "checker6a"); c2 = _user(db, demo, "checker6b")
    res = _request(db, demo, maker, "200000")
    from app.models.approvals import ApprovalRequest
    req = db.get(ApprovalRequest, __import__("uuid").UUID(res["approval_request_id"]))
    out1 = approvals.approve(db, req, approver_id=c1.id)
    assert out1["completed"] is False and req.status == ApprovalStatus.PENDING
    out2 = approvals.approve(db, req, approver_id=c2.id)
    assert out2["completed"] is True and req.status == ApprovalStatus.APPROVED


def test_api_self_approval_blocked(client, auth_headers, db, demo):
    # admin creates a rule and requests a payment, then can't approve its own.
    client.post("/api/approvals/rules", json={"doc_type": "PAYMENT", "min_amount": "100000"},
                headers=auth_headers)
    r = client.post("/api/approvals/payments", json={
        "supplier_id": str(demo["supplier_id"]), "bank_account_id": str(demo["bank_account_id"]),
        "amount": "150000"}, headers=auth_headers)
    assert r.status_code == 201 and r.json()["approval_required"] is True
    rid = r.json()["approval_request_id"]
    ap = client.post(f"/api/approvals/requests/{rid}/approve", json={}, headers=auth_headers)
    assert ap.status_code == 422          # requester cannot approve own request
