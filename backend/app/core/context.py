"""Request-scoped multi-tenant context resolution.

Resolves the acting company (and optional factory) for a request from the
authenticated user's memberships, validating any client-requested scope
server-side. The frontend may *request* a company/factory via headers, but
authorization is always decided here against membership rows — never trusted
from the client.

Backward compatibility is the design goal: when a user has no membership rows,
the context falls back to ``User.company_id`` so every existing single-company
code path and test behaves exactly as before.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.core import Branch, User
from app.models.tenancy import UserCompanyMembership, UserFactoryMembership


# Company-wide factory access permission: a role holding this reaches any
# factory of its acting company without per-factory membership rows.
FACTORY_ACCESS_ALL = "factory.access_all"


class TenantScopeError(HTTPException):
    def __init__(self, detail: str):
        super().__init__(status.HTTP_403_FORBIDDEN, detail)


@dataclass
class TenantContext:
    user: User
    company_id: uuid.UUID
    factory_id: uuid.UUID | None  # == branch id

    def require_company(self) -> uuid.UUID:
        if self.company_id is None:
            raise TenantScopeError("No company in scope for this request")
        return self.company_id


def _authorized_company_ids(db: Session, user: User) -> set[uuid.UUID]:
    """Companies the user may act in: explicit memberships, plus the legacy
    default company. Superusers are unrestricted (empty set signals 'any')."""
    if user.is_superuser:
        return set()
    ids: set[uuid.UUID] = set(
        db.execute(
            select(UserCompanyMembership.company_id).where(
                UserCompanyMembership.user_id == user.id,
                UserCompanyMembership.is_active.is_(True),
            )
        ).scalars().all()
    )
    if user.company_id is not None:
        ids.add(user.company_id)
    return ids


def _default_company_id(db: Session, user: User) -> uuid.UUID | None:
    row = db.execute(
        select(UserCompanyMembership.company_id).where(
            UserCompanyMembership.user_id == user.id,
            UserCompanyMembership.is_active.is_(True),
            UserCompanyMembership.is_default.is_(True),
        )
    ).scalars().first()
    if row is not None:
        return row
    return user.company_id


def resolve_tenant_context(
    db: Session,
    user: User,
    requested_company_id: uuid.UUID | None = None,
    requested_factory_id: uuid.UUID | None = None,
) -> TenantContext:
    """Pure resolver (no FastAPI deps) so services and tests can call it."""
    authorized = _authorized_company_ids(db, user)
    is_super = user.is_superuser

    # ---- company ----
    if requested_company_id is not None:
        if not is_super and requested_company_id not in authorized:
            raise TenantScopeError("Not a member of the requested company")
        company_id = requested_company_id
    else:
        company_id = _default_company_id(db, user)
        if company_id is None and is_super:
            # superuser with no default: leave unresolved; callers may set later
            company_id = None
    if company_id is None:
        raise TenantScopeError("No authorized company for this user")

    # ---- factory (== branch) ----
    factory_id: uuid.UUID | None = None
    if requested_factory_id is not None:
        branch = db.get(Branch, requested_factory_id)
        if branch is None or branch.company_id != company_id:
            raise TenantScopeError("Factory not found in the acting company")
        if not is_super:
            # Policy (business rule 3): factory access requires EXPLICIT factory
            # membership. Company membership alone does NOT grant access to every
            # factory. A designated role may hold the company-wide override
            # permission ``factory.access_all`` to reach any factory of the
            # acting company without per-factory rows.
            if not user.has_permission(FACTORY_ACCESS_ALL):
                factory_rows = set(db.execute(
                    select(UserFactoryMembership.branch_id).where(
                        UserFactoryMembership.user_id == user.id,
                        UserFactoryMembership.company_id == company_id,
                        UserFactoryMembership.is_active.is_(True),
                    )
                ).scalars().all())
                if requested_factory_id not in factory_rows:
                    raise TenantScopeError(
                        "Not authorized for the requested factory "
                        "(explicit factory membership or factory.access_all required)")
        factory_id = requested_factory_id

    return TenantContext(user=user, company_id=company_id, factory_id=factory_id)


def acting_factory_id(user) -> uuid.UUID | None:
    """The factory (branch) resolved for this request, or None when no factory is
    in scope. Set by ``get_current_user`` from an authorized ``X-Factory-Id``."""
    try:
        return user.__dict__.get("acting_factory_id")
    except AttributeError:
        return None


def apply_factory_scope(stmt, model, user):
    """OPT-IN factory filter: narrows ``stmt`` to the acting factory only when one
    is in scope. With no factory selected the query is unchanged (company-level),
    so nothing is hidden by default and legacy/unassigned rows stay visible until
    a specific factory is chosen."""
    fid = acting_factory_id(user)
    if fid is not None and hasattr(model, "factory_id"):
        stmt = stmt.where(model.factory_id == fid)
    return stmt


# --- explicit factory authorization (for factory_id supplied in a payload) --

def user_factory_ids(db: Session, user: User, company_id: uuid.UUID) -> set[uuid.UUID]:
    """Branch ids the user has explicit, active factory membership for in a company."""
    return set(db.execute(
        select(UserFactoryMembership.branch_id).where(
            UserFactoryMembership.user_id == user.id,
            UserFactoryMembership.company_id == company_id,
            UserFactoryMembership.is_active.is_(True),
        )
    ).scalars().all())


def accessible_factory_ids(db: Session, user: User, company_id: uuid.UUID) -> set[uuid.UUID] | None:
    """The factories the user may act in within a company, or ``None`` for
    'every factory' (superuser or the company-wide ``factory.access_all``)."""
    if user.is_superuser or user.has_permission(FACTORY_ACCESS_ALL):
        return None
    return user_factory_ids(db, user, company_id)


def user_can_access_factory(db: Session, user: User, factory_id: uuid.UUID | None,
                            company_id: uuid.UUID) -> bool:
    """True when ``factory_id`` belongs to ``company_id`` and the user may act in
    it. A ``None`` factory (company-level record) is always accessible."""
    if factory_id is None:
        return True
    branch = db.get(Branch, factory_id)
    if branch is None or branch.company_id != company_id:
        return False
    allowed = accessible_factory_ids(db, user, company_id)
    return allowed is None or factory_id in allowed


class FactoryAuthError(TenantScopeError):
    """A factory/warehouse is outside the acting company or not accessible.

    Carries ``not_found`` so the API layer can distinguish a 404 (wrong company /
    nonexistent — do not leak existence) from a 403 (exists but no access)."""

    def __init__(self, detail: str, *, not_found: bool = False):
        super().__init__(detail)
        self.not_found = not_found


def authorize_factory(db: Session, user: User, factory_id: uuid.UUID | None,
                      company_id: uuid.UUID) -> None:
    """Raise :class:`FactoryAuthError` unless ``factory_id`` is in the acting
    company and the user may act in it. ``None`` is allowed (company-level)."""
    if factory_id is None:
        return
    branch = db.get(Branch, factory_id)
    if branch is None or branch.company_id != company_id:
        raise FactoryAuthError("Factory not found in the acting company", not_found=True)
    allowed = accessible_factory_ids(db, user, company_id)
    if allowed is not None and factory_id not in allowed:
        raise FactoryAuthError(
            "Not authorized for the requested factory "
            "(explicit factory membership or factory.access_all required)")


def authorize_warehouse(db: Session, user: User, warehouse_id: uuid.UUID | None,
                        company_id: uuid.UUID, factory_id: uuid.UUID | None = None) -> None:
    """Raise :class:`FactoryAuthError` unless ``warehouse_id`` belongs to the
    acting company and is compatible with ``factory_id`` (a factory-owned
    warehouse must match the selected factory)."""
    if warehouse_id is None:
        return
    from app.models.masters import Warehouse  # local import avoids a cycle
    wh = db.get(Warehouse, warehouse_id)
    if wh is None or wh.company_id != company_id:
        raise FactoryAuthError("Warehouse not found in the acting company", not_found=True)
    if factory_id is not None and wh.branch_id is not None and wh.branch_id != factory_id:
        raise FactoryAuthError("Warehouse does not belong to the selected factory")


def _parse_uuid(value: str | None, label: str) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid {label}")


def get_tenant_context(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    x_company_id: str | None = Header(default=None),
    x_factory_id: str | None = Header(default=None),
) -> TenantContext:
    """FastAPI dependency: resolve the tenant context for the request.

    Reads optional ``X-Company-Id`` / ``X-Factory-Id`` headers as *requests*,
    validated against membership. Absent headers fall back to the user's default
    company (legacy ``User.company_id``), preserving existing behaviour.
    """
    return resolve_tenant_context(
        db,
        user,
        requested_company_id=_parse_uuid(x_company_id, "X-Company-Id"),
        requested_factory_id=_parse_uuid(x_factory_id, "X-Factory-Id"),
    )
