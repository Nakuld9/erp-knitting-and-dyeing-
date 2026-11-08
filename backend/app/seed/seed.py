"""Idempotent seeding of baseline data.

Creates (if absent): permissions, roles, voucher types, a demo company with its
chart of accounts, a full set of monthly accounting periods for the current
Indian financial year, a few units of measure, and a superuser.

Run:  python -m app.seed.seed
"""
from __future__ import annotations

import os
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, engine
from app.core.security import hash_password
from app.models import Base
from app.models.accounting import (
    AccountingPeriod,
    ChartOfAccount,
    PeriodStatus,
    VoucherType,
)
from app.models.core import Company, Permission, Role, User
from app.models.masters import CostCentre, CostCentreType, UnitOfMeasure

# Departmental cost centres (brief module 14). Machines (work centres) sit under
# these by machine type.
DEPARTMENT_CENTRES = [
    ("KNIT", "Knitting"), ("DYE", "Dyeing"), ("FINISH", "Finishing"),
    ("CHEMSTORE", "Chemical Store"), ("UTIL", "Utilities"), ("MAINT", "Maintenance"),
    ("ADMIN", "Administration"), ("PACK", "Packing"),
]
# machine_type -> department cost-centre code
DEPT_FOR_MACHINE_TYPE = {"KNITTING": "KNIT", "DYEING": "DYE", "FINISHING": "FINISH"}
from app.seed.chart_of_accounts import COA, VOUCHER_TYPES
from app.seed.rbac import PERMISSIONS, ROLES
from app.services.accounting.posting_rules import seed_default_posting_rules

DEMO_COMPANY_CODE = "DEMO"
UOMS = [
    ("KG", "Kilogram", "MASS", 1),
    ("G", "Gram", "MASS", "0.001"),
    ("L", "Litre", "VOLUME", 1),
    ("ML", "Millilitre", "VOLUME", "0.001"),
    ("M", "Meter", "LENGTH", 1),
    ("ROLL", "Roll", "COUNT", 1),
    ("PCS", "Pieces", "COUNT", 1),
]


def _get_or_create(db: Session, model, defaults=None, **keys):
    obj = db.execute(select(model).filter_by(**keys)).scalar_one_or_none()
    if obj:
        return obj, False
    params = {**keys, **(defaults or {})}
    obj = model(**params)
    db.add(obj)
    db.flush()
    return obj, True


def seed_cost_centres(db: Session, company: Company) -> None:
    for code, name in DEPARTMENT_CENTRES:
        _get_or_create(db, CostCentre, {"name": name, "centre_type": CostCentreType.COST},
                       company_id=company.id, code=code)
    db.flush()


def seed_permissions_and_roles(db: Session) -> dict[str, Role]:
    perms: dict[str, Permission] = {}
    for code, module, desc in PERMISSIONS:
        p, _ = _get_or_create(db, Permission, {"module": module, "description": desc}, code=code)
        perms[code] = p

    roles: dict[str, Role] = {}
    for rcode, (name, pcodes) in ROLES.items():
        role, _ = _get_or_create(db, Role, {"name": name}, code=rcode)
        wanted = list(perms.values()) if pcodes == ["*"] else [perms[c] for c in pcodes]
        existing = {p.id for p in role.permissions}
        for p in wanted:
            if p.id not in existing:
                role.permissions.append(p)
        roles[rcode] = role
    db.flush()
    return roles


def seed_voucher_types(db: Session) -> None:
    for code, name, prefix, approval in VOUCHER_TYPES:
        _get_or_create(
            db, VoucherType, {"name": name, "prefix": prefix, "requires_approval": approval}, code=code
        )


def seed_company(db: Session) -> Company:
    company, created = _get_or_create(
        db,
        Company,
        {
            "name": "Demo Textile Mills Pvt Ltd",
            "legal_name": "Demo Textile Mills Private Limited",
            "gstin": "03ABCDE1234F1Z5",
            "state_code": "03",  # Punjab
            "currency": "INR",
            "fiscal_year_start_month": 4,
        },
        code=DEMO_COMPANY_CODE,
    )
    return company


