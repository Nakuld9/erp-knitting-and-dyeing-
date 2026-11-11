"""Core organisational and access-control models.

companies / branches / departments / employees + users / roles / permissions.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String, Table, Uuid, Column
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import AuditMixin, Base, TimestampMixin

# --- association tables -----------------------------------------------------

user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", Uuid, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)

role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", Uuid, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", Uuid, ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)


# --- organisation -----------------------------------------------------------

class Company(AuditMixin, Base):
    __tablename__ = "companies"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Optional group/holding parent for consolidated reporting (additive; nullable
    # so standalone companies and all existing rows are unaffected).
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    legal_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    pan: Mapped[str | None] = mapped_column(String(10), nullable=True)
    state_code: Mapped[str | None] = mapped_column(String(2), nullable=True)  # GST state code
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    fiscal_year_start_month: Mapped[int] = mapped_column(default=4)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    branches: Mapped[list["Branch"]] = relationship(back_populates="company", cascade="all, delete-orphan")


class Branch(AuditMixin, Base):
    __tablename__ = "branches"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    code: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(200))
    gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    state_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Marks the explicit "Legacy / Unassigned" bucket factory used to hold
    # historical operational rows whose real factory could not be established.
    # Rows pointing at such a branch are, by definition, pending reconciliation.
    # This is NOT an operational factory.
    is_legacy_unassigned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    company: Mapped[Company] = relationship(back_populates="branches")


class Department(AuditMixin, Base):
    __tablename__ = "departments"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    code: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Employee(AuditMixin, Base):
    __tablename__ = "employees"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    code: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(200))
    designation: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_operator: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# --- access control ---------------------------------------------------------

class Permission(TimestampMixin, Base):
    __tablename__ = "permissions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)  # e.g. "voucher.post"
    module: Mapped[str] = mapped_column(String(40))
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)


class Role(TimestampMixin, Base):
    __tablename__ = "roles"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)

    permissions: Mapped[list[Permission]] = relationship(secondary=role_permissions, lazy="selectin")


class User(AuditMixin, Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)

    roles: Mapped[list[Role]] = relationship(secondary=user_roles, lazy="selectin")

    @property
    def permission_codes(self) -> set[str]:
        if self.is_superuser:
            return {"*"}
        codes: set[str] = set()
        for role in self.roles:
            for perm in role.permissions:
                codes.add(perm.code)
        return codes

    def has_permission(self, code: str) -> bool:
        pc = self.permission_codes
        return "*" in pc or code in pc
