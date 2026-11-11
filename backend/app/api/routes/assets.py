"""Fixed-asset register, depreciation runs and disposal."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_permission
from app.models.assets import AssetStatus, FixedAsset
from app.models.core import User
from app.services import assets as svc

router = APIRouter(prefix="/api/assets", tags=["assets"])


class AssetIn(BaseModel):
    code: str
    name: str
    category: str | None = None
    asset_account_code: str
    acquisition_date: date
    cost: Decimal
    salvage_value: Decimal = Decimal("0")
    method: str = "SLM"
    useful_life_months: int | None = None
    dep_rate_pct: Decimal | None = None
    credit_account_code: str | None = None


def _asset_out(a: FixedAsset) -> dict:
    cost = Decimal(str(a.capitalized_cost or 0))
    accum = Decimal(str(a.accumulated_depreciation or 0))
    return {"id": str(a.id), "code": a.code, "name": a.name, "category": a.category,
            "asset_account_code": a.asset_account_code,
            "acquisition_date": a.acquisition_date.isoformat(),
            "cost": str(cost), "salvage_value": str(a.salvage_value),
            "method": a.method.value if hasattr(a.method, "value") else a.method,
            "useful_life_months": a.useful_life_months,
            "dep_rate_pct": str(a.dep_rate_pct) if a.dep_rate_pct is not None else None,
            "accumulated_depreciation": str(accum), "book_value": str(cost - accum),
            "status": a.status.value if hasattr(a.status, "value") else a.status,
            "disposal_date": a.disposal_date.isoformat() if a.disposal_date else None}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_asset(payload: AssetIn, db: Session = Depends(get_db),
                 user: User = Depends(require_permission("asset.manage"))):
    try:
        asset = svc.capitalize_asset(
            db, company_id=user.company_id, code=payload.code, name=payload.name,
            category=payload.category, asset_account_code=payload.asset_account_code,
            acquisition_date=payload.acquisition_date, cost=payload.cost,
            salvage=payload.salvage_value, method=payload.method,
            useful_life_months=payload.useful_life_months, dep_rate_pct=payload.dep_rate_pct,
            credit_account_code=payload.credit_account_code, created_by=user.id,
        )
        db.commit()
    except svc.AssetError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return _asset_out(asset)


@router.get("")
def list_assets(db: Session = Depends(get_db),
                user: User = Depends(require_permission("asset.manage"))):
    rows = db.execute(select(FixedAsset).where(FixedAsset.company_id == user.company_id)
                      .order_by(FixedAsset.code)).scalars().all()
    return [_asset_out(a) for a in rows[:500]]


class DepRunIn(BaseModel):
    period_month: date   # any day in the month to charge


@router.post("/depreciation-runs", status_code=status.HTTP_201_CREATED)
def run_depreciation(payload: DepRunIn, db: Session = Depends(get_db),
                     user: User = Depends(require_permission("asset.manage"))):
    res = svc.run_depreciation(db, company_id=user.company_id,
                               period_month=payload.period_month, created_by=user.id)
    db.commit()
    voucher = res.get("voucher")
    return {"period": res["period"], "already_posted": res.get("already_posted", False),
            "assets_charged": res.get("assets_charged", 0),
            "total": str(res.get("total", "")),
            "voucher_number": voucher.number if voucher else None}


class DisposeIn(BaseModel):
    disposal_date: date
    proceeds: Decimal = Decimal("0")
    proceeds_account_code: str | None = None


@router.post("/{asset_id}/dispose")
def dispose_asset(asset_id: uuid.UUID, payload: DisposeIn, db: Session = Depends(get_db),
                  user: User = Depends(require_permission("asset.manage"))):
    asset = db.get(FixedAsset, asset_id)
    if asset is None or asset.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Asset not found")
    try:
        res = svc.dispose_asset(db, company_id=user.company_id, asset=asset,
                                disposal_date=payload.disposal_date, proceeds=payload.proceeds,
                                proceeds_account_code=payload.proceeds_account_code, created_by=user.id)
        db.commit()
    except svc.AssetError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    voucher = res.get("voucher")
    return {"asset_id": res["asset_id"], "book_value": str(res["book_value"]),
            "proceeds": str(res["proceeds"]), "gain_loss": str(res["gain_loss"]),
            "voucher_number": voucher.number if voucher else None}
