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


# ------------------------------ stock on hand -------------------------------

@router.get("/stock-on-hand")
def stock_on_hand(warehouse_id: uuid.UUID | None = None, item_type: str | None = None,
                  factory_id: uuid.UUID | None = None,
                  db: Session = Depends(get_db),
                  user: User = Depends(require_permission("report.read"))):
    """Live stock position (qty + value) by item and warehouse, from open FIFO
    layers. Optionally filter to one warehouse, item type and/or factory. When a
    factory is in request scope (authorized X-Factory-Id) and no explicit
    factory_id is given, the position is restricted to the acting factory."""
    from app.core.context import acting_factory_id
    it = None
    if item_type:
        try:
            it = ItemType(item_type.upper())
        except ValueError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"item_type must be one of {[t.value for t in ItemType]}")
    fac = factory_id or acting_factory_id(user)
    rows = inventory.stock_on_hand(db, user.company_id, warehouse_id=warehouse_id,
                                   item_type=it, factory_id=fac)
    return [{**r, "qty": str(r["qty"]), "value": str(r["value"])} for r in rows]


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


class StockCountIn(BaseModel):
    item_type: str = "CHEMICAL"
    item_id: uuid.UUID
    warehouse_id: uuid.UUID | None = None
    counted_qty: Decimal
    count_date: date | None = None
    reason: str | None = None


@router.post("/stock-count", status_code=status.HTTP_201_CREATED)
def stock_count(payload: StockCountIn, db: Session = Depends(get_db),
                user: User = Depends(require_permission("inventory.adjust"))):
    from app.services import stock_count as sc
    try:
        res = sc.post_stock_count(
            db, company_id=user.company_id, item_type=payload.item_type, item_id=payload.item_id,
            warehouse_id=payload.warehouse_id, counted_qty=payload.counted_qty,
            count_date=payload.count_date, reason=payload.reason, created_by=user.id)
        db.commit()
    except (sc.StockCountError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    v = res.get("voucher")
    return {"number": res["number"], "book_qty": str(res["book_qty"]),
            "counted_qty": str(res["counted_qty"]), "variance_qty": str(res["variance_qty"]),
            "variance_value": str(res["variance_value"]), "voucher_number": v.number if v else None}


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
