"""Stock-valuation vs general-ledger reconciliation (acceptance TEST 17).

For each inventory control account, compare the perpetual inventory valuation
(sum of FIFO layer remaining x unit cost) with the general-ledger balance of the
control account. They tie out because every stock movement posts a GL entry of
equal value through the posting engine (opening stock included, via the
opening-balance voucher).

FABRIC layers back TWO control accounts: greige fabric (1070) and finished
fabric (1085). A single ``ItemType.FABRIC`` filter would conflate them, so the
value of each FABRIC layer is classified by the *actual identity* of the item it
holds, not by item-type alone:

* greige production-order grey fabric (keyed by a ProductionOrder id)  -> 1070
* a FabricRoll's roll, by its kind: GREIGE -> 1070, FINISHED -> 1085
* dyed finished fabric (a DyeingOutput's inventory item)              -> 1085

Finishing (greige->fg) and dyeing both move value from 1070 to 1085 in the GL, so
the sub-ledger split must follow the same boundary for the reconciliation to tie.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.accounting import ChartOfAccount, JournalLine, Voucher, VoucherStatus
from app.models.dyeing_cost import DyeingOutput
from app.models.inventory import ItemType, StockLayer
from app.models.manufacturing import FabricRoll, RollKind
from app.models.masters import Chemical

ZERO = quantize(0, MONEY_SCALE)
_POSTED = [s.value for s in (VoucherStatus.POSTED, VoucherStatus.REVERSED, VoucherStatus.REVERSAL)]

# Non-fabric control accounts -> how to select the stock layers that back them.
GROUPS = [
    {"account": "1100", "label": "Chemical Inventory", "item_type": ItemType.CHEMICAL, "is_dye": False},
    {"account": "1090", "label": "Dye Inventory", "item_type": ItemType.CHEMICAL, "is_dye": True},
    {"account": "1060", "label": "Yarn Inventory", "item_type": ItemType.YARN, "is_dye": None},
]


def _ledger_balance(db: Session, company_id: uuid.UUID, code: str, as_of: date | None) -> object:
    acct = db.execute(
        select(ChartOfAccount).where(ChartOfAccount.company_id == company_id, ChartOfAccount.code == code)
    ).scalar_one_or_none()
    if acct is None:
        return ZERO
    stmt = (
        select(JournalLine.debit, JournalLine.credit)
        .join(Voucher, JournalLine.voucher_id == Voucher.id)
        .where(Voucher.company_id == company_id, Voucher.status.in_(_POSTED),
               JournalLine.account_id == acct.id)
    )
    if as_of is not None:
        stmt = stmt.where(Voucher.voucher_date <= as_of)
    bal = ZERO
    for debit, credit in db.execute(stmt):
        bal += (debit or ZERO) - (credit or ZERO)
    return bal


def _stock_value(db: Session, company_id: uuid.UUID, item_type: ItemType, is_dye) -> object:
    itype = item_type.value
    dye_ids = None
    if is_dye is not None and item_type == ItemType.CHEMICAL:
        dye_ids = {
            c.id for c in db.execute(
                select(Chemical).where(Chemical.company_id == company_id, Chemical.is_dye.is_(is_dye))
            ).scalars()
        }
    value = ZERO
    for layer in db.execute(
        select(StockLayer).where(StockLayer.company_id == company_id, StockLayer.item_type == itype)
    ).scalars():
        if dye_ids is not None and layer.item_id not in dye_ids:
            continue
        value += layer.value_remaining or ZERO
    return value


def _fabric_stock_split(db: Session, company_id: uuid.UUID) -> tuple[object, object]:
    """Split FABRIC sub-ledger value into (greige -> 1070, finished -> 1085) by the
    actual identity of each layer's item, mirroring the GL boundary.

    A FABRIC layer is 'finished' when its item is a dyed-fabric output or a
    FINISHED FabricRoll; otherwise it is greige (grey production-order fabric or a
    GREIGE roll). Classification uses explicit identity sets, not assumptions."""
    finished_ids: set[uuid.UUID] = set()
    # dyed finished fabric: the inventory item of each dyeing output lot
    for o in db.execute(
        select(DyeingOutput.dyed_item_id).where(DyeingOutput.company_id == company_id)
    ).scalars():
        if o is not None:
            finished_ids.add(o)
    # FabricRolls by kind (FINISHED -> 1085); GREIGE rolls stay greige
    for rid, kind in db.execute(
        select(FabricRoll.id, FabricRoll.kind).where(FabricRoll.company_id == company_id)
    ).all():
        k = kind.value if isinstance(kind, RollKind) else kind
        if k == RollKind.FINISHED.value:
            finished_ids.add(rid)

    greige_val = finished_val = ZERO
    for layer in db.execute(
        select(StockLayer).where(StockLayer.company_id == company_id,
                                 StockLayer.item_type == ItemType.FABRIC.value)
    ).scalars():
        if layer.item_id in finished_ids:
            finished_val += layer.value_remaining or ZERO
        else:
            greige_val += layer.value_remaining or ZERO
    return greige_val, finished_val


def stock_ledger_reconciliation(db: Session, company_id: uuid.UUID, *, as_of: date | None = None) -> dict:
    rows = []
    all_ok = True
    tot_stock = tot_ledger = ZERO

    def _row(account, label, stock):
        nonlocal all_ok, tot_stock, tot_ledger
        ledger = _ledger_balance(db, company_id, account, as_of)
        diff = ledger - stock
        ok = diff == ZERO
        all_ok = all_ok and ok
        tot_stock += stock
        tot_ledger += ledger
        rows.append({"account": account, "label": label, "stock_value": stock,
                     "ledger_balance": ledger, "difference": diff, "matched": ok})

    for g in GROUPS:
        _row(g["account"], g["label"], _stock_value(db, company_id, g["item_type"], g["is_dye"]))
    greige_val, finished_val = _fabric_stock_split(db, company_id)
    _row("1070", "Greige Fabric Inventory", greige_val)
    _row("1085", "Finished Fabric Inventory", finished_val)

    return {
        "as_of": as_of,
        "rows": rows,
        "total_stock_value": tot_stock,
        "total_ledger_balance": tot_ledger,
        "total_difference": tot_ledger - tot_stock,
        "is_reconciled": all_ok,
    }
