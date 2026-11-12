"""Chart of accounts routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_company_db
from app.core.deps import require_permission
from app.models.accounting import AccountType, ChartOfAccount
from app.models.core import User
from app.schemas import AccountOut

router = APIRouter(prefix="/api/accounts", tags=["accounting"])


@router.get("", response_model=list[AccountOut])
def list_accounts(
    db: Session = Depends(get_company_db),
    user: User = Depends(require_permission("master.read")),
) -> list[ChartOfAccount]:
    return (
        db.execute(
            select(ChartOfAccount)
            .where(ChartOfAccount.company_id == user.company_id)
            .order_by(ChartOfAccount.code)
        )
        .scalars()
        .all()
    )


class AccountCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=20)
    name: str = Field(..., min_length=1, max_length=200)
    account_type: str                      # ASSET / LIABILITY / EQUITY / INCOME / EXPENSE
    parent_code: str | None = None         # the group this account sits under
    is_group: bool = False                 # a heading (cannot receive postings)
    requires_cost_centre: bool = False


def _find(db: Session, company_id, code: str) -> ChartOfAccount | None:
    return db.execute(select(ChartOfAccount).where(
        ChartOfAccount.company_id == company_id, ChartOfAccount.code == code)).scalar_one_or_none()


@router.post("", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def create_account(
    payload: AccountCreate,
    db: Session = Depends(get_company_db),
    user: User = Depends(require_permission("master.write")),
) -> ChartOfAccount:
    try:
        acct_type = AccountType(payload.account_type.upper())
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Invalid account_type; use one of {[t.value for t in AccountType]}")
    if _find(db, user.company_id, payload.code) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Account code {payload.code} already exists")

    parent = None
    if payload.parent_code:
        parent = _find(db, user.company_id, payload.parent_code)
        if parent is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"Parent account {payload.parent_code} not found")
        if not parent.is_group:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"Parent {payload.parent_code} is not a group; pick a heading account")
        if parent.account_type != acct_type:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"Account type must match the parent group ({parent.account_type})")

    acct = ChartOfAccount(
        company_id=user.company_id, code=payload.code.strip(), name=payload.name.strip(),
        account_type=acct_type, parent_id=parent.id if parent else None,
        is_group=payload.is_group, allow_posting=not payload.is_group,
        requires_cost_centre=payload.requires_cost_centre, created_by=user.id,
    )
    db.add(acct)
    db.commit()
    db.refresh(acct)
    return acct
