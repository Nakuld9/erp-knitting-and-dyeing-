"""FastAPI dependencies: current user and permission enforcement.

``get_current_user`` additionally resolves the **acting company** for the request
from the user's authorized memberships (validating any client-supplied
``X-Company-Id``/``X-Factory-Id`` header server-side) and stamps it onto the
returned user's ``company_id`` for the duration of the request. This is what lets
a multi-company user switch companies: every route and service reads
``user.company_id`` and therefore sees the acting company's data.

The acting value is written with SQLAlchemy ``set_committed_value`` so it is a
clean, non-dirty in-memory value — it is **never persisted**, so the user's
stored default company is untouched. A user with no memberships and no header
resolves to their stored ``company_id`` (backward compatible).
"""
from __future__ import annotations

import uuid

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.core import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def _parse_header_uuid(value: str | None, label: str) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid {label}")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    x_company_id: str | None = Header(default=None),
    x_factory_id: str | None = Header(default=None),
) -> User:
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, TypeError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token subject")
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")

    # Force-load roles+permissions while attached (used by has_permission below
    # and by the resolver's factory-access check).
    _ = user.permission_codes

    # Resolve + authorize the acting company/factory for this request, then stamp
    # the acting company onto the in-memory user without marking it dirty.
    # Deferred import avoids a module-load cycle (context imports this module).
    from app.core.context import resolve_tenant_context

    ctx = resolve_tenant_context(
        db, user,
        requested_company_id=_parse_header_uuid(x_company_id, "X-Company-Id"),
        requested_factory_id=_parse_header_uuid(x_factory_id, "X-Factory-Id"),
    )
    if ctx.company_id is not None and ctx.company_id != user.company_id:
        set_committed_value(user, "company_id", ctx.company_id)
    # Expose the resolved factory on the request's user object (transient; not a
    # mapped column, so never persisted). Routes may read it once factory
    # filtering is wired.
    user.__dict__["acting_factory_id"] = ctx.factory_id
    return user


def get_company_db(user: User = Depends(get_current_user)):
    """Yield a session routed to the ACTING COMPANY's database (database-per-company).

    Auth/memberships live in the control/default database (loaded by
    get_current_user); this dependency routes *company-scoped* reads/writes to the
    company's own database when one is registered, falling back to the default
    database otherwise. Routes adopt this in place of get_db as part of the staged
    database-per-company cutover (see docs/DATABASE_PER_COMPANY.md)."""
    from app.core.db_router import company_session

    with company_session(user.company_id) as db:
        yield db


def require_permission(code: str):
    """Dependency factory enforcing a single permission code."""

    def checker(user: User = Depends(get_current_user)) -> User:
        if not user.has_permission(code):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"Permission '{code}' required"
            )
        return user

    return checker
