"""Yarn lot receipt: create a yarn lot, add costed FIFO stock, and post the
matching accounting entry — the yarn counterpart to a chemical GRN."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.inventory import ItemType
from app.models.masters import YarnLot
from app.services import inventory
from app.services.accounting import posting_engine as pe


def receive_yarn_lot(db: Session, company_id: uuid.UUID, *, yarn_id, internal_lot_no, qty, unit_cost,
                     supplier_id=None, supplier_lot_no=None, warehouse_id=None,
                     received_date: date | None = None, created_by=None):
    """Receive a yarn lot into stock. Idempotent on the lot number.

    Posts Dr Yarn Inventory / Cr Accounts Payable when a supplier is given
    (a purchase), else Cr Owner's Capital (an opening/manual receipt)."""
    ref = f"YARNLOT:{internal_lot_no}"
    existing = pe.find_existing(db, company_id, ref)
    if existing is not None:
        return None, existing

    rdate = received_date or date.today()
    lot = YarnLot(company_id=company_id, yarn_id=yarn_id, supplier_id=supplier_id,
                  internal_lot_no=internal_lot_no, supplier_lot_no=supplier_lot_no,
                  warehouse_id=warehouse_id, received_date=rdate.isoformat(),
                  unit_cost=quantize(unit_cost, MONEY_SCALE), created_by=created_by)
    db.add(lot)
    db.flush()

    inventory.receive(db, company_id=company_id, item_type=ItemType.YARN, item_id=yarn_id,
                      qty=qty, unit_cost=unit_cost, lot_id=lot.id, warehouse_id=warehouse_id,
                      event_type="YARN_GRN", source_module="yarn_lot", source_id=str(lot.id),
                      posting_reference=ref)

    value = quantize(Decimal(str(qty)) * Decimal(str(unit_cost)), MONEY_SCALE)
    credit_account = "2010" if supplier_id else "3010"
    voucher = pe.post_voucher(db, pe.PostingRequest(
        company_id=company_id, voucher_type_code=("PI" if supplier_id else "OB"), voucher_date=rdate,
        posting_reference=ref, narration=f"Yarn lot {internal_lot_no}",
        source_module="yarn_lot", source_id=str(lot.id), created_by=created_by,
        lines=[pe.PostingLine("1060", debit=value, narration="Yarn inventory"),
               pe.PostingLine(credit_account, credit=value, narration="Yarn lot received",
                              party_type="supplier" if supplier_id else None, party_id=supplier_id)],
    ))
    return lot, voucher
