"""Stock transfers, fabric-roll split / merge, and weighted-average valuation."""
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
from app.models.core import User
from app.models.inventory import ItemType, StockTransfer
from app.services import inventory
from app.services.inventory.service import InsufficientStockError
from app.services.accounting.posting_engine import fiscal_year_for
from app.services.manufacturing import roll_ops
from app.services.sequences import next_document_number

router = APIRouter(prefix="/api/inventory", tags=["inventory-ops"])


# ------------------------------ stock transfers -----------------------------

class TransferIn(BaseModel):
    item_type: str = "CHEMICAL"
    item_id: uuid.UUID
    from_warehouse_id: uuid.UUID
    to_warehouse_id: uuid.UUID
    qty: Decimal
    method: str = "FIFO"


@router.post("/transfers", status_code=status.HTTP_201_CREATED)
def create_transfer(payload: TransferIn, db: Session = Depends(get_db),
                    user: User = Depends(require_permission("inventory.adjust"))):
    try:
        it = ItemType(payload.item_type)
        res = inventory.transfer_stock(
            db, company_id=user.company_id, item_type=it, item_id=payload.item_id,
            from_warehouse_id=payload.from_warehouse_id, to_warehouse_id=payload.to_warehouse_id,
            qty=payload.qty, method=payload.method)
        number = next_document_number(db, company_id=user.company_id, doc_type="XFER",
                                      fiscal_year=fiscal_year_for(date.today()), prefix="ST-")
        rec = StockTransfer(company_id=user.company_id, number=number, transfer_date=date.today(),
                            item_type=it.value, item_id=payload.item_id,
                            from_warehouse_id=payload.from_warehouse_id,
                            to_warehouse_id=payload.to_warehouse_id,
                            qty=res["qty"], cost_value=res["cost_value"], created_by=user.id)
        db.add(rec)
        db.commit()
    except (ValueError, InsufficientStockError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return {"transfer_id": str(rec.id), "number": rec.number, "qty": str(rec.qty),
            "cost_value": str(rec.cost_value)}


@router.get("/transfers")
def list_transfers(db: Session = Depends(get_db),
                   user: User = Depends(require_permission("report.read"))):
    rows = db.execute(select(StockTransfer).where(StockTransfer.company_id == user.company_id)
                      .order_by(StockTransfer.transfer_date.desc())).scalars().all()
    return [{"id": str(t.id), "number": t.number, "date": t.transfer_date.isoformat(),
             "item_type": t.item_type, "qty": str(t.qty), "cost_value": str(t.cost_value)}
            for t in rows[:200]]


@router.get("/valuation")
def valuation(item_type: str, item_id: uuid.UUID, db: Session = Depends(get_db),
              user: User = Depends(require_permission("report.read"))):
    it = ItemType(item_type)
    wavg = inventory.weighted_average_cost(db, company_id=user.company_id, item_type=it, item_id=item_id)
    return {"qty_on_hand": str(wavg["qty_on_hand"]), "value": str(wavg["value"]),
            "weighted_avg_cost": str(wavg["avg_cost"])}


# ----------------------------- roll split / merge ---------------------------

class SplitPart(BaseModel):
    weight_kg: Decimal
    roll_no: str | None = None


class SplitIn(BaseModel):
    parts: list[SplitPart] = Field(min_length=1)


@router.post("/rolls/{roll_id}/split", status_code=status.HTTP_201_CREATED)
def split_roll(roll_id: uuid.UUID, payload: SplitIn, db: Session = Depends(get_db),
               user: User = Depends(require_permission("greige.manage"))):
    try:
        res = roll_ops.split_roll(db, company_id=user.company_id, roll_id=roll_id,
                                  parts=[p.model_dump() for p in payload.parts], created_by=user.id)
        db.commit()
    except roll_ops.RollOpError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    except InsufficientStockError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return {"parent_remaining_kg": str(res["parent_remaining_kg"]), "parent_status": res["parent_status"],
            "children": [{"roll_no": c["roll_no"], "weight_kg": str(c["weight_kg"])} for c in res["children"]]}


class MergeIn(BaseModel):
    roll_ids: list[uuid.UUID] = Field(min_length=2)
    new_roll_no: str | None = None


@router.post("/rolls/merge", status_code=status.HTTP_201_CREATED)
def merge_rolls(payload: MergeIn, db: Session = Depends(get_db),
                user: User = Depends(require_permission("greige.manage"))):
    try:
        res = roll_ops.merge_rolls(db, company_id=user.company_id, roll_ids=payload.roll_ids,
                                   new_roll_no=payload.new_roll_no, created_by=user.id)
        db.commit()
    except roll_ops.RollOpError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return {"merged_roll_no": res["roll_no"], "weight_kg": str(res["weight_kg"]),
            "cost_value": str(res["cost_value"]), "sources": res["sources"]}
