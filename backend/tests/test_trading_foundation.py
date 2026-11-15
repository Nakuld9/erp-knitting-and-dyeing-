"""Stage 4B-1 — trading-fabric identity, item master and warehouse isolation.

Foundation only: SKU master CRUD + validation, the distinct TRADING_FABRIC
inventory identity, and the strict trading-warehouse validator. No trading GRN,
purchase, sale, dispatch, return or financial posting is exercised (none exists).
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.core import Company
from app.models.hybrid import WarehouseCategory
from app.models.inventory import ItemType, StockLayer
from app.models.manufacturing import FabricRoll
from app.models.genealogy import RollGenealogyEdge
from app.models.masters import FabricType, Warehouse
from app.models.core import Branch
from app.models.trading import TradingFabricItem
from app.services import inventory, trading


# ------------------------------- helpers ------------------------------------

def _second_company(db):
    c = Company(code=f"OTHER-{uuid.uuid4().hex[:5]}", name="Other Co")
    db.add(c); db.flush()
    return c


def _wh(db, company_id, code, category, *, branch_id=None):
    w = Warehouse(company_id=company_id, code=code, name=code, kind="STORE",
                  category=category, branch_id=branch_id)
    db.add(w); db.flush()
    return w


# --------------------------- item master: CRUD ------------------------------

def test_create_get_update_list_deactivate(db, demo):
    cid = demo["company_id"]
    item = trading.create_trading_item(db, cid, code="TF-001", name="Imported Lycra",
                                       fabric_type_id=demo["fabric_type_id"], gsm=Decimal("180"),
                                       width_cm=Decimal("150"), colour="Navy")
    db.commit()
    assert item.id and item.is_active and item.code == "TF-001"

    got = trading.get_trading_item(db, cid, item.id)
    assert got.name == "Imported Lycra" and got.colour == "Navy"

    trading.update_trading_item(db, cid, item.id, changes={"name": "Imported Lycra 2", "colour": "Black"})
    db.commit()
    assert trading.get_trading_item(db, cid, item.id).name == "Imported Lycra 2"

    trading.create_trading_item(db, cid, code="TF-002", name="Polyester Mesh")
    db.commit()
    rows = trading.list_trading_items(db, cid)
    assert {r.code for r in rows} >= {"TF-001", "TF-002"}

    trading.set_trading_item_active(db, cid, item.id, active=False)
    db.commit()
    assert trading.get_trading_item(db, cid, item.id).is_active is False
    assert all(r.code != "TF-001" for r in trading.list_trading_items(db, cid, active_only=True))


def test_update_cannot_change_activation(db, demo):
    """A general update must never flip is_active — only set_trading_item_active
    (the /active endpoint) may. An is_active key in the update is ignored."""
    cid = demo["company_id"]
    item = trading.create_trading_item(db, cid, code="TF-ACT", name="Active One")
    db.commit()
    assert item.is_active is True
    # attempt to deactivate via a general update — must be ignored
    trading.update_trading_item(db, cid, item.id, changes={"name": "Still Active", "is_active": False})
    db.commit()
    refreshed = trading.get_trading_item(db, cid, item.id)
    assert refreshed.is_active is True          # unchanged by update
    assert refreshed.name == "Still Active"     # other fields did update
    # the dedicated path still works
    trading.set_trading_item_active(db, cid, item.id, active=False)
    db.commit()
    assert trading.get_trading_item(db, cid, item.id).is_active is False


def test_company_scoped_code_uniqueness(db, demo):
    cid = demo["company_id"]
    trading.create_trading_item(db, cid, code="DUP", name="A")
    db.commit()
    with pytest.raises(trading.TradingItemError):
        trading.create_trading_item(db, cid, code="DUP", name="B")
    db.rollback()
    # the SAME code is allowed in a DIFFERENT company (company-scoped uniqueness)
    other = _second_company(db)
    item = trading.create_trading_item(db, other.id, code="DUP", name="C")
    db.commit()
    assert item.id


def test_cross_company_fabric_type_rejected(db, demo):
    cid = demo["company_id"]
    other = _second_company(db)
    ft = FabricType(company_id=other.id, code="FT-X", name="Other FT")
    db.add(ft); db.flush()
    with pytest.raises(trading.TradingItemError):
        trading.create_trading_item(db, cid, code="TF-XC", name="x", fabric_type_id=ft.id)
    db.rollback()


def test_invalid_tax_code_rejected(db, demo):
    cid = demo["company_id"]
    with pytest.raises(trading.TradingItemError):
        trading.create_trading_item(db, cid, code="TF-TAX", name="x", tax_code_id=uuid.uuid4())
    db.rollback()


def test_blank_code_rejected(db, demo):
    with pytest.raises(trading.TradingItemError):
        trading.create_trading_item(db, demo["company_id"], code="  ", name="x")
    db.rollback()


# --------------------------- inventory identity -----------------------------

def test_trading_identity_distinct_from_fabric(db, demo):
    assert ItemType.TRADING_FABRIC.value == "TRADE_FABRIC"
    assert ItemType.TRADING_FABRIC is not ItemType.FABRIC
    assert ItemType.TRADING_FABRIC.value != ItemType.FABRIC.value


def test_trading_stock_independently_queryable_and_excluded_from_fabric(db, demo):
    cid = demo["company_id"]
    item = trading.create_trading_item(db, cid, code="TF-INV", name="Trade Stock")
    wh = _wh(db, cid, "WH-TRADE", WarehouseCategory.TRADING.value)
    db.commit()
    # Receive aggregate trading stock through the generic engine.
    inventory.receive(db, company_id=cid, item_type=ItemType.TRADING_FABRIC, item_id=item.id,
                      qty="500", unit_cost="120", warehouse_id=wh.id, event_type="GRN")
    db.commit()
    # Queryable by its own identity.
    bal = inventory.balance(db, company_id=cid, item_type=ItemType.TRADING_FABRIC, item_id=item.id)
    assert bal["qty_on_hand"] == Decimal("500.000000")
    # Manufactured-FABRIC balance for the same id is empty (distinct identity).
    fab = inventory.balance(db, company_id=cid, item_type=ItemType.FABRIC, item_id=item.id)
    assert fab["qty_on_hand"] == Decimal("0.000000")
    # A manufactured-FABRIC layer query never returns the trading layer.
    fab_layers = db.execute(select(StockLayer).where(
        StockLayer.company_id == cid, StockLayer.item_type == ItemType.FABRIC.value)).scalars().all()
    assert all(l.item_id != item.id for l in fab_layers)


def test_trading_stock_on_hand_not_grouped_as_fabric(db, demo):
    cid = demo["company_id"]
    item = trading.create_trading_item(db, cid, code="TF-SOH", name="SOH Trade")
    wh = _wh(db, cid, "WH-TR2", WarehouseCategory.TRADING.value)
    db.commit()
    inventory.receive(db, company_id=cid, item_type=ItemType.TRADING_FABRIC, item_id=item.id,
                      qty="100", unit_cost="80", warehouse_id=wh.id, event_type="GRN")
    db.commit()
    rows = inventory.stock_on_hand(db, cid)
    trading_rows = [r for r in rows if r["item_type"] == ItemType.TRADING_FABRIC.value]
    assert len(trading_rows) == 1
    assert trading_rows[0]["item_code"] == "TF-SOH"
    # It must NOT appear under a manufactured FABRIC grouping.
    assert all(r["item_type"] != ItemType.FABRIC.value or "Trade" not in r["item_name"] for r in rows)


def test_trading_item_creates_no_roll_lot_or_genealogy(db, demo):
    cid = demo["company_id"]
    rolls_before = db.execute(select(FabricRoll).where(FabricRoll.company_id == cid)).scalars().all()
    edges_before = db.execute(select(RollGenealogyEdge).where(RollGenealogyEdge.company_id == cid)).scalars().all()
    item = trading.create_trading_item(db, cid, code="TF-NOROLL", name="No Roll")
    wh = _wh(db, cid, "WH-TR3", WarehouseCategory.TRADING.value)
    db.commit()
    inventory.receive(db, company_id=cid, item_type=ItemType.TRADING_FABRIC, item_id=item.id,
                      qty="10", unit_cost="5", warehouse_id=wh.id, event_type="GRN")
    db.commit()
    rolls_after = db.execute(select(FabricRoll).where(FabricRoll.company_id == cid)).scalars().all()
    edges_after = db.execute(select(RollGenealogyEdge).where(RollGenealogyEdge.company_id == cid)).scalars().all()
    assert len(rolls_after) == len(rolls_before)
    assert len(edges_after) == len(edges_before)
    assert db.get(FabricRoll, item.id) is None   # the trading SKU id is not a roll


# --------------------------- warehouse validator ----------------------------

def test_trading_warehouse_accepts_trading(db, demo):
    cid = demo["company_id"]
    wh = _wh(db, cid, "WH-OK", WarehouseCategory.TRADING.value)
    db.commit()
    assert trading.assert_trading_warehouse(db, cid, wh.id).id == wh.id


@pytest.mark.parametrize("cat", ["GREIGE", "SEMI_FINISHED", "FINISHED_GOODS", "YARN_STORE", None])
def test_trading_warehouse_rejects_non_trading(db, demo, cat):
    cid = demo["company_id"]
    wh = _wh(db, cid, f"WH-{cat or 'NONE'}", cat)
    db.commit()
    with pytest.raises(trading.TradingWarehouseError):
        trading.assert_trading_warehouse(db, cid, wh.id)


def test_trading_warehouse_rejects_missing(db, demo):
    with pytest.raises(trading.TradingWarehouseError):
        trading.assert_trading_warehouse(db, demo["company_id"], None)
    with pytest.raises(trading.TradingWarehouseError):
        trading.assert_trading_warehouse(db, demo["company_id"], uuid.uuid4())


def test_trading_warehouse_rejects_wrong_company(db, demo):
    other = _second_company(db)
    wh = _wh(db, other.id, "WH-OTHER", WarehouseCategory.TRADING.value)
    db.commit()
    with pytest.raises(trading.TradingWarehouseError):
        trading.assert_trading_warehouse(db, demo["company_id"], wh.id)


def test_trading_warehouse_rejects_wrong_factory(db, demo):
    cid = demo["company_id"]
    b1 = Branch(company_id=cid, code="F1", name="Factory 1")
    b2 = Branch(company_id=cid, code="F2", name="Factory 2")
    db.add_all([b1, b2]); db.flush()
    wh = _wh(db, cid, "WH-F1", WarehouseCategory.TRADING.value, branch_id=b1.id)
    db.commit()
    assert trading.assert_trading_warehouse(db, cid, wh.id, factory_id=b1.id).id == wh.id
    with pytest.raises(trading.TradingWarehouseError):
        trading.assert_trading_warehouse(db, cid, wh.id, factory_id=b2.id)


# --------------------------- accounting foundation --------------------------

def test_trading_posting_rules_unconfigured(db, demo):
    from app.services.accounting.posting_rules import trading_rules_configured, DEFAULT_RULES
    # No trading event has a built-in default (nothing can silently post).
    assert "TRADING_GRN" not in DEFAULT_RULES
    status = trading_rules_configured(db, demo["company_id"])
    assert set(status) >= {"TRADING_GRN", "TRADING_DELIVERY", "TRADING_PURCHASE_RETURN",
                           "TRADING_SALES_RETURN", "TRADING_ADJUSTMENT"}
    # Every slot is currently unconfigured (CA must set the account codes).
    assert all(v is False for slots in status.values() for v in slots.values())
