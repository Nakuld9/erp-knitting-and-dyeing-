"""Phase 1 multi-company foundation: membership-aware tenant context.

Verifies the additive membership model and the backward-compatible resolver:
- legacy users (no memberships) resolve to User.company_id unchanged;
- a user with memberships can act in each member company and is rejected for
  others (forged X-Company-Id);
- factory (branch) scoping is enforced when explicit factory rows exist;
- superusers are unrestricted.
"""
from __future__ import annotations

import uuid

import pytest

from app.core.context import FACTORY_ACCESS_ALL, TenantScopeError, resolve_tenant_context
from app.models.core import Branch, Company, Permission, Role, User
from app.models.tenancy import UserCompanyMembership, UserFactoryMembership


def _company(db, code):
    c = Company(code=code, name=f"Co {code}")
    db.add(c)
    db.flush()
    return c


def _user(db, username, company_id=None, is_superuser=False):
    u = User(
        username=username,
        email=f"{username}@example.com",
        full_name=username,
        hashed_password="x",
        company_id=company_id,
        is_superuser=is_superuser,
    )
    db.add(u)
    db.flush()
    return u


def _branch(db, company_id, code):
    b = Branch(company_id=company_id, code=code, name=f"Factory {code}")
    db.add(b)
    db.flush()
    return b


def test_legacy_user_resolves_to_company_id(db):
    """A user with no membership rows falls back to User.company_id (legacy)."""
    c = _company(db, "L1")
    u = _user(db, "legacy", company_id=c.id)
    db.flush()

    ctx = resolve_tenant_context(db, u)
    assert ctx.company_id == c.id
    assert ctx.factory_id is None


def test_membership_allows_requested_company(db):
    c1 = _company(db, "M1")
    c2 = _company(db, "M2")
    u = _user(db, "multi", company_id=c1.id)
    db.add_all([
        UserCompanyMembership(user_id=u.id, company_id=c1.id, is_default=True),
        UserCompanyMembership(user_id=u.id, company_id=c2.id),
    ])
    db.flush()

    # default resolves to the is_default membership
    assert resolve_tenant_context(db, u).company_id == c1.id
    # can explicitly act in the second member company
    assert resolve_tenant_context(db, u, requested_company_id=c2.id).company_id == c2.id


def test_forged_company_id_is_rejected(db):
    """Requesting a company the user is not a member of is forbidden."""
    c1 = _company(db, "F1")
    c2 = _company(db, "F2")  # user is NOT a member
    u = _user(db, "attacker", company_id=c1.id)
    db.add(UserCompanyMembership(user_id=u.id, company_id=c1.id, is_default=True))
    db.flush()

    with pytest.raises(TenantScopeError):
        resolve_tenant_context(db, u, requested_company_id=c2.id)

    # a random non-existent id is also rejected
    with pytest.raises(TenantScopeError):
        resolve_tenant_context(db, u, requested_company_id=uuid.uuid4())


def test_factory_scoping_enforced_when_rows_exist(db):
    c = _company(db, "FS")
    f1 = _branch(db, c.id, "A1")
    f2 = _branch(db, c.id, "A2")
    u = _user(db, "factory-user", company_id=c.id)
    db.add_all([
        UserCompanyMembership(user_id=u.id, company_id=c.id, is_default=True),
        UserFactoryMembership(user_id=u.id, company_id=c.id, branch_id=f1.id),
    ])
    db.flush()

    # allowed factory
    ctx = resolve_tenant_context(db, u, requested_factory_id=f1.id)
    assert ctx.factory_id == f1.id
    # forbidden factory (has explicit rows, f2 not among them)
    with pytest.raises(TenantScopeError):
        resolve_tenant_context(db, u, requested_factory_id=f2.id)


def test_factory_from_other_company_rejected(db):
    c1 = _company(db, "FC1")
    c2 = _company(db, "FC2")
    other_factory = _branch(db, c2.id, "B1")
    u = _user(db, "cross", company_id=c1.id)
    db.add(UserCompanyMembership(user_id=u.id, company_id=c1.id, is_default=True))
    db.flush()

    with pytest.raises(TenantScopeError):
        resolve_tenant_context(db, u, requested_factory_id=other_factory.id)


def test_no_factory_rows_denied_without_permission(db):
    """Business rule 3: company membership alone does NOT grant factory access."""
    c = _company(db, "AF")
    f1 = _branch(db, c.id, "X1")
    u = _user(db, "allfac", company_id=c.id)
    db.add(UserCompanyMembership(user_id=u.id, company_id=c.id, is_default=True))
    db.flush()

    with pytest.raises(TenantScopeError):
        resolve_tenant_context(db, u, requested_factory_id=f1.id)


def test_factory_access_all_permission_bypasses_membership(db):
    """A role holding factory.access_all reaches any factory of its company."""
    from sqlalchemy import select
    c = _company(db, "FA")
    f1 = _branch(db, c.id, "Y1")
    u = _user(db, "supervisor", company_id=c.id)
    # reuse the seeded permission rather than create a duplicate code
    perm = db.execute(select(Permission).where(Permission.code == FACTORY_ACCESS_ALL)).scalar_one()
    role = Role(code="plant-head", name="Plant Head")
    role.permissions.append(perm)
    u.roles.append(role)
    db.add(role)
    db.add(UserCompanyMembership(user_id=u.id, company_id=c.id, is_default=True))
    db.flush()

    ctx = resolve_tenant_context(db, u, requested_factory_id=f1.id)
    assert ctx.factory_id == f1.id


def test_superuser_unrestricted(db):
    c1 = _company(db, "SU1")
    c2 = _company(db, "SU2")
    su = _user(db, "root", company_id=c1.id, is_superuser=True)
    db.flush()

    # superuser can act in any company without membership
    assert resolve_tenant_context(db, su, requested_company_id=c2.id).company_id == c2.id
