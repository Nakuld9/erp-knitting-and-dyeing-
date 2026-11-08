"""Pydantic request/response schemas."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- auth -------------------------------------------------------------------

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(ORMModel):
    id: uuid.UUID
    username: str
    email: str
    full_name: str | None = None
    is_superuser: bool
    company_id: uuid.UUID | None = None
    permissions: list[str] = Field(default_factory=list)


# --- masters ----------------------------------------------------------------

class PartyCreate(BaseModel):
    code: str
    name: str
    gstin: str | None = None
    state_code: str | None = None
    address: str | None = None
    email: str | None = None
    phone: str | None = None


class CustomerCreate(PartyCreate):
    credit_limit: Decimal = Decimal("0")


class SupplierCreate(PartyCreate):
    is_chemical_supplier: bool = False


class PartyOut(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    gstin: str | None = None
    state_code: str | None = None
    credit_limit: Decimal | None = None


class WarehouseCreate(BaseModel):
    code: str
    name: str
    kind: str = "STORE"  # STORE / GREIGE / FG / DISPENSING_TANK


class WarehouseOut(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    kind: str
    is_active: bool


# --- chart of accounts ------------------------------------------------------

class AccountOut(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    account_type: str
    is_group: bool
    allow_posting: bool
    parent_id: uuid.UUID | None = None


# --- vouchers ---------------------------------------------------------------

class VoucherLineIn(BaseModel):
    account_code: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    narration: str | None = None
    cost_centre_id: uuid.UUID | None = None

    @field_validator("debit", "credit")
    @classmethod
    def _non_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("amount cannot be negative")
        return v


class VoucherCreate(BaseModel):
    voucher_type_code: str
    voucher_date: date
    posting_reference: str
    narration: str | None = None
    branch_id: uuid.UUID | None = None
    lines: list[VoucherLineIn] = Field(min_length=2)


class VoucherLineOut(ORMModel):
    line_no: int
    account_id: uuid.UUID
    debit: Decimal
    credit: Decimal
    narration: str | None = None


class VoucherOut(ORMModel):
    id: uuid.UUID
    number: str
    voucher_date: date
    status: str
    narration: str | None = None
    posting_reference: str
    total_debit: Decimal
    total_credit: Decimal
    lines: list[VoucherLineOut] = Field(default_factory=list)


class ReverseRequest(BaseModel):
    reason: str
