"""Payroll computation and posting.

Per employee each month:
  gross        = basic + hra + conveyance + other allowances
  pf_employee  = min(basic, pf_wage_ceiling) * pf_employee_pct%   (if applicable)
  pf_employer  = min(basic, pf_wage_ceiling) * pf_employer_pct%
  esi_employee = gross * esi_employee_pct%                        (if applicable)
  esi_employer = gross * esi_employer_pct%
  deductions   = pf_employee + esi_employee + professional_tax + tds
  net_pay      = gross - deductions

A run posts one balanced PRV voucher:
  Dr Salaries (gross) + Dr Employer PF + Dr Employer ESI
  Cr Salary Payable (net) + Cr PF/ESI/PT/TDS Payable (statutory)
and is idempotent on the pay period.
"""
from __future__ import annotations

import calendar
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.core import Employee
from app.models.payroll import PayrollRun, PayrollStatus, Payslip, SalaryStructure
from app.services.accounting import posting_engine as pe
from app.services.accounting.posting_rules import resolve_account

MZERO = quantize(0, MONEY_SCALE)


class PayrollError(Exception):
    """A payroll operation was invalid."""


def _D(v) -> Decimal:
    return Decimal(str(v or 0))


def _first_of_month(d: date) -> date:
    return d.replace(day=1)


