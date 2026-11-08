"""Management dashboard routes."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_permission
from app.models.core import User
from app.services import dashboard as dash

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _s(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _s(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_s(v) for v in obj]
    return obj


@router.get("/owner")
def owner(as_of: date | None = None, db: Session = Depends(get_db),
          user: User = Depends(require_permission("report.read"))) -> dict:
    return _s(dash.owner_dashboard(db, user.company_id, as_of))


@router.get("/accounts")
def accounts(as_of: date | None = None, db: Session = Depends(get_db),
             user: User = Depends(require_permission("report.read"))) -> dict:
    return _s(dash.accounts_dashboard(db, user.company_id, as_of))


@router.get("/factory")
def factory(as_of: date | None = None, db: Session = Depends(get_db),
            user: User = Depends(require_permission("report.read"))) -> dict:
    return _s(dash.factory_dashboard(db, user.company_id, as_of))


@router.get("/production-trend")
def production_trend(as_of: date | None = None, days: int = 14, db: Session = Depends(get_db),
                     user: User = Depends(require_permission("report.read"))):
    return _s(dash.production_trend(db, user.company_id, as_of, days))


@router.get("/chemical")
def chemical(as_of: date | None = None, db: Session = Depends(get_db),
             user: User = Depends(require_permission("report.read"))) -> dict:
    return _s(dash.chemical_dashboard(db, user.company_id, as_of))
