"""Administration routes: accounting periods, users, roles."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_company_db, require_permission
from app.core.security import hash_password
from app.models.accounting import AccountingPeriod, PeriodStatus
from app.models.core import Role, User

router = APIRouter(prefix="/api/admin", tags=["admin"])


# --- accounting periods -----------------------------------------------------

@router.get("/periods")
def list_periods(db: Session = Depends(get_company_db), user: User = Depends(require_permission("report.read"))):
    rows = db.execute(select(AccountingPeriod).where(
        AccountingPeriod.company_id == user.company_id).order_by(AccountingPeriod.start_date)).scalars().all()
    return [{"id": str(p.id), "code": p.code, "name": p.name, "fiscal_year": p.fiscal_year,
             "start_date": p.start_date.isoformat(), "end_date": p.end_date.isoformat(),
             "status": p.status} for p in rows]


def _set_period(db, user, period_id, new_status):
    p = db.get(AccountingPeriod, period_id)
    if p is None or p.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Period not found")
    p.status = new_status.value
    db.commit()
    return {"id": str(p.id), "code": p.code, "status": p.status}


@router.post("/periods/{period_id}/close")
def close_period(period_id: uuid.UUID, db: Session = Depends(get_company_db),
                 user: User = Depends(require_permission("period.close"))):
    return _set_period(db, user, period_id, PeriodStatus.CLOSED)


@router.post("/periods/{period_id}/reopen")
def reopen_period(period_id: uuid.UUID, db: Session = Depends(get_company_db),
                  user: User = Depends(require_permission("period.close"))):
    return _set_period(db, user, period_id, PeriodStatus.OPEN)


# --- roles & users ----------------------------------------------------------

@router.get("/roles")
def list_roles(db: Session = Depends(get_db), user: User = Depends(require_permission("master.read"))):
    rows = db.execute(select(Role).order_by(Role.code)).scalars().all()
    return [{"id": str(r.id), "code": r.code, "name": r.name} for r in rows]


@router.get("/users")
def list_users(db: Session = Depends(get_db), user: User = Depends(require_permission("admin.manage"))):
    rows = db.execute(select(User).where(User.company_id == user.company_id).order_by(User.username)).scalars().all()
    return [{"id": str(u.id), "username": u.username, "email": u.email, "full_name": u.full_name,
             "is_active": u.is_active, "is_superuser": u.is_superuser,
             "roles": [r.code for r in u.roles]} for u in rows]


class UserIn(BaseModel):
    username: str
    email: str = Field(min_length=3)
    full_name: str | None = None
    password: str = Field(min_length=6)
    role_codes: list[str] = Field(default_factory=list)
    is_superuser: bool = False


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(p: UserIn, db: Session = Depends(get_db),
                user: User = Depends(require_permission("admin.manage"))):
    if db.execute(select(User).where(User.username == p.username)).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists")
    if db.execute(select(User).where(User.email == str(p.email))).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already exists")
    u = User(company_id=user.company_id, username=p.username, email=str(p.email), full_name=p.full_name,
             hashed_password=hash_password(p.password), is_active=True, is_superuser=p.is_superuser,
             created_by=user.id)
    for code in p.role_codes:
        role = db.execute(select(Role).where(Role.code == code)).scalar_one_or_none()
        if role:
            u.roles.append(role)
    db.add(u); db.commit()
    return {"id": str(u.id), "username": u.username, "roles": [r.code for r in u.roles]}


class RolesIn(BaseModel):
    role_codes: list[str]


@router.put("/users/{user_id}/roles")
def set_user_roles(user_id: uuid.UUID, p: RolesIn, db: Session = Depends(get_db),
                   admin: User = Depends(require_permission("admin.manage"))):
    u = db.get(User, user_id)
    if u is None or u.company_id != admin.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    u.roles = []
    for code in p.role_codes:
        role = db.execute(select(Role).where(Role.code == code)).scalar_one_or_none()
        if role:
            u.roles.append(role)
    db.commit()
    return {"id": str(u.id), "roles": [r.code for r in u.roles]}
