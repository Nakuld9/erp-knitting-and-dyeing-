"""Multi-company / multi-factory administration + request scope.

Additive endpoints that make the membership model usable without changing any
existing single-company behaviour:

- ``GET  /api/me/context``            the caller's authorized companies/factories
                                       and the resolved active scope.
- ``GET/POST /api/admin/organizations`` manage group/holding entities.
- ``GET/POST /api/admin/memberships``   grant users access to companies/factories.
- ``GET/POST /api/admin/gst-registrations`` manage a company's GSTINs.

Writes are guarded by ``admin.manage``; the scope endpoint only needs a valid
user. Nothing here backfills or enforces ownership on existing operational data.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import TenantContext, get_tenant_context, _authorized_company_ids
from app.core.database import get_db
from app.core.deps import get_current_user, require_permission
from app.models.core import Branch, Company, User
from app.models.tenancy import (
    GSTRegistration,
    Organization,
    UserCompanyMembership,
    UserFactoryMembership,
)

router = APIRouter(tags=["tenancy"])


# --- caller scope -----------------------------------------------------------

@router.get("/api/me/context")
def my_context(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Return the authorized companies/factories and the active resolved scope.

    The frontend uses this to populate company/factory selectors and to confirm
    which scope the backend actually granted (authorization is server-side).
    """
    allowed = _authorized_company_ids(db, user)
    if user.is_superuser and not allowed:
        companies = db.execute(select(Company).where(Company.is_active.is_(True))).scalars().all()
    else:
        companies = db.execute(
            select(Company).where(Company.id.in_(allowed))
        ).scalars().all() if allowed else []

    def _factories(company_id):
        rows = db.execute(
            select(Branch).where(Branch.company_id == company_id, Branch.is_active.is_(True))
        ).scalars().all()
        # restrict to explicit factory memberships when any exist
        fac_ids = set(db.execute(
            select(UserFactoryMembership.branch_id).where(
                UserFactoryMembership.user_id == user.id,
                UserFactoryMembership.company_id == company_id,
                UserFactoryMembership.is_active.is_(True),
            )
        ).scalars().all())
        if fac_ids and not user.is_superuser:
            rows = [b for b in rows if b.id in fac_ids]
        return [{"id": str(b.id), "code": b.code, "name": b.name} for b in rows]

    return {
        "user": {"id": str(user.id), "username": user.username, "is_superuser": user.is_superuser},
        "active": {
            "company_id": str(ctx.company_id) if ctx.company_id else None,
            "factory_id": str(ctx.factory_id) if ctx.factory_id else None,
        },
        "companies": [
            {"id": str(c.id), "code": c.code, "name": c.name,
             "organization_id": str(c.organization_id) if c.organization_id else None,
             "factories": _factories(c.id)}
            for c in companies
        ],
    }


# --- organizations ----------------------------------------------------------

class OrganizationIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=200)


@router.get("/api/admin/organizations")
def list_organizations(db: Session = Depends(get_db),
                       user: User = Depends(require_permission("master.read"))):
    rows = db.execute(select(Organization).order_by(Organization.code)).scalars().all()
    return [{"id": str(o.id), "code": o.code, "name": o.name, "is_active": o.is_active} for o in rows]


@router.post("/api/admin/organizations", status_code=status.HTTP_201_CREATED)
def create_organization(p: OrganizationIn, db: Session = Depends(get_db),
                        user: User = Depends(require_permission("admin.manage"))):
    if db.execute(select(Organization).where(Organization.code == p.code)).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "Organization code already exists")
    o = Organization(code=p.code.strip(), name=p.name.strip(), created_by=user.id)
    db.add(o)
    db.commit()
    return {"id": str(o.id), "code": o.code, "name": o.name}


class CompanyOrgIn(BaseModel):
    organization_id: uuid.UUID | None = None


