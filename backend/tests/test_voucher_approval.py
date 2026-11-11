"""Journal / voucher routing through the maker-checker approval workflow."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from app.models.approvals import ApprovalRule, PendingVoucher, PendingVoucherStatus
from app.models.core import User
from app.services import approval_exec, approvals, voucher_approvals as va


def _user(db, demo, username):
    u = User(company_id=demo["company_id"], username=username, email=f"{username}@demo.local",
             hashed_password="x", is_active=True)
    db.add(u); db.flush()
    return u


def _lines(amount="90000"):
    # Dr Office Expenses (6020) / Cr Cash in Hand (1010)
    return [{"account_code": "6020", "debit": amount, "credit": "0"},
            {"account_code": "1010", "debit": "0", "credit": amount}]


def _rule(db, demo, min_amount="50000"):
    db.add(ApprovalRule(company_id=demo["company_id"], doc_type="VOUCHER",
                        min_amount=Decimal(min_amount), approvals_required=1)); db.flush()


# --------------------------- holds above threshold --------------------------

def test_voucher_above_threshold_is_held_then_posts_on_approval(db, demo):
    _rule(db, demo)
    maker = _user(db, demo, "jv-maker"); checker = _user(db, demo, "jv-checker")
    res = va.submit_voucher(db, company_id=demo["company_id"], voucher_type_code="JV",
                            voucher_date=date(2026, 11, 8), lines=_lines("90000"),
                            narration="Big manual JV", requested_by=maker.id)
    db.commit()
    assert res["approval_required"] is True
    pv = db.get(PendingVoucher, uuid.UUID(res["pending_voucher_id"]))
    assert pv.status == PendingVoucherStatus.PENDING       # not posted yet

    req = db.get(approvals.ApprovalRequest, uuid.UUID(res["approval_request_id"]))
    approvals.approve(db, req, approver_id=checker.id)
    ref = approval_exec.execute_approved(db, req, created_by=checker.id)
    db.commit()
    db.refresh(pv)
    assert pv.status == PendingVoucherStatus.POSTED
    assert ref                                             # a voucher number came back
    assert pv.posting_reference == f"VCHAP:{pv.id}"
    assert pv.posted_voucher_id is not None


# --------------------------- posts below threshold --------------------------

def test_voucher_below_threshold_posts_immediately(db, demo):
    _rule(db, demo, min_amount="500000")
    maker = _user(db, demo, "jv-maker2")
    res = va.submit_voucher(db, company_id=demo["company_id"], voucher_type_code="JV",
                            voucher_date=date(2026, 11, 8), lines=_lines("90000"),
                            requested_by=maker.id)
    db.commit()
    assert res["approval_required"] is False
    assert res["voucher_number"]
    pv = db.get(PendingVoucher, uuid.UUID(res["pending_voucher_id"]))
    assert pv.status == PendingVoucherStatus.POSTED


# ----------------------------- guards at submit -----------------------------

def test_unbalanced_voucher_is_rejected_at_submit(db, demo):
    _rule(db, demo)
    maker = _user(db, demo, "jv-maker3")
    bad = [{"account_code": "6020", "debit": "90000", "credit": "0"},
           {"account_code": "1010", "debit": "0", "credit": "80000"}]
    try:
        va.submit_voucher(db, company_id=demo["company_id"], voucher_type_code="JV",
                          voucher_date=date(2026, 11, 8), lines=bad, requested_by=maker.id)
        assert False, "expected VoucherApprovalError"
    except va.VoucherApprovalError as exc:
        assert "unbalanced" in str(exc).lower()


def test_reject_leaves_voucher_unposted(db, demo):
    _rule(db, demo)
    maker = _user(db, demo, "jv-maker4"); checker = _user(db, demo, "jv-checker4")
    res = va.submit_voucher(db, company_id=demo["company_id"], voucher_type_code="JV",
                            voucher_date=date(2026, 11, 8), lines=_lines("90000"),
                            requested_by=maker.id)
    db.commit()
    req = db.get(approvals.ApprovalRequest, uuid.UUID(res["approval_request_id"]))
    approvals.reject(db, req, approver_id=checker.id)
    approval_exec.on_rejected(db, req)
    db.commit()
    pv = db.get(PendingVoucher, uuid.UUID(res["pending_voucher_id"]))
    assert pv.status == PendingVoucherStatus.REJECTED


# --------------------------------- via API ----------------------------------

def test_voucher_submit_approval_via_api(client, auth_headers, db, demo):
    _rule(db, demo); db.commit()
    body = {"voucher_type_code": "JV", "voucher_date": "2026-11-08", "posting_reference": "JV-API-1",
            "narration": "API JV", "lines": [
                {"account_code": "6020", "debit": "75000", "credit": "0"},
                {"account_code": "1010", "debit": "0", "credit": "75000"}]}
    r = client.post("/api/vouchers/submit-approval", json=body, headers=auth_headers)
    assert r.status_code == 201, r.text
    assert r.json()["approval_required"] is True
