"""Job work — inward (we earn) and outward (we pay), posted via the engine."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.jobwork import JobWork, JobWorkStatus
from app.services import jobwork as svc
from app.services.accounting import trial_balance as tb


def _tb_row(db, demo, code):
    rows = tb.trial_balance(db, demo["company_id"])["rows"]
    return next((r for r in rows if r["account_code"] == code), None)


# -------------------------------- inward ------------------------------------

def test_inward_job_work_bills_income_and_gst(db, demo):
    jw = svc.create(db, company_id=demo["company_id"], number="JW-IN-1", direction="INWARD",
                    process="DYEING", party_id=demo["customer_id"], order_date=date(2026, 11, 9),
                    qty_kg="1000", rate="25", tax_code_id=demo["tax_id"],
                    description="Dye 1000kg customer greige")
    db.commit()
    assert jw.status == JobWorkStatus.OPEN

    voucher = svc.bill(db, jw, created_by=None)
    db.commit()
    assert jw.status == JobWorkStatus.BILLED
    # 1000*25 = 25,000 charge; 18% GST = 4,500; gross 29,500
    assert jw.taxable_value == Decimal("25000.00")
    assert jw.tax_amount == Decimal("4500.00")
    assert jw.total_value == Decimal("29500.00")
    assert voucher.total_debit == voucher.total_credit == Decimal("29500.00")
    # Dyeing Job Work Income (4030) credited with the charge
    assert _tb_row(db, demo, "4030")["period_credit"] == Decimal("25000.00")
    # receivable (1040) debited with the gross
    assert _tb_row(db, demo, "1040")["period_debit"] == Decimal("29500.00")


# -------------------------------- outward -----------------------------------

def test_outward_job_work_books_cost_and_payable(db, demo):
    jw = svc.create(db, company_id=demo["company_id"], number="JW-OUT-1", direction="OUTWARD",
                    process="DYEING", party_id=demo["supplier_id"], order_date=date(2026, 11, 9),
                    qty_kg="1000", rate="20", tax_code_id=demo["tax_id"],
                    description="Send 1000kg to external dyer")
    db.commit()
    svc.dispatch(db, jw); db.commit()
    assert jw.status == JobWorkStatus.SENT

    voucher = svc.bill(db, jw, created_by=None)
    db.commit()
    assert jw.status == JobWorkStatus.BILLED
    # 1000*20 = 20,000 charge; 18% = 3,600; gross 23,600
    assert jw.total_value == Decimal("23600.00")
    assert voucher.total_debit == voucher.total_credit == Decimal("23600.00")
    # Processing Charges (5090) debited with the charge; AP (2010) credited with gross
    assert _tb_row(db, demo, "5090")["period_debit"] == Decimal("20000.00")
    assert _tb_row(db, demo, "2010")["period_credit"] == Decimal("23600.00")


# --------------------------------- guards -----------------------------------

def test_cannot_dispatch_inward(db, demo):
    jw = svc.create(db, company_id=demo["company_id"], number="JW-IN-2", direction="INWARD",
                    process="DYEING", party_id=demo["customer_id"], order_date=date(2026, 11, 9),
                    qty_kg="100", rate="25")
    with pytest.raises(svc.JobWorkError):
        svc.dispatch(db, jw)


def test_zero_qty_rejected(db, demo):
    with pytest.raises(svc.JobWorkError):
        svc.create(db, company_id=demo["company_id"], number="JW-BAD", direction="INWARD",
                   process="DYEING", party_id=demo["customer_id"], order_date=date(2026, 11, 9),
                   qty_kg="0", rate="25")


def test_bill_is_idempotent(db, demo):
    jw = svc.create(db, company_id=demo["company_id"], number="JW-IN-3", direction="INWARD",
                    process="FINISHING", party_id=demo["customer_id"], order_date=date(2026, 11, 9),
                    qty_kg="500", rate="10", tax_code_id=demo["tax_id"])
    db.commit()
    v1 = svc.bill(db, jw); db.commit()
    v2 = svc.bill(db, jw); db.commit()
    assert v1.id == v2.id


# ---------------------------------- API -------------------------------------

def test_jobwork_api_inward_create_and_bill(client, auth_headers, demo):
    r = client.post("/api/jobwork", headers=auth_headers, json={
        "number": "JW-API-1", "direction": "INWARD", "process": "DYEING",
        "party_id": str(demo["customer_id"]), "order_date": "2026-11-09",
        "qty_kg": "800", "rate": "30", "tax_code_id": str(demo["tax_id"])})
    assert r.status_code == 201, r.text
    jw_id = r.json()["id"]
    b = client.post(f"/api/jobwork/{jw_id}/bill", headers=auth_headers)
    assert b.status_code == 200 and b.json()["status"] == "BILLED"
    assert b.json()["voucher_number"]
