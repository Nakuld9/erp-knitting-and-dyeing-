"""Same-company factory-to-factory stock transfer (goods-in-transit workflow).

GL-neutral: no sales/purchase invoice is created between factories of one company
(business rule). Lifecycle:

    REQUESTED --authorize--> AUTHORIZED --dispatch--> DISPATCHED --receive--> RECEIVED

- dispatch consumes the quantity from the source warehouse at FIFO cost; the value
  is "in transit" (left the source, not yet at the destination) and held on the
  transfer record.
- receive puts the received quantity into the destination warehouse at the
  dispatched unit cost. A quantity shortage (dispatched - received) and any
  quality rejection are recorded and the transfer is flagged
  ``needs_reconciliation`` — transit-loss accounting is a business decision and is
  never silently posted here.

Maker-checker: the actor who requested a transfer cannot authorize it.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.audit import DocumentSequence  # noqa: F401 (sequence table)
from app.models.core import Branch
from app.models.inventory import ItemType
from app.models.masters import Warehouse
from app.models.transfers import FactoryStockTransfer, TransferStatus
from app.services.accounting.posting_engine import fiscal_year_for
from app.services.inventory.service import consume, receive
from app.services.sequences import next_document_number

ZERO = Decimal("0")


class TransferError(ValueError):
    """Invalid factory-transfer request or state transition."""


def _warehouse_in_factory(db: Session, warehouse_id: uuid.UUID, company_id: uuid.UUID,
                          branch_id: uuid.UUID, label: str) -> Warehouse:
    wh = db.get(Warehouse, warehouse_id)
    if wh is None or wh.company_id != company_id:
        raise TransferError(f"{label} warehouse not found in this company")
    if wh.branch_id != branch_id:
        raise TransferError(f"{label} warehouse does not belong to the stated factory")
    return wh


def create_transfer_request(db: Session, *, company_id: uuid.UUID, item_type: ItemType,
                            item_id: uuid.UUID, from_warehouse_id: uuid.UUID,
                            to_warehouse_id: uuid.UUID, qty, transfer_date: date | None = None,
                            requested_by: uuid.UUID | None = None,
                            remarks: str | None = None) -> FactoryStockTransfer:
    if from_warehouse_id == to_warehouse_id:
        raise TransferError("Source and destination warehouses must differ")
    if Decimal(str(qty)) <= ZERO:
        raise TransferError("Transfer quantity must be positive")
    src = db.get(Warehouse, from_warehouse_id)
    dst = db.get(Warehouse, to_warehouse_id)
    if src is None or dst is None or src.company_id != company_id or dst.company_id != company_id:
        raise TransferError("Both warehouses must belong to this company")
    if src.branch_id is None or dst.branch_id is None:
        raise TransferError("Both warehouses must be assigned to a factory (branch)")
    if src.branch_id == dst.branch_id:
        raise TransferError("A factory transfer requires two different factories; "
                            "use a plain warehouse transfer within one factory")
    tdate = transfer_date or date.today()
    number = next_document_number(db, company_id=company_id, doc_type="FTR",
                                  fiscal_year=fiscal_year_for(tdate), prefix="FTR")
    t = FactoryStockTransfer(
        company_id=company_id, number=number, transfer_date=tdate,
        from_branch_id=src.branch_id, to_branch_id=dst.branch_id,
        from_warehouse_id=from_warehouse_id, to_warehouse_id=to_warehouse_id,
        item_type=item_type.value if isinstance(item_type, ItemType) else item_type,
        item_id=item_id, requested_qty=qty, status=TransferStatus.REQUESTED,
        requested_by=requested_by, created_by=requested_by, remarks=remarks)
    db.add(t)
    db.commit()
    return t


def _get(db: Session, transfer_id: uuid.UUID, company_id: uuid.UUID) -> FactoryStockTransfer:
    t = db.get(FactoryStockTransfer, transfer_id)
    if t is None or t.company_id != company_id:
        raise TransferError("Transfer not found")
    return t


def authorize_transfer(db: Session, transfer_id: uuid.UUID, company_id: uuid.UUID,
                       actor_id: uuid.UUID | None) -> FactoryStockTransfer:
    t = _get(db, transfer_id, company_id)
    if TransferStatus(t.status) != TransferStatus.REQUESTED:
        raise TransferError(f"Only a REQUESTED transfer can be authorized (is {t.status})")
    if actor_id is not None and t.requested_by is not None and actor_id == t.requested_by:
        raise TransferError("The requester cannot authorize their own transfer (maker-checker)")
    t.status = TransferStatus.AUTHORIZED
    t.authorized_by = actor_id
    t.authorized_at = datetime.now(timezone.utc)
    db.commit()
    return t


def dispatch_transfer(db: Session, transfer_id: uuid.UUID, company_id: uuid.UUID,
                      actor_id: uuid.UUID | None, method: str = "FIFO") -> FactoryStockTransfer:
    t = _get(db, transfer_id, company_id)
    if TransferStatus(t.status) != TransferStatus.AUTHORIZED:
        raise TransferError(f"Only an AUTHORIZED transfer can be dispatched (is {t.status})")
    itype = ItemType(t.item_type)
    res = consume(db, company_id=company_id, item_type=itype, item_id=t.item_id,
                  qty=t.requested_qty, warehouse_id=t.from_warehouse_id, method=method,
                  event_type="FACTORY_TRANSFER_OUT", source_module="factory_transfer",
                  source_id=str(t.id))
    moved = Decimal(str(res["qty"]))
    cost = Decimal(str(res["total_cost"]))
    t.dispatched_qty = moved
    t.dispatched_cost = cost
    t.unit_cost = quantize(cost / moved, MONEY_SCALE) if moved > ZERO else ZERO
    t.status = TransferStatus.DISPATCHED
    t.dispatched_by = actor_id
    t.dispatched_at = datetime.now(timezone.utc)
    db.commit()
    return t


def receive_transfer(db: Session, transfer_id: uuid.UUID, company_id: uuid.UUID,
                     actor_id: uuid.UUID | None, received_qty=None, rejected_qty=0) -> FactoryStockTransfer:
    t = _get(db, transfer_id, company_id)
    if TransferStatus(t.status) != TransferStatus.DISPATCHED:
        raise TransferError(f"Only a DISPATCHED transfer can be received (is {t.status})")
    dispatched = Decimal(str(t.dispatched_qty))
    recv = dispatched if received_qty is None else Decimal(str(received_qty))
    rej = Decimal(str(rejected_qty or 0))
    if recv < ZERO or rej < ZERO:
        raise TransferError("Received and rejected quantities cannot be negative")
    if recv > dispatched:
        raise TransferError("Received quantity cannot exceed the dispatched quantity")
    if rej > recv:
        raise TransferError("Rejected quantity cannot exceed the received quantity")

    # physically received (accepted) goes into the destination warehouse
    accepted = recv - rej
    if accepted > ZERO:
        receive(db, company_id=company_id, item_type=ItemType(t.item_type), item_id=t.item_id,
                qty=accepted, unit_cost=t.unit_cost, warehouse_id=t.to_warehouse_id,
                event_type="FACTORY_TRANSFER_IN", source_module="factory_transfer",
                source_id=str(t.id))
    t.received_qty = recv
    t.rejected_qty = rej
    t.shortage_qty = dispatched - recv
    t.needs_reconciliation = (t.shortage_qty > ZERO) or (rej > ZERO)
    t.status = TransferStatus.RECEIVED
    t.received_by = actor_id
    t.received_at = datetime.now(timezone.utc)
    db.commit()
    return t


def cancel_transfer(db: Session, transfer_id: uuid.UUID, company_id: uuid.UUID,
                    actor_id: uuid.UUID | None) -> FactoryStockTransfer:
    t = _get(db, transfer_id, company_id)
    if TransferStatus(t.status) in (TransferStatus.DISPATCHED, TransferStatus.RECEIVED):
        raise TransferError("Cannot cancel a transfer that has already been dispatched")
    t.status = TransferStatus.CANCELLED
    db.commit()
    return t