@router.post("/api/admin/companies/{company_id}/organization")
def set_company_organization(company_id: uuid.UUID, p: CompanyOrgIn,
                             db: Session = Depends(get_db),
                             user: User = Depends(require_permission("admin.manage"))):
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    if p.organization_id is not None and db.get(Organization, p.organization_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    company.organization_id = p.organization_id
    db.commit()
    return {"id": str(company.id),
            "organization_id": str(company.organization_id) if company.organization_id else None}


# --- memberships ------------------------------------------------------------

class CompanyMembershipIn(BaseModel):
    user_id: uuid.UUID
    company_id: uuid.UUID
    role_code: str | None = None
    is_default: bool = False


@router.get("/api/admin/memberships")
def list_memberships(db: Session = Depends(get_db),
                     user: User = Depends(require_permission("admin.manage"))):
    comp = db.execute(select(UserCompanyMembership)).scalars().all()
    fac = db.execute(select(UserFactoryMembership)).scalars().all()
    return {
        "company": [{"id": str(m.id), "user_id": str(m.user_id), "company_id": str(m.company_id),
                     "role_code": m.role_code, "is_default": m.is_default, "is_active": m.is_active}
                    for m in comp],
        "factory": [{"id": str(m.id), "user_id": str(m.user_id), "company_id": str(m.company_id),
                     "branch_id": str(m.branch_id), "role_code": m.role_code, "is_active": m.is_active}
                    for m in fac],
    }


@router.post("/api/admin/memberships/company", status_code=status.HTTP_201_CREATED)
def grant_company_membership(p: CompanyMembershipIn, db: Session = Depends(get_db),
                             user: User = Depends(require_permission("admin.manage"))):
    if db.get(User, p.user_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if db.get(Company, p.company_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    existing = db.execute(select(UserCompanyMembership).where(
        UserCompanyMembership.user_id == p.user_id,
        UserCompanyMembership.company_id == p.company_id)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Membership already exists")
    if p.is_default:
        # only one default per user
        for m in db.execute(select(UserCompanyMembership).where(
                UserCompanyMembership.user_id == p.user_id,
                UserCompanyMembership.is_default.is_(True))).scalars().all():
            m.is_default = False
    m = UserCompanyMembership(user_id=p.user_id, company_id=p.company_id,
                              role_code=p.role_code, is_default=p.is_default, created_by=user.id)
    db.add(m)
    db.commit()
    return {"id": str(m.id)}


class FactoryMembershipIn(BaseModel):
    user_id: uuid.UUID
    branch_id: uuid.UUID
    role_code: str | None = None


@router.post("/api/admin/memberships/factory", status_code=status.HTTP_201_CREATED)
def grant_factory_membership(p: FactoryMembershipIn, db: Session = Depends(get_db),
                             user: User = Depends(require_permission("admin.manage"))):
    branch = db.get(Branch, p.branch_id)
    if branch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Factory (branch) not found")
    if db.get(User, p.user_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    existing = db.execute(select(UserFactoryMembership).where(
        UserFactoryMembership.user_id == p.user_id,
        UserFactoryMembership.branch_id == p.branch_id)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Factory membership already exists")
    m = UserFactoryMembership(user_id=p.user_id, company_id=branch.company_id,
                              branch_id=p.branch_id, role_code=p.role_code, created_by=user.id)
    db.add(m)
    db.commit()
    return {"id": str(m.id)}


# --- GST registrations ------------------------------------------------------

class GstRegistrationIn(BaseModel):
    company_id: uuid.UUID
    gstin: str = Field(min_length=15, max_length=15)
    state_code: str | None = Field(default=None, max_length=2)
    legal_name: str | None = None
    branch_id: uuid.UUID | None = None
    is_primary: bool = False


@router.get("/api/admin/gst-registrations")
def list_gst_registrations(company_id: uuid.UUID | None = None, db: Session = Depends(get_db),
                           user: User = Depends(require_permission("master.read"))):
    stmt = select(GSTRegistration)
    if company_id is not None:
        stmt = stmt.where(GSTRegistration.company_id == company_id)
    rows = db.execute(stmt.order_by(GSTRegistration.gstin)).scalars().all()
    return [{"id": str(g.id), "company_id": str(g.company_id),
             "branch_id": str(g.branch_id) if g.branch_id else None, "gstin": g.gstin,
             "state_code": g.state_code, "legal_name": g.legal_name,
             "is_primary": g.is_primary, "is_active": g.is_active} for g in rows]


@router.post("/api/admin/gst-registrations", status_code=status.HTTP_201_CREATED)
def create_gst_registration(p: GstRegistrationIn, db: Session = Depends(get_db),
                            user: User = Depends(require_permission("admin.manage"))):
    if db.get(Company, p.company_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    if p.branch_id is not None:
        b = db.get(Branch, p.branch_id)
        if b is None or b.company_id != p.company_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Branch not found in this company")
    dup = db.execute(select(GSTRegistration).where(
        GSTRegistration.company_id == p.company_id,
        GSTRegistration.gstin == p.gstin)).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "GSTIN already registered for this company")
    if p.is_primary:
        for g in db.execute(select(GSTRegistration).where(
                GSTRegistration.company_id == p.company_id,
                GSTRegistration.is_primary.is_(True))).scalars().all():
            g.is_primary = False
    g = GSTRegistration(company_id=p.company_id, branch_id=p.branch_id, gstin=p.gstin.strip(),
                        state_code=p.state_code, legal_name=p.legal_name,
                        is_primary=p.is_primary, created_by=user.id)
    db.add(g)
    db.commit()
    return {"id": str(g.id), "gstin": g.gstin}