def seed_chart_of_accounts(db: Session, company: Company) -> None:
    code_to_id: dict[str, uuid.UUID] = {}
    # First pass: ensure rows exist (parents resolved in a second pass).
    for code, name, atype, is_group, parent_code, flags in COA:
        acct = db.execute(
            select(ChartOfAccount).filter_by(company_id=company.id, code=code)
        ).scalar_one_or_none()
        if acct is None:
            acct = ChartOfAccount(
                company_id=company.id,
                code=code,
                name=name,
                account_type=atype.value,
                is_group=is_group,
                allow_posting=not is_group,
                is_cash="cash" in flags,
                is_bank="bank" in flags,
                is_control="control" in flags,
            )
            db.add(acct)
            db.flush()
        code_to_id[code] = acct.id
    # Second pass: wire parents.
    for code, name, atype, is_group, parent_code, flags in COA:
        if parent_code:
            acct = db.execute(
                select(ChartOfAccount).filter_by(company_id=company.id, code=code)
            ).scalar_one()
            acct.parent_id = code_to_id[parent_code]
    db.flush()


def seed_periods(db: Session, company: Company, fy_start_year: int) -> None:
    """Create 12 monthly OPEN periods for the given FY (April..March)."""
    fy = f"{fy_start_year}-{fy_start_year + 1}"
    months = [(fy_start_year, m) for m in range(4, 13)] + [(fy_start_year + 1, m) for m in range(1, 4)]
    for y, m in months:
        code = f"{y}-{m:02d}"
        start = date(y, m, 1)
        end = date(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1)
        end = date.fromordinal(end.toordinal() - 1)
        _get_or_create(
            db,
            AccountingPeriod,
            {
                "name": start.strftime("%B %Y"),
                "fiscal_year": fy,
                "start_date": start,
                "end_date": end,
                "status": PeriodStatus.OPEN.value,
            },
            company_id=company.id,
            code=code,
        )


def seed_uoms(db: Session) -> None:
    for code, name, dim, factor in UOMS:
        _get_or_create(
            db, UnitOfMeasure, {"name": name, "dimension": dim, "base_factor": factor}, code=code
        )


def seed_admin(db: Session, company: Company, roles: dict[str, Role]) -> User:
    username = os.getenv("ERP_ADMIN_USERNAME", "admin")
    password = os.getenv("ERP_ADMIN_PASSWORD", "admin123")
    user = db.execute(select(User).filter_by(username=username)).scalar_one_or_none()
    if user is None:
        user = User(
            username=username,
            email=os.getenv("ERP_ADMIN_EMAIL", "admin@demo.local"),
            full_name="System Administrator",
            hashed_password=hash_password(password),
            is_active=True,
            is_superuser=True,
            company_id=company.id,
        )
        if "SUPER_ADMIN" in roles:
            user.roles.append(roles["SUPER_ADMIN"])
        db.add(user)
        db.flush()
    return user


def run_seed(db: Session, *, fy_start_year: int | None = None) -> dict:
    if fy_start_year is None:
        today = date.today()
        fy_start_year = today.year if today.month >= 4 else today.year - 1
    roles = seed_permissions_and_roles(db)
    seed_voucher_types(db)
    company = seed_company(db)
    seed_chart_of_accounts(db, company)
    seed_default_posting_rules(db, company.id)
    seed_periods(db, company, fy_start_year)
    seed_cost_centres(db, company)
    seed_uoms(db)
    admin = seed_admin(db, company, roles)
    db.commit()
    return {"company_id": str(company.id), "admin": admin.username, "fiscal_year": fy_start_year}


def main() -> None:  # pragma: no cover - CLI entry
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        result = run_seed(db)
    print("Seed complete:", result)


if __name__ == "__main__":  # pragma: no cover
    main()
