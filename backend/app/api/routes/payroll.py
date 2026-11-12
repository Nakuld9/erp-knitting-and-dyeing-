"""Payroll: employees, salary structures, monthly runs and payslips."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_company_db
from app.core.deps import require_permission
from app.models.core import Employee, User
from app.models.payroll import PayrollRun, Payslip, SalaryStructure
from app.services import payroll as svc

router = APIRouter(prefix="/api/payroll", tags=["payroll"])


# -------------------------------- employees ---------------------------------

class EmployeeIn(BaseModel):
    code: str
    name: str
    designation: str | None = None
    is_operator: bool = False


@router.post("/employees", status_code=status.HTTP_201_CREATED)
def create_employee(payload: EmployeeIn, db: Session = Depends(get_company_db),
                    user: User = Depends(require_permission("payroll.manage"))):
    emp = Employee(company_id=user.company_id, **payload.model_dump())
    db.add(emp)
    db.commit()
    return {"id": str(emp.id), "code": emp.code, "name": emp.name}


@router.get("/employees")
def list_employees(db: Session = Depends(get_company_db),
                   user: User = Depends(require_permission("payroll.manage"))):
    rows = db.execute(select(Employee).where(Employee.company_id == user.company_id,
                                             Employee.is_active.is_(True))
                      .order_by(Employee.code)).scalars().all()
    return [{"id": str(e.id), "code": e.code, "name": f"{e.code} — {e.name}",
             "designation": e.designation} for e in rows]


# ----------------------------- salary structures ----------------------------

class StructureIn(BaseModel):
    employee_id: uuid.UUID
    effective_from: date
    basic: Decimal
    hra: Decimal = Decimal("0")
    conveyance: Decimal = Decimal("0")
    other_allowances: Decimal = Decimal("0")
    pf_applicable: bool = True
    pf_employee_pct: Decimal = Decimal("12")
    pf_employer_pct: Decimal = Decimal("12")
    pf_wage_ceiling: Decimal = Decimal("15000")
    esi_applicable: bool = False
    esi_employee_pct: Decimal = Decimal("0.75")
    esi_employer_pct: Decimal = Decimal("3.25")
    professional_tax: Decimal = Decimal("0")
    tds_monthly: Decimal = Decimal("0")


@router.post("/salary-structures", status_code=status.HTTP_201_CREATED)
def upsert_structure(payload: StructureIn, db: Session = Depends(get_company_db),
                     user: User = Depends(require_permission("payroll.manage"))):
    data = payload.model_dump()
    emp_id = data.pop("employee_id")
    eff = data.pop("effective_from")
    s = svc.upsert_salary_structure(db, company_id=user.company_id, employee_id=emp_id,
                                    effective_from=eff, created_by=user.id, **data)
    db.commit()
    preview = svc.compute_payslip(s)
    return {"id": str(s.id), "gross": str(preview["gross"]), "net_pay": str(preview["net_pay"]),
            "total_deductions": str(preview["total_deductions"])}


@router.get("/salary-structures")
def list_structures(db: Session = Depends(get_company_db),
                    user: User = Depends(require_permission("payroll.manage"))):
    rows = db.execute(select(SalaryStructure).where(
        SalaryStructure.company_id == user.company_id, SalaryStructure.is_active.is_(True))).scalars().all()
    out = []
    for s in rows:
        emp = db.get(Employee, s.employee_id)
        p = svc.compute_payslip(s)
        out.append({"id": str(s.id), "employee": emp.name if emp else None,
                    "employee_code": emp.code if emp else None,
                    "basic": str(s.basic), "gross": str(p["gross"]),
                    "deductions": str(p["total_deductions"]), "net_pay": str(p["net_pay"])})
    return out


# -------------------------------- payroll run -------------------------------

class RunIn(BaseModel):
    period_month: date


@router.post("/runs", status_code=status.HTTP_201_CREATED)
def run_payroll(payload: RunIn, db: Session = Depends(get_company_db),
                user: User = Depends(require_permission("payroll.manage"))):
    try:
        res = svc.run_payroll(db, company_id=user.company_id, period_month=payload.period_month,
                              created_by=user.id)
        db.commit()
    except svc.PayrollError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    voucher = res.get("voucher")
    return {"run_id": res.get("run_id"), "period": res["period"],
            "already_posted": res.get("already_posted", False),
            "employees": res.get("employees", 0),
            "total_gross": str(res.get("total_gross", "")), "total_net": str(res.get("total_net", "")),
            "voucher_number": voucher.number if voucher else None}


@router.get("/runs")
def list_runs(db: Session = Depends(get_company_db),
              user: User = Depends(require_permission("payroll.manage"))):
    rows = db.execute(select(PayrollRun).where(PayrollRun.company_id == user.company_id)
                      .order_by(PayrollRun.period_month.desc())).scalars().all()
    return [{"id": str(r.id), "period": r.period_month.isoformat(), "status": r.status,
             "employees": len(r.payslips), "total_gross": str(r.total_gross),
             "total_net": str(r.total_net)} for r in rows[:200]]


@router.get("/runs/{run_id}/payslips")
def run_payslips(run_id: uuid.UUID, db: Session = Depends(get_company_db),
                 user: User = Depends(require_permission("payroll.manage"))):
    run = db.get(PayrollRun, run_id)
    if run is None or run.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payroll run not found")
    out = []
    for p in run.payslips:
        emp = db.get(Employee, p.employee_id)
        out.append({"employee": emp.name if emp else None, "employee_code": emp.code if emp else None,
                    "gross": str(p.gross), "pf_employee": str(p.pf_employee),
                    "esi_employee": str(p.esi_employee), "professional_tax": str(p.professional_tax),
                    "tds": str(p.tds), "total_deductions": str(p.total_deductions),
                    "net_pay": str(p.net_pay)})
    return out
