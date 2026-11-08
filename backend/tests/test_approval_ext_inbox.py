"""Approvals extended to purchase orders, plus notifications and attachments."""
from __future__ import annotations

import base64
from datetime import date
from decimal import Decimal

from app.models.approvals import ApprovalRule, ApprovalStatus
from app.models.commercial import DocStatus
from app.models.core import User
from app.services import approval_exec, approvals, notifications
from app.services import order_lifecycle as ol


def _user(db, demo, username):
    u = User(company_id=demo["company_id"], username=username, email=f"{username}@demo.local",
             hashed_password="x", is_active=True)
    db.add(u); db.flush()
    return u


def _po(db, demo, qty="100", rate="1200"):
    return ol.create_purchase_order(
        db, company_id=demo["company_id"], supplier_id=demo["supplier_id"], number="PO-AP",
        order_date=date(2026, 11, 8),
        lines=[{"chemical_id": demo["chemicals"]["RB-01"], "qty": qty, "rate": rate}])


# ------------------------- PO approval via the engine -----------------------

def test_po_approval_confirms_on_approve(db, demo):
    db.add(ApprovalRule(company_id=demo["company_id"], doc_type="PURCHASE_ORDER",
                        min_amount=Decimal("50000"), approvals_required=1)); db.flush()
    maker = _user(db, demo, "po-maker"); checker = _user(db, demo, "po-checker")
    po = _po(db, demo)                                   # 100 * 1200 = 120,000 >= 50,000
    gross = sum(Decimal(str(l.qty)) * Decimal(str(l.rate)) for l in po.lines)
    rule = approvals.rule_for(db, demo["company_id"], "PURCHASE_ORDER", gross)
    assert rule is not None
    req = approvals.create_request(db, company_id=demo["company_id"], doc_type="PURCHASE_ORDER",
                                   doc_id=po.id, title="PO", amount=gross, requested_by=maker.id, rule=rule)
    db.commit()
    assert po.status == DocStatus.DRAFT                   # held, not yet confirmed

    out = approvals.approve(db, req, approver_id=checker.id)
    assert out["completed"] is True
    ref = approval_exec.execute_approved(db, req, created_by=checker.id)
    db.commit()
    assert ref == po.number
    db.refresh(po)
    assert po.status == DocStatus.CONFIRMED              # confirmed on approval


def test_po_below_threshold_needs_no_approval(db, demo):
    db.add(ApprovalRule(company_id=demo["company_id"], doc_type="PURCHASE_ORDER",
                        min_amount=Decimal("500000"), approvals_required=1)); db.flush()
    po = _po(db, demo)                                   # 120,000 < 500,000
    gross = sum(Decimal(str(l.qty)) * Decimal(str(l.rate)) for l in po.lines)
    assert approvals.rule_for(db, demo["company_id"], "PURCHASE_ORDER", gross) is None


# ------------------------------ notifications -------------------------------

def test_approval_request_creates_notification(db, demo):
    maker = _user(db, demo, "notif-maker")
    before = notifications.unread_count(db, demo["company_id"], maker.id)
    approvals.create_request(db, company_id=demo["company_id"], doc_type="PAYMENT", doc_id=None,
                             title="Payment to Acme", amount=Decimal("90000"), requested_by=maker.id)
    db.commit()
    after = notifications.unread_count(db, demo["company_id"], maker.id)
    assert after == before + 1                           # broadcast notification created
    rows = notifications.list_notifications(db, demo["company_id"], maker.id)
    assert rows[0].kind == "APPROVAL"
    notifications.mark_read(db, demo["company_id"], maker.id)
    db.commit()
    assert notifications.unread_count(db, demo["company_id"], maker.id) == 0


# ------------------------------- attachments --------------------------------

def test_attachment_upload_list_download(client, auth_headers, demo):
    import uuid as _u
    doc_id = str(_u.uuid4())
    payload = {"doc_type": "PO", "doc_id": doc_id, "filename": "spec.txt",
               "content_type": "text/plain",
               "data_base64": base64.b64encode(b"hello fabric").decode()}
    up = client.post("/api/attachments", json=payload, headers=auth_headers)
    assert up.status_code == 201 and up.json()["size"] == 12
    att_id = up.json()["id"]

    lst = client.get(f"/api/attachments?doc_type=PO&doc_id={doc_id}", headers=auth_headers).json()
    assert len(lst) == 1 and lst[0]["filename"] == "spec.txt"

    dl = client.get(f"/api/attachments/{att_id}/download", headers=auth_headers)
    assert dl.status_code == 200 and dl.content == b"hello fabric"


def test_attachment_rejects_bad_base64(client, auth_headers):
    import uuid as _u
    r = client.post("/api/attachments", json={"doc_type": "PO", "doc_id": str(_u.uuid4()),
                    "filename": "x", "data_base64": "not!base64!"}, headers=auth_headers)
    assert r.status_code == 422
