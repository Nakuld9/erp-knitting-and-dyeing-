"""HIGH-2: yarn issue GL timing + production-order cancellation with material
return and balanced GL reversal.

Scenarios:
  1. yarn issued but grey output not yet valued -> sub-ledger == GL
  2. in-process reconciliation holds
  3. cancel before any issue
  4. cancel after partial issue
  5. cancel after complete issue
  6. material return + GL reversal recorded (audit trail preserved)
  7. duplicate / concurrent cancel rejected
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.accounting import Voucher, VoucherStatus
from app.models.fabric_bom import ProductionOrderStatus, ReservationStatus
from app.models.inventory import InventoryTransaction, ItemType
from app.services.accounting.stock_reconciliation import stock_ledger_reconciliation
from app.services.accounting.trial_balance import trial_balance
from app.services.bom import production, service as bom
from app.services.inventory import service as inventory

VD = date(2026, 6, 15)


def _order(db, demo, *, code, qty="100"):
    b = bom.create_bom(db, company_id=demo["company_id"], code=code, name=code)
    v = b.versions[0]
    bom.set_yarns(db, v.id, [{"yarn_id": demo["yarn_id"], "blend_percent": Decimal("100"),
                              "std_consumption_per_kg": Decimal("1.0"), "std_unit_cost": Decimal("240")}])
    bom.approve_version(db, version_id=v.id, approved_by=None)
    o = production.create_order(db, company_id=demo["company_id"], bom_version_id=v.id,
                               planned_qty_kg=Decimal(qty), number="PO-"+code, order_date=VD)
    return o, o.reservations[0]


def _yarn_bal(db, demo):
    return inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.YARN,
                             item_id=demo["yarn_id"])["value"]


def test_issue_posts_yarn_gl_and_reconciles_in_process(db, demo):
    """(1)+(2): after issuing yarn (grey NOT valued), yarn sub-ledger and GL both
    drop by the same amount and the stock reconciliation holds."""
    before = _yarn_bal(db, demo)
    o, r = _order(db, demo, code="CAN1")
    production.issue_material(db, order_id=o.id, reservation_id=r.id, qty=r.planned_qty, voucher_date=VD)
    after = _yarn_bal(db, demo)
    assert before - after == Decimal("24000.00")           # 100kg * 240 consumed
    # the issue posted Dr WIP / Cr Yarn -> GL 1060 reduced by the same amount
    rec = stock_ledger_reconciliation(db, demo["company_id"])
    assert rec["is_reconciled"] is True, rec
    assert trial_balance(db, demo["company_id"])["is_balanced"]


def test_cancel_before_issue(db, demo):
    """(3): cancel with nothing issued -> reservations released, no GL, no return."""
    o, r = _order(db, demo, code="CAN2")
    before = _yarn_bal(db, demo)
    o = production.cancel_order(db, order_id=o.id, voucher_date=VD)
    assert o.status == ProductionOrderStatus.CANCELLED
    db.refresh(r)
    assert r.status == ReservationStatus.RELEASED
    assert _yarn_bal(db, demo) == before                   # nothing returned, nothing consumed
    # no issue/return vouchers for this order
    assert db.execute(select(Voucher).where(
        Voucher.company_id == demo["company_id"],
        Voucher.posting_reference.like(f"PO:{o.number}:%"))).first() is None


def test_cancel_after_partial_issue(db, demo):
    """(4): cancel after a partial issue returns the issued qty and reverses GL."""
    o, r = _order(db, demo, code="CAN3", qty="100")
    before = _yarn_bal(db, demo)
    production.issue_material(db, order_id=o.id, reservation_id=r.id, qty=Decimal("40"), voucher_date=VD)
    assert before - _yarn_bal(db, demo) == Decimal("9600.00")   # 40 * 240
    production.cancel_order(db, order_id=o.id, voucher_date=VD)
    assert _yarn_bal(db, demo) == before                   # fully restored
    db.refresh(r)
    assert r.status == ReservationStatus.RELEASED and Decimal(str(r.issued_qty)) == Decimal("0")
    rec = stock_ledger_reconciliation(db, demo["company_id"])
    assert rec["is_reconciled"] is True
    assert trial_balance(db, demo["company_id"])["is_balanced"]


def test_cancel_after_complete_issue_returns_and_reverses(db, demo):
    """(5)+(6): cancel after full issue restores stock + GL; the return inventory
    movement and the REVERSAL voucher are recorded (audit trail preserved)."""
    o, r = _order(db, demo, code="CAN4", qty="100")
    before = _yarn_bal(db, demo)
    production.issue_material(db, order_id=o.id, reservation_id=r.id, qty=r.planned_qty, voucher_date=VD)
    production.cancel_order(db, order_id=o.id, voucher_date=VD)
    assert _yarn_bal(db, demo) == before
    # (6) a PRODUCTION_RETURN inventory IN movement exists
    ret = db.execute(select(InventoryTransaction).where(
        InventoryTransaction.company_id == demo["company_id"],
        InventoryTransaction.event_type == "PRODUCTION_RETURN",
        InventoryTransaction.source_id == str(o.id))).first()
    assert ret is not None
    # (6) the issue voucher was REVERSED and a REVERSAL voucher posted
    iv = db.execute(select(Voucher).where(
        Voucher.company_id == demo["company_id"],
        Voucher.posting_reference.like(f"PO:{o.number}:issue:%"))).scalar_one()
    assert VoucherStatus(iv.status) == VoucherStatus.REVERSED
    rev = db.execute(select(Voucher).where(
        Voucher.company_id == demo["company_id"],
        Voucher.reversal_of_id == iv.id)).scalar_one()
    assert rev.total_debit == iv.total_credit              # swapped & balanced
    assert trial_balance(db, demo["company_id"])["is_balanced"]
    rec = stock_ledger_reconciliation(db, demo["company_id"])
    assert rec["is_reconciled"] is True


def test_duplicate_cancel_rejected(db, demo):
    """(7): a second cancel is rejected by the status guard (no double reversal)."""
    o, r = _order(db, demo, code="CAN5")
    production.issue_material(db, order_id=o.id, reservation_id=r.id, qty=r.planned_qty, voucher_date=VD)
    production.cancel_order(db, order_id=o.id, voucher_date=VD)
    with pytest.raises(production.ProductionError):
        production.cancel_order(db, order_id=o.id, voucher_date=VD)


def test_cannot_cancel_after_grey_valued(db, demo):
    """Grey fabric already exists in inventory -> cancel is refused."""
    o, r = _order(db, demo, code="CAN6")
    production.issue_material(db, order_id=o.id, reservation_id=r.id, qty=r.planned_qty, voucher_date=VD)
    production.complete_order(db, order_id=o.id, produced_qty_kg=Decimal("100"))
    production.value_grey_output(db, order_id=o.id, voucher_date=VD)
    with pytest.raises(production.ProductionError):
        production.cancel_order(db, order_id=o.id, voucher_date=VD)
