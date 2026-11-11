"""Factory-to-factory stock transfer routes (same company, goods-in-transit)."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_permission
from app.models.core import User
from app.models.inventory import ItemType
from app.models.transfers import FactoryStockTransfer, TransferStatus
from app.services import factory_transfer as ft
from app.services.factory_transfer import TransferError

router = APIRouter(prefix="/api/transfers", tags=["transfers"])


def _out(t: FactoryStockTransfer) -> dict:
    return {
        "id": str(t.id), "number": t.number, "status": t.status,
        "transfer_date": t.transfer_date.isoformat(),
        "item_type": t.item_type, "item_id": str(t.item_id),
        "from_warehouse_id": str(t.from_warehouse_id), "to_warehouse_id": str(t.to_warehouse_id),
        "from_branch_id": str(t.from_branch_id), "to_branch_id": str(t.to_branch_id),
        "requested_qty": str(t.requested_qty), "dispatched_qty": str(t.dispatched_qty),
        "received_qty": str(t.received_qty), "shortage_qty": str(t.shortage_qty),
        "rejected_qty": str(t.rejected_qty), "unit_cost": str(t.unit_cost),
        "needs_reconciliation": t.needs_reconciliation,
    }


class TransferRequestIn(BaseModel):
    item_type: ItemType
    item_id: uuid.UUID
    from_warehouse_id: uuid.UUID
    to_warehouse_id: uuid.UUID
    qty: float = Field(gt=0)
    transfer_date: date | None = None
    remarks: str | None = None


class ReceiveIn(BaseModel):
    received_qty: float | None = None
    rejected_qty: float = 0


def _guard(fn):
    try:
        return fn()
    except TransferError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))


@router.get("/factory")
def list_factory_transfers(status_filter: str | None = None, db: Session = Depends(get_db),
                           user: User = Depends(require_permission("report.read"))):
    stmt = select(FactoryStockTransfer).where(FactoryStockTransfer.company_id == user.company_id)
    if status_filter:
        stmt = stmt.where(FactoryStockTransfer.status == status_filter)
    rows = db.execute(stmt.order_by(FactoryStockTransfer.transfer_date.desc())).scalars().all()
    return [_out(t) for t in rows]


@router.post("/factory", status_code=status.HTTP_201_CREATED)
def create_factory_transfer(p: TransferRequestIn, db: Session = Depends(get_db),
                            user: User = Depends(require_permission("transfer.manage"))):
    t = _guard(lambda: ft.create_transfer_request(
        db, company_id=user.company_id, item_type=p.item_type, item_id=p.item_id,
        from_warehouse_id=p.from_warehouse_id, to_warehouse_id=p.to_warehouse_id,
        qty=p.qty, transfer_date=p.transfer_date, requested_by=user.id, remarks=p.remarks))
    return _out(t)


@router.post("/factory/{transfer_id}/authorize")
def authorize_factory_transfer(transfer_id: uuid.UUID, db: Session = Depends(get_db),
                               user: User = Depends(require_permission("transfer.authorize"))):
    return _out(_guard(lambda: ft.authorize_transfer(db, transfer_id, user.company_id, user.id)))


@router.post("/factory/{transfer_id}/dispatch")
def dispatch_factory_transfer(transfer_id: uuid.UUID, db: Session = Depends(get_db),
                              user: User = Depends(require_permission("transfer.manage"))):
    return _out(_guard(lambda: ft.dispatch_transfer(db, transfer_id, user.company_id, user.id)))


@router.post("/factory/{transfer_id}/receive")
def receive_factory_transfer(transfer_id: uuid.UUID, p: ReceiveIn, db: Session = Depends(get_db),
                             user: User = Depends(require_permission("transfer.manage"))):
    return _out(_guard(lambda: ft.receive_transfer(
        db, transfer_id, user.company_id, user.id,
        received_qty=p.received_qty, rejected_qty=p.rejected_qty)))


@router.post("/factory/{transfer_id}/cancel")
def cancel_factory_transfer(transfer_id: uuid.UUID, db: Session = Depends(get_db),
                            user: User = Depends(require_permission("transfer.manage"))):
    return _out(_guard(lambda: ft.cancel_transfer(db, transfer_id, user.company_id, user.id)))
