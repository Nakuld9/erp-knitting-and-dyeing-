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
            factory_rows = db.execute(
                select(UserFactoryMembership.branch_id).where(
                    UserFactoryMembership.user_id == user.id,
                    UserFactoryMembership.company_id == company_id,
                    UserFactoryMembership.is_active.is_(True),
                )
            ).scalars().all()
            # Policy: if the user has explicit factory rows, they are restricted
            # to them; if they have none, company membership implies all factories.
            if factory_rows and requested_factory_id not in set(factory_rows):
                raise TenantScopeError("Not authorized for the requested factory")
        factory_id = requested_factory_id

    return TenantContext(user=user, company_id=company_id, factory_id=factory_id)


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
