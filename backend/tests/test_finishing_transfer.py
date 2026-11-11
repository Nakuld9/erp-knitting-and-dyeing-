"""Finishing moves a roll's cost greige -> finished goods in the GL.

Without this, a finished roll's value stays in the greige account while
dispatch credits finished-goods inventory, so the sub-ledger stops tying to the
control accounts. The transfer keeps each fabric account reconciled to the
rolls it actually holds.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models.accounting import ChartOfAccount, JournalLine, Voucher
from app.models.inventory import ItemType
from app.models.manufacturing import KnittingProduction, RollKind
from app.services import inventory, quality
from app.services.manufacturing.knitting import record_production


def _acct_net(db, company_id, code):
    """Net debit-minus-credit movement on an account code."""
    acc = db.execute(select(ChartOfAccount).where(
        ChartOfAccount.company_id == company_id, ChartOfAccount.code == code)).scalar_one()
    rows = db.execute(select(JournalLine.debit, JournalLine.credit)
                      .join(Voucher, JournalLine.voucher_id == Voucher.id)
                      .where(JournalLine.account_id == acc.id)).all()
    return sum((Decimal(str(d)) - Decimal(str(c)) for d, c in rows), Decimal("0"))


def _one_greige_roll(db, demo, weight="100"):
    kp = KnittingProduction(company_id=demo["company_id"], number="KP-FIN", machine_id=demo["machines"]["knit"],
                            yarn_id=demo["yarn_id"], yarn_lot_id=demo["yarn_lot_id"],
                            fabric_type_id=demo["fabric_type_id"], production_date=date(2026, 11, 8),
                            produced_kg=Decimal(weight), yarn_consumed_kg=Decimal("102"))
    db.add(kp); db.flush()
    res = record_production(db, kp, [{"weight_kg": weight, "warehouse_id": demo["warehouses"]["greige"]}])
    db.commit()
    return res["rolls"][0]


def test_finishing_transfers_greige_to_fg(db, demo):
    roll = _one_greige_roll(db, demo)
    value = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC,
                              item_id=roll.id)["value"]
    assert value > 0

    greige_before = _acct_net(db, demo["company_id"], "1070")
    fg_before = _acct_net(db, demo["company_id"], "1085")

    quality.record_finishing(db, demo["company_id"], process_type="COMPACTING",
                             process_date=date(2026, 11, 8), fabric_roll_id=roll.id)
    db.commit()
    db.refresh(roll)
    assert roll.kind == RollKind.FINISHED

    greige_after = _acct_net(db, demo["company_id"], "1070")
    fg_after = _acct_net(db, demo["company_id"], "1085")
    assert greige_before - greige_after == value     # greige reduced by the roll's cost
    assert fg_after - fg_before == value             # finished goods increased by it


def test_finishing_transfer_is_idempotent(db, demo):
    roll = _one_greige_roll(db, demo)
    quality.record_finishing(db, demo["company_id"], process_type="COMPACTING",
                             process_date=date(2026, 11, 8), fabric_roll_id=roll.id)
    db.commit()
    fg_after_first = _acct_net(db, demo["company_id"], "1085")
    # Finishing the same roll again must not transfer twice.
    quality.record_finishing(db, demo["company_id"], process_type="HEAT_SETTING",
                             process_date=date(2026, 11, 9), fabric_roll_id=roll.id)
    db.commit()
    assert _acct_net(db, demo["company_id"], "1085") == fg_after_first
