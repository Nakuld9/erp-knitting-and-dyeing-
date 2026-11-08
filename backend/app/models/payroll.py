"""Payroll: per-employee salary structures, monthly runs and payslips.

A salary structure holds the earnings and the statutory rates (all editable,
never hardcoded in the engine). A payroll run computes a payslip per employee
and posts one balanced PRV voucher for the batch.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.types import Money, Quantity
from app.models.base import AuditMixin, Base


class PayrollStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    POSTED = "POSTED"


class SalaryStructure(AuditMixin, Base):
    """Current earnings + statutory rates for an employee. One active row per
    employee (the latest effective_from is used by a run)."""
    __tablename__ = "salary_structures"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), index=True)
    effective_from: Mapped[date] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # earnings
    basic: Mapped[object] = mapped_column(Money, default=0)
    hra: Mapped[object] = mapped_column(Money, default=0)
    conveyance: Mapped[object] = mapped_column(Money, default=0)
    other_allowances: Mapped[object] = mapped_column(Money, default=0)
    # statutory rates (configurable, with common Indian defaults)
    pf_applicable: Mapped[bool] = mapped_column(Boolean, default=True)
    pf_employee_pct: Mapped[object] = mapped_column(Quantity, default=12)
    pf_employer_pct: Mapped[object] = mapped_column(Quantity, default=12)
    pf_wage_ceiling: Mapped[object] = mapped_column(Money, default=15000)
    esi_applicable: Mapped[bool] = mapped_column(Boolean, default=False)
    esi_employee_pct: Mapped[object] = mapped_column(Quantity, default="0.75")
    esi_employer_pct: Mapped[object] = mapped_column(Quantity, default="3.25")
    professional_tax: Mapped[object] = mapped_column(Money, default=0)
    tds_monthly: Mapped[object] = mapped_column(Money, default=0)


class PayrollRun(AuditMixin, Base):
    __tablename__ = "payroll_runs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    period_month: Mapped[date] = mapped_column(Date, index=True)   # first day of the paid month
    run_date: Mapped[date] = mapped_column(Date)
    status: Mapped[PayrollStatus] = mapped_column(String(10), default=PayrollStatus.DRAFT)
    total_gross: Mapped[object] = mapped_column(Money, default=0)
    total_deductions: Mapped[object] = mapped_column(Money, default=0)
    total_net: Mapped[object] = mapped_column(Money, default=0)
    posting_reference: Mapped[str | None] = mapped_column(String(120), index=True)

    payslips: Mapped[list["Payslip"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class Payslip(AuditMixin, Base):
    __tablename__ = "payslips"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payroll_runs.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), index=True)
    basic: Mapped[object] = mapped_column(Money, default=0)
    hra: Mapped[object] = mapped_column(Money, default=0)
    allowances: Mapped[object] = mapped_column(Money, default=0)
    gross: Mapped[object] = mapped_column(Money, default=0)
    pf_employee: Mapped[object] = mapped_column(Money, default=0)
    pf_employer: Mapped[object] = mapped_column(Money, default=0)
    esi_employee: Mapped[object] = mapped_column(Money, default=0)
    esi_employer: Mapped[object] = mapped_column(Money, default=0)
    professional_tax: Mapped[object] = mapped_column(Money, default=0)
    tds: Mapped[object] = mapped_column(Money, default=0)
    total_deductions: Mapped[object] = mapped_column(Money, default=0)
    net_pay: Mapped[object] = mapped_column(Money, default=0)

    run: Mapped[PayrollRun] = relationship(back_populates="payslips")
