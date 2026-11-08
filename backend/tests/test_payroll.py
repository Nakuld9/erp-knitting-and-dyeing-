"""Payroll: payslip computation and a balanced monthly run.

Earnings minus employee PF/ESI/PT/TDS give net pay; the run posts one balanced
PRV voucher (salary + employer contributions debited; net + statutory payables
credited) and is idempotent on the pay period.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.core import Employee
from app.services import payroll as svc


def _employee(db, company, code="EMP-1"):
    e = Employee(company_id=company.id, code=code, name=f"Worker {code}")
    db.add(e); db.flush()
    return e


def _structure(db, company, emp, **kw):
    defaults = dict(effective_from=date(2026, 11, 1), basic="20000", hra="8000",
                    other_allowances="2000", pf_applicable=True, esi_applicable=False,
                    professional_tax="200", tds_monthly="1000")
    defaults.update(kw)
    s = svc.upsert_salary_structure(db, company_id=company.id, employee_id=emp.id, **defaults)
    db.commit()
    return s


def test_payslip_computation(db, company):
    emp = _employee(db, company)
    s = _structure(db, company, emp)
    p = svc.compute_payslip(s)
    # gross = 20000 + 8000 + 2000 = 30000; PF 12% of basic 20000 (<=ceiling 15000 -> 1800)
    assert p["gross"] == Decimal("30000.00")
    assert p["pf_employee"] == Decimal("1800.00")        # 12% of min(20000,15000)=15000
    assert p["pf_employer"] == Decimal("1800.00")
    assert p["esi_employee"] == Decimal("0.00")          # not applicable
    # deductions = 1800 + 0 + 200 + 1000 = 3000; net = 27000
    assert p["total_deductions"] == Decimal("3000.00")
    assert p["net_pay"] == Decimal("27000.00")


def test_esi_applies_when_flagged(db, company):
    emp = _employee(db, company, "EMP-ESI")
    s = _structure(db, company, emp, basic="10000", hra="4000", other_allowances="1000",
                   esi_applicable=True, professional_tax="200", tds_monthly="0")
    p = svc.compute_payslip(s)
    # gross 15000; ESI employee 0.75% = 112.50; PF 12% of 10000 = 1200
    assert p["gross"] == Decimal("15000.00")
    assert p["esi_employee"] == Decimal("112.50")
    assert p["esi_employer"] == Decimal("487.50")        # 3.25%
    assert p["pf_employee"] == Decimal("1200.00")


def test_payroll_run_posts_balanced_voucher(db, company):
    e1 = _employee(db, company, "EMP-1"); _structure(db, company, e1)
    e2 = _employee(db, company, "EMP-2")
    _structure(db, company, e2, basic="15000", hra="5000", other_allowances="0",
               professional_tax="200", tds_monthly="0")
    res = svc.run_payroll(db, company_id=company.id, period_month=date(2026, 11, 30))
    db.commit()
    assert res["employees"] == 2
    v = res["voucher"]
    assert v.total_debit == v.total_credit               # balanced
    # total gross = 30000 + 20000 = 50000 -> salary expense + employer PF debited
    assert res["total_gross"] == Decimal("50000.00")
    assert v.total_debit > res["total_gross"]            # includes employer contributions


def test_payroll_run_is_idempotent(db, company):
    e = _employee(db, company); _structure(db, company, e)
    r1 = svc.run_payroll(db, company_id=company.id, period_month=date(2026, 11, 10)); db.commit()
    r2 = svc.run_payroll(db, company_id=company.id, period_month=date(2026, 11, 25)); db.commit()
    assert r2["already_posted"] is True
    assert r1["voucher"].id == r2["voucher"].id


def test_structure_upsert_replaces_active(db, company):
    emp = _employee(db, company)
    _structure(db, company, emp, basic="20000")
    _structure(db, company, emp, basic="25000")          # second upsert
    from sqlalchemy import select
    from app.models.payroll import SalaryStructure
    active = db.execute(select(SalaryStructure).where(
        SalaryStructure.employee_id == emp.id, SalaryStructure.is_active.is_(True))).scalars().all()
    assert len(active) == 1 and Decimal(str(active[0].basic)) == Decimal("25000")


def test_payroll_api_roundtrip(client, auth_headers):
    emp = client.post("/api/payroll/employees", json={"code": "API-E1", "name": "API Worker"},
                      headers=auth_headers)
    assert emp.status_code == 201, emp.text
    emp_id = emp.json()["id"]

    st = client.post("/api/payroll/salary-structures", json={
        "employee_id": emp_id, "effective_from": "2026-11-01", "basic": "20000",
        "hra": "8000", "other_allowances": "2000", "professional_tax": "200", "tds_monthly": "1000"},
        headers=auth_headers)
    assert st.status_code == 201 and st.json()["net_pay"] == "27000.00"

    run = client.post("/api/payroll/runs", json={"period_month": "2026-11-30"}, headers=auth_headers)
    assert run.status_code == 201, run.text
    assert run.json()["employees"] >= 1 and Decimal(run.json()["total_net"]) >= Decimal("27000.00")

    slips = client.get(f"/api/payroll/runs/{run.json()['run_id']}/payslips", headers=auth_headers)
    assert slips.status_code == 200
    assert any(sl["net_pay"] == "27000.00" for sl in slips.json())
