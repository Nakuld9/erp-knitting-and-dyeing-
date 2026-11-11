"""Multi-company / multi-factory tenancy models (additive foundation).

These tables are *additive*: they do not alter or depend on existing data and
are safe to create on a live database via an incremental Alembic migration. They
layer group/membership structure on top of the existing single-``company_id``
model without changing it, so current single-company behaviour is preserved.

Hierarchy:
    Organization (optional group) -> Company (legal entity, exists in core.py)
      -> Factory (== core.Branch, reused) -> Warehouse (exists in masters.py)

Access control:
    UserCompanyMembership / UserFactoryMembership grant a user access to one or
    more companies / factories. When a user has no membership rows, the request
    context falls back to ``User.company_id`` (legacy behaviour).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Uuid, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.types import Money
from app.models.base import AuditMixin, Base


class Organization(AuditMixin, Base):
    """Optional holding/group entity above companies, for consolidated reporting.

    A company with ``organization_id IS NULL`` is a standalone legal entity.
    """

    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class GSTRegistration(AuditMixin, Base):
    """A GST registration (GSTIN) held by a company, optionally per branch/state.

    Extracted so a company can hold more than one registration. The inline
    ``gstin``/``state_code`` on ``Company``/``Branch`` remain as the primary
    registration for backward compatibility; this table is the normalised form.
    """

    __tablename__ = "gst_registrations"
    __table_args__ = (
        UniqueConstraint("company_id", "gstin", name="uq_gstreg_company_gstin"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id"), index=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True)
    gstin: Mapped[str] = mapped_column(String(15))
    state_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    legal_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class UserCompanyMembership(AuditMixin, Base):
    """Grants a user access to a company. A user may belong to several."""

    __tablename__ = "user_company_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "company_id", name="uq_ucm_user_company"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    # Optional role scoping at the membership level (code into roles.code). When
    # null, the user's global roles apply within this company.
    role_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class UserFactoryMembership(AuditMixin, Base):
    """Grants a user access to a specific factory (==branch) within a company.

    When a user has company membership but no factory rows, access to all
    factories of that company is implied (policy; see architecture doc).
    """

    __tablename__ = "user_factory_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "branch_id", name="uq_ufm_user_branch"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    role_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ConsolidationElimination(AuditMixin, Base):
    """A report-only consolidation adjustment for a group (Organization).

    Eliminations exist ONLY in the consolidated report — they are NEVER posted to
    any company's ledger, so each company's statutory books stay independent.
    They apply to the consolidated figures for a given account code and must be
    approved by finance before they count toward an approved consolidation.
    """

    __tablename__ = "consolidation_eliminations"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    account_code: Mapped[str] = mapped_column(String(40), index=True)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # elimination amounts applied to the consolidated view for this account
    debit: Mapped[object] = mapped_column(Money, default=0)
    credit: Mapped[object] = mapped_column(Money, default=0)
    # optional cross-reference to the matching inter-company entries
    counterparty_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IntercompanyAccountMapping(AuditMixin, Base):
    """Configurable control accounts for transactions with a related company.

    From ``company_id``'s books, toward ``counterparty_company_id``:
    - ``due_from_account_id``  (asset)     — amounts the counterparty owes us
    - ``due_to_account_id``    (liability) — amounts we owe the counterparty

    Account codes are NOT hardcoded or assumed: an authorized admin picks accounts
    that already exist in this company's chart of accounts, and an accountant must
    APPROVE the mapping before any inter-company posting may use it (fail-closed).
    """

    __tablename__ = "intercompany_account_mappings"
    __table_args__ = (
        UniqueConstraint("company_id", "counterparty_company_id", name="uq_ica_company_counterparty"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    counterparty_company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    due_from_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chart_of_accounts.id"))
    due_to_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chart_of_accounts.id"))
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class CompanyDatabase(AuditMixin, Base):
    """Control-plane registry mapping a company to its dedicated database.

    Lives in the control/default database. When a company has an active row here,
    its company-scoped data lives in the database at ``dsn``; requests acting in
    that company are routed there. A company with no row (or an inactive one)
    stays on the default database (backward compatible).

    NOTE: ``dsn`` may contain credentials; in production prefer a DSN that pulls
    the password from the environment. Access to this table is control-plane only.
    """

    __tablename__ = "company_databases"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), unique=True, index=True)
    dsn: Mapped[str] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    provisioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
