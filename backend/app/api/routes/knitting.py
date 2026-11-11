"""Knitting production entry route."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import apply_factory_scope
from app.core.database import get_db
from app.core.deps import require_permission
from app.models.core import User
from app.models.manufacturing import KnittingProduction
from app.services.inventory import InsufficientStockError
from app.services.manufacturing.knitting import record_production

router = APIRouter(prefix="/api/knitting", tags=["knitting"])


class RollIn(BaseModel):
    roll_no: str = ""
    weight_kg: Decimal
    length_m: Decimal | None = None
    warehouse_id: uuid.UUID | None = None


class ProductionIn(BaseModel):
    number: str
    machine_id: uuid.UUID
    yarn_id: uuid.UUID
    production_date: date
    produced_kg: Decimal
    yarn_consumed_kg: Decimal
    wastage_kg: Decimal = Decimal("0")
    sales_order_id: uuid.UUID | None = None
    yarn_lot_id: uuid.UUID | None = None
    fabric_type_id: uuid.UUID | None = None
    shift: str | None = None
    rolls: list[RollIn] = Field(min_length=1)


@router.post("/production", status_code=status.HTTP_201_CREATED)
def post_production(payload: ProductionIn, db: Session = Depends(get_db),
                   user: User = Depends(require_permission("knitting.manage"))):
    kp = KnittingProduction(
        company_id=user.company_id, number=payload.number, machine_id=payload.machine_id,
        yarn_id=payload.yarn_id, sales_order_id=payload.sales_order_id, yarn_lot_id=payload.yarn_lot_id,
        fabric_type_id=payload.fabric_type_id, production_date=payload.production_date,
        shift=payload.shift, produced_kg=payload.produced_kg,
        yarn_consumed_kg=payload.yarn_consumed_kg, wastage_kg=payload.wastage_kg, created_by=user.id,
    )
    db.add(kp)
    db.flush()
    try:
        result = record_production(db, kp, [r.model_dump() for r in payload.rolls], created_by=user.id)
        db.commit()
    except InsufficientStockError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return {
        "id": str(kp.id), "number": kp.number,
        "yarn_cost": str(result["yarn_cost"]),
        "rolls_created": len(result["rolls"]),
        "issue_voucher": result["issue_voucher"].number,
        "produce_voucher": result["produce_voucher"].number,
    }


@router.get("/production")
def list_production(db: Session = Depends(get_db), user: User = Depends(require_permission("report.read"))):
    stmt = apply_factory_scope(select(KnittingProduction).where(
        KnittingProduction.company_id == user.company_id), KnittingProduction, user)
    rows = db.execute(stmt.order_by(KnittingProduction.production_date.desc())).scalars().all()
    return [{"id": str(k.id), "number": k.number, "production_date": k.production_date.isoformat(),
             "produced_kg": str(k.produced_kg), "yarn_consumed_kg": str(k.yarn_consumed_kg)} for k in rows]
