"""Manufacturing costing & profitability routes."""
from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_company_db
from app.core.deps import require_permission
from app.models.core import User
from app.services import costing

router = APIRouter(prefix="/api/costing", tags=["costing"])


def _s(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _s(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_s(v) for v in obj]
    return obj


def _fin(user):
    return user


@router.get("/batch/{batch_id}")
def batch(batch_id: uuid.UUID, db: Session = Depends(get_company_db),
          user: User = Depends(require_permission("report.financial"))):
    try:
        return _s(costing.batch_cost(db, user.company_id, batch_id))
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))


@router.get("/bath/{bath_id}/standard-vs-actual")
def standard_vs_actual(bath_id: uuid.UUID, db: Session = Depends(get_company_db),
                       user: User = Depends(require_permission("report.financial"))):
    try:
        return _s(costing.bath_standard_vs_actual(db, user.company_id, bath_id))
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))


@router.get("/orders")
def orders(db: Session = Depends(get_company_db), user: User = Depends(require_permission("report.financial"))):
    return _s(costing.order_profitability_list(db, user.company_id))


@router.get("/orders/{order_id}")
def order(order_id: uuid.UUID, db: Session = Depends(get_company_db),
          user: User = Depends(require_permission("report.financial"))):
    try:
        return _s(costing.order_profitability(db, user.company_id, order_id))
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))


@router.get("/customers")
def customers(db: Session = Depends(get_company_db), user: User = Depends(require_permission("report.financial"))):
    return _s(costing.customer_profitability(db, user.company_id))


@router.get("/machines")
def machines(db: Session = Depends(get_company_db), user: User = Depends(require_permission("report.financial"))):
    return _s(costing.machine_cost(db, user.company_id))


@router.get("/shades")
def shades(db: Session = Depends(get_company_db), user: User = Depends(require_permission("report.financial"))):
    return _s(costing.shade_cost(db, user.company_id))


@router.get("/cost-per-kg")
def cost_per_kg(db: Session = Depends(get_company_db), user: User = Depends(require_permission("report.financial"))):
    return _s(costing.cost_per_kg_summary(db, user.company_id))


@router.get("/by-factory")
def by_factory(db: Session = Depends(get_company_db), user: User = Depends(require_permission("report.financial"))):
    """Factory-wise manufacturing throughput & efficiency for the acting company."""
    return _s(costing.production_by_factory(db, user.company_id))
