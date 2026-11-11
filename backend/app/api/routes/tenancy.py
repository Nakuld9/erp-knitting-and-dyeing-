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

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

# Standard 15-char GSTIN: 2-digit state + 10-char PAN + entity + 'Z' + checksum.
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
from sqlalchemy import select
from sqlalchemy.orm import Session

from datetime import datetime, timezone
from decimal import Decimal

from app.core.context import TenantContext, get_tenant_context, _authorized_company_ids
from app.core.database import get_db
from app.core.deps import get_current_user, require_permission
from app.models.core import Branch, Company, User
from app.models.tenancy import (
    ConsolidationElimination,
    GSTRegistration,
    IntercompanyAccountMapping,
    Organization,
    UserCompanyMembership,
    UserFactoryMembership,
)
from app.services.accounting import consolidation as consol


def _stringify(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _stringify(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stringify(v) for v in obj]
    return obj

router = APIRouter(tags=["tenancy"])


# --- legacy-factory reconciliation (read-only visibility) -------------------

@router.get("/api/admin/factory-reconciliation")
def factory_reconciliation(db: Session = Depends(get_db),
                           user: User = Depends(require_permission("report.read"))):
    """Historical operational rows still sitting on a Legacy/Unassigned factory,
    i.e. pending reconciliation to a real factory. Backfill itself is a deliberate
    CLI step (scripts/backfill_legacy_factory.py), never run from the API."""
    from app.services.tenancy_backfill import reconciliation_report
    return reconciliation_report(db)


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

    @field_validator("gstin")
    @classmethod
    def _validate_gstin(cls, v: str) -> str:
        v = v.strip().upper()
        if not _GSTIN_RE.match(v):
            raise ValueError("Invalid GSTIN format (expected 15-char GSTIN, e.g. 27AAAAA0000A1Z5)")
        return v


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
    # Current business rule: exactly one ACTIVE GST registration per company.
    # (The schema supports many; this limits active ones. Deactivate the existing
    # one first to replace it.)
    active_exists = db.execute(select(GSTRegistration).where(
        GSTRegistration.company_id == p.company_id,
        GSTRegistration.is_active.is_(True))).first()
    if active_exists is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Company already has an active GST registration (one active GSTIN per "
            "company). Deactivate the existing registration before adding another.")
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


# --- consolidation (group-level, read-only reporting) -----------------------

def _require_group_access(db, user, member_ids):
    """Consolidation spans companies; the caller must be authorized for all
    member companies (or be a superuser)."""
    if user.is_superuser:
        return
    authorized = _authorized_company_ids(db, user)
    if not set(member_ids).issubset(authorized):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Not authorized for every company in this group")


@router.get("/api/reports/consolidated")
def consolidated_report(organization_id: uuid.UUID, date_from: str | None = None,
                        date_to: str | None = None, db: Session = Depends(get_db),
                        user: User = Depends(require_permission("report.financial"))):
    from datetime import date as _date
    org = db.get(Organization, organization_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    member_ids = consol.member_company_ids(db, organization_id)
    _require_group_access(db, user, member_ids)
    df = _date.fromisoformat(date_from) if date_from else None
    dt = _date.fromisoformat(date_to) if date_to else None
    return _stringify(consol.consolidated_trial_balance(db, organization_id, date_from=df, date_to=dt))


class EliminationIn(BaseModel):
    organization_id: uuid.UUID
    account_code: str = Field(min_length=1, max_length=40)
    description: str | None = None
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    counterparty_ref: str | None = None


@router.get("/api/admin/consolidation-eliminations")
def list_eliminations(organization_id: uuid.UUID, db: Session = Depends(get_db),
                      user: User = Depends(require_permission("report.financial"))):
    rows = db.execute(select(ConsolidationElimination).where(
        ConsolidationElimination.organization_id == organization_id)
        .order_by(ConsolidationElimination.account_code)).scalars().all()
    return [{"id": str(e.id), "account_code": e.account_code, "description": e.description,
             "debit": str(e.debit or 0), "credit": str(e.credit or 0),
             "counterparty_ref": e.counterparty_ref, "is_approved": e.is_approved,
             "approved_at": e.approved_at.isoformat() if e.approved_at else None} for e in rows]


@router.post("/api/admin/consolidation-eliminations", status_code=status.HTTP_201_CREATED)
def create_elimination(p: EliminationIn, db: Session = Depends(get_db),
                       user: User = Depends(require_permission("admin.manage"))):
    if db.get(Organization, p.organization_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    if p.debit < 0 or p.credit < 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Amounts must be non-negative")
    e = ConsolidationElimination(
        organization_id=p.organization_id, account_code=p.account_code.strip(),
        description=p.description, debit=p.debit, credit=p.credit,
        counterparty_ref=p.counterparty_ref, is_approved=False, created_by=user.id)
    db.add(e)
    db.commit()
    return {"id": str(e.id), "is_approved": e.is_approved}


@router.post("/api/admin/consolidation-eliminations/{elim_id}/approve")
def approve_elimination(elim_id: uuid.UUID, db: Session = Depends(get_db),
                        user: User = Depends(require_permission("consolidation.approve"))):
    e = db.get(ConsolidationElimination, elim_id)
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Elimination not found")
    e.is_approved = True
    e.approved_by = user.id
    e.approved_at = datetime.now(timezone.utc)
    db.commit()
    return {"id": str(e.id), "is_approved": True, "approved_at": e.approved_at.isoformat()}


# --- inter-company account mappings (configurable, accountant-approved) ------

from app.services.accounting import intercompany as ic  # noqa: E402
from app.services.accounting.intercompany import IntercompanyConfigError  # noqa: E402
from app.models.accounting import ChartOfAccount  # noqa: E402


class IntercompanyMappingIn(BaseModel):
    company_id: uuid.UUID
    counterparty_company_id: uuid.UUID
    due_from_account_code: str = Field(min_length=1, max_length=20)
    due_to_account_code: str = Field(min_length=1, max_length=20)


def _account_id_by_code(db, company_id, code):
    acct = db.execute(select(ChartOfAccount).where(
        ChartOfAccount.company_id == company_id, ChartOfAccount.code == code)).scalar_one_or_none()
    if acct is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"Account {code} not found in this company's chart of accounts")
    return acct.id


@router.get("/api/admin/intercompany-mappings")
def list_intercompany_mappings(db: Session = Depends(get_db),
                               user: User = Depends(require_permission("report.financial"))):
    rows = db.execute(select(IntercompanyAccountMapping)).scalars().all()
    return [{"id": str(m.id), "company_id": str(m.company_id),
             "counterparty_company_id": str(m.counterparty_company_id),
             "due_from_account_id": str(m.due_from_account_id),
             "due_to_account_id": str(m.due_to_account_id),
             "is_approved": m.is_approved, "is_active": m.is_active} for m in rows]


@router.post("/api/admin/intercompany-mappings", status_code=status.HTTP_201_CREATED)
def create_intercompany_mapping(p: IntercompanyMappingIn, db: Session = Depends(get_db),
                                user: User = Depends(require_permission("admin.manage"))):
    due_from_id = _account_id_by_code(db, p.company_id, p.due_from_account_code)
    due_to_id = _account_id_by_code(db, p.company_id, p.due_to_account_code)
    try:
        m = ic.create_mapping(db, company_id=p.company_id,
                              counterparty_company_id=p.counterparty_company_id,
                              due_from_account_id=due_from_id, due_to_account_id=due_to_id,
                              created_by=user.id)
    except IntercompanyConfigError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    return {"id": str(m.id), "is_approved": m.is_approved}


@router.post("/api/admin/intercompany-mappings/{mapping_id}/approve")
def approve_intercompany_mapping(mapping_id: uuid.UUID, db: Session = Depends(get_db),
                                 user: User = Depends(require_permission("intercompany.approve"))):
    try:
        m = ic.approve_mapping(db, mapping_id, user.id)
    except IntercompanyConfigError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    return {"id": str(m.id), "is_approved": True,
            "approved_at": m.approved_at.isoformat() if m.approved_at else None}


@router.get("/api/admin/intercompany-reconciliation")
def intercompany_reconciliation(db: Session = Depends(get_db),
                                user: User = Depends(require_permission("report.financial"))):
    return ic.reconciliation(db)


# --- inter-company posting (dual-leg, atomic) -------------------------------

from app.services.tax import GstStateError  # noqa: E402
from app.services.accounting.posting_engine import PostingError  # noqa: E402


class IntercompanyPostingIn(BaseModel):
    seller_company_id: uuid.UUID
    buyer_company_id: uuid.UUID
    taxable: Decimal
    voucher_date: str
    reference: str = Field(min_length=1, max_length=60)
    tax_code_id: uuid.UUID | None = None
    narration: str | None = None
    seller_income_code: str | None = None
    buyer_expense_code: str | None = None


@router.post("/api/accounting/intercompany-posting", status_code=status.HTTP_201_CREATED)
def post_intercompany(p: IntercompanyPostingIn, db: Session = Depends(get_db),
                      user: User = Depends(require_permission("voucher.post"))):
    # posting touches both companies' ledgers — require access to both
    if not user.is_superuser:
        authorized = _authorized_company_ids(db, user)
        if not {p.seller_company_id, p.buyer_company_id}.issubset(authorized):
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "You must be authorized for both companies to post an inter-company charge")
    if p.taxable <= 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "taxable must be positive")
    from datetime import date as _date
    try:
        vdate = _date.fromisoformat(p.voucher_date)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "voucher_date must be YYYY-MM-DD")
    try:
        return ic.post_intercompany_charge(
            db, seller_company_id=p.seller_company_id, buyer_company_id=p.buyer_company_id,
            taxable=p.taxable, voucher_date=vdate, reference=p.reference,
            tax_code_id=p.tax_code_id, narration=p.narration, created_by=user.id,
            seller_income_code=p.seller_income_code, buyer_expense_code=p.buyer_expense_code)
    except (IntercompanyConfigError, GstStateError, PostingError) as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))


@router.get("/api/reports/consolidated/pnl")
def consolidated_pnl(organization_id: uuid.UUID, date_from: str | None = None,
                     date_to: str | None = None, db: Session = Depends(get_db),
                     user: User = Depends(require_permission("report.financial"))):
    """Group consolidated Profit & Loss (read-only)."""
    from datetime import date as _date
    org = db.get(Organization, organization_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    member_ids = consol.member_company_ids(db, organization_id)
    _require_group_access(db, user, member_ids)
    df = _date.fromisoformat(date_from) if date_from else None
    dt = _date.fromisoformat(date_to) if date_to else None
    return _stringify(consol.consolidated_profit_and_loss(db, organization_id, date_from=df, date_to=dt))


@router.get("/api/reports/consolidated/elimination-suggestions")
def consolidated_elimination_suggestions(organization_id: uuid.UUID, db: Session = Depends(get_db),
                                         user: User = Depends(require_permission("report.financial"))):
    """Read-only: suggested eliminations from matched inter-company balances."""
    org = db.get(Organization, organization_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    member_ids = consol.member_company_ids(db, organization_id)
    _require_group_access(db, user, member_ids)
    return consol.suggest_intercompany_eliminations(db, organization_id)