def _end_of_month(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def upsert_salary_structure(db: Session, *, company_id: uuid.UUID, employee_id: uuid.UUID,
                            effective_from: date, created_by: uuid.UUID | None = None, **fields) -> SalaryStructure:
    """Create or replace the active salary structure for an employee."""
    existing = db.execute(
        select(SalaryStructure).where(
            SalaryStructure.company_id == company_id,
            SalaryStructure.employee_id == employee_id,
            SalaryStructure.is_active.is_(True),
        )
    ).scalars().all()
    for row in existing:
        row.is_active = False
    s = SalaryStructure(company_id=company_id, employee_id=employee_id,
                        effective_from=effective_from, is_active=True, created_by=created_by, **fields)
    db.add(s)
    db.flush()
    return s


def compute_payslip(structure: SalaryStructure) -> dict:
    basic = _D(structure.basic)
    hra = _D(structure.hra)
    allowances = _D(structure.conveyance) + _D(structure.other_allowances)
    gross = quantize(basic + hra + allowances, MONEY_SCALE)

    pf_employee = pf_employer = MZERO
    if structure.pf_applicable:
        pf_base = min(basic, _D(structure.pf_wage_ceiling)) if _D(structure.pf_wage_ceiling) > 0 else basic
        pf_employee = quantize(pf_base * _D(structure.pf_employee_pct) / Decimal("100"), MONEY_SCALE)
        pf_employer = quantize(pf_base * _D(structure.pf_employer_pct) / Decimal("100"), MONEY_SCALE)

    esi_employee = esi_employer = MZERO
    if structure.esi_applicable:
        esi_employee = quantize(gross * _D(structure.esi_employee_pct) / Decimal("100"), MONEY_SCALE)
        esi_employer = quantize(gross * _D(structure.esi_employer_pct) / Decimal("100"), MONEY_SCALE)

    pt = quantize(_D(structure.professional_tax), MONEY_SCALE)
    tds = quantize(_D(structure.tds_monthly), MONEY_SCALE)
    deductions = quantize(pf_employee + esi_employee + pt + tds, MONEY_SCALE)
    net = quantize(gross - deductions, MONEY_SCALE)

    return {"basic": basic, "hra": hra, "allowances": allowances, "gross": gross,
            "pf_employee": pf_employee, "pf_employer": pf_employer,
            "esi_employee": esi_employee, "esi_employer": esi_employer,
            "professional_tax": pt, "tds": tds, "total_deductions": deductions, "net_pay": net}


def run_payroll(db: Session, *, company_id: uuid.UUID, period_month: date,
                run_date: date | None = None, created_by: uuid.UUID | None = None) -> dict:
    period = _first_of_month(period_month)
    run_date = run_date or _end_of_month(period)
    ref = f"PAY:{company_id}:{period:%Y-%m}"
    existing = pe.find_existing(db, company_id, ref)
    if existing is not None:
        return {"already_posted": True, "voucher": existing, "period": period.isoformat()}

    structures = db.execute(
        select(SalaryStructure).where(
            SalaryStructure.company_id == company_id, SalaryStructure.is_active.is_(True),
            SalaryStructure.effective_from <= _end_of_month(period),
        )
    ).scalars().all()
    # Only pay active employees.
    structures = [s for s in structures
                  if (emp := db.get(Employee, s.employee_id)) is not None and emp.is_active]
    if not structures:
        raise PayrollError("No active salary structures to pay for this period")

    run = PayrollRun(company_id=company_id, period_month=period, run_date=run_date,
                     status=PayrollStatus.DRAFT, created_by=created_by)
    db.add(run)
    db.flush()

    acc = lambda slot: resolve_account(db, company_id, "PAYROLL", slot)
    tot = {k: MZERO for k in ("gross", "net", "pf_ee", "pf_er", "esi_ee", "esi_er", "pt", "tds")}

    for s in structures:
        p = compute_payslip(s)
        run.payslips.append(Payslip(
            employee_id=s.employee_id, basic=p["basic"], hra=p["hra"], allowances=p["allowances"],
            gross=p["gross"], pf_employee=p["pf_employee"], pf_employer=p["pf_employer"],
            esi_employee=p["esi_employee"], esi_employer=p["esi_employer"],
            professional_tax=p["professional_tax"], tds=p["tds"],
            total_deductions=p["total_deductions"], net_pay=p["net_pay"]))
        tot["gross"] += p["gross"]; tot["net"] += p["net_pay"]
        tot["pf_ee"] += p["pf_employee"]; tot["pf_er"] += p["pf_employer"]
        tot["esi_ee"] += p["esi_employee"]; tot["esi_er"] += p["esi_employer"]
        tot["pt"] += p["professional_tax"]; tot["tds"] += p["tds"]
    db.flush()

    lines = [pe.PostingLine(acc("salary_expense"), debit=tot["gross"], narration="Salaries")]
    if tot["pf_er"] > 0:
        lines.append(pe.PostingLine(acc("employer_pf"), debit=tot["pf_er"], narration="Employer PF"))
    if tot["esi_er"] > 0:
        lines.append(pe.PostingLine(acc("employer_esi"), debit=tot["esi_er"], narration="Employer ESI"))
    lines.append(pe.PostingLine(acc("salary_payable"), credit=tot["net"], narration="Net salary payable"))
    pf_total = quantize(tot["pf_ee"] + tot["pf_er"], MONEY_SCALE)
    esi_total = quantize(tot["esi_ee"] + tot["esi_er"], MONEY_SCALE)
    if pf_total > 0:
        lines.append(pe.PostingLine(acc("pf_payable"), credit=pf_total, narration="PF payable"))
    if esi_total > 0:
        lines.append(pe.PostingLine(acc("esi_payable"), credit=esi_total, narration="ESI payable"))
    if tot["pt"] > 0:
        lines.append(pe.PostingLine(acc("pt_payable"), credit=tot["pt"], narration="Professional tax payable"))
    if tot["tds"] > 0:
        lines.append(pe.PostingLine(acc("tds_payable"), credit=tot["tds"], narration="TDS payable"))

    voucher = pe.post_voucher(db, pe.PostingRequest(
        company_id=company_id, voucher_type_code="PRV", voucher_date=run_date,
        posting_reference=ref, narration=f"Payroll {period:%Y-%m}",
        source_module="payroll", source_id=str(run.id), created_by=created_by,
        approved_by=created_by, lines=lines,
    ))
    run.total_gross = tot["gross"]
    run.total_deductions = quantize(tot["gross"] - tot["net"], MONEY_SCALE)
    run.total_net = tot["net"]
    run.posting_reference = ref
    run.status = PayrollStatus.POSTED
    db.flush()
    return {"run_id": str(run.id), "period": period.isoformat(), "employees": len(run.payslips),
            "total_gross": tot["gross"], "total_net": tot["net"], "voucher": voucher}
