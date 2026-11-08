"""PO / SO lifecycle: placement -> confirmation -> fulfilment roll-up.

Purchase: a PO moves to PARTIAL then COMPLETED as matched GRNs arrive.
Sales:    an SO moves to PARTIAL then COMPLETED as deliveries dispatch rolls;
          dispatch takes fabric out of stock (FIFO, actual cost) and books a
          balanced COGS voucher, and a roll can't be dispatched twice.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.commercial import DocStatus, GoodsReceiptNote, GRNLine
from app.models.inventory import ItemType
from app.models.manufacturing import KnittingProduction
from app.services import inventory, order_lifecycle as ol
from app.services.manufacturing.knitting import record_production
from app.services.procurement import post_chemical_grn


# ------------------------------- purchase -----------------------------------

def _grn_against(db, demo, po, line, qty):
    grn = GoodsReceiptNote(company_id=demo["company_id"], supplier_id=demo["supplier_id"],
                           number=f"GRN-{qty}", grn_date=date(2026, 11, 8),
                           purchase_order_id=po.id)
    grn.lines.append(GRNLine(
        line_no=1, chemical_id=demo["chemicals"]["RB-01"], chemical_lot_id=demo["lots"]["RB-01"],
        warehouse_id=demo["warehouses"]["chem"], purchase_order_line_id=line.id,
        qty=Decimal(qty), unit_cost=Decimal("1200"), tax_code_id=demo["tax_id"],
    ))
    db.add(grn)
    db.flush()
    post_chemical_grn(db, grn)
    db.commit()
    return grn


def test_po_rolls_to_partial_then_completed(db, demo):
    po = ol.create_purchase_order(
        db, company_id=demo["company_id"], supplier_id=demo["supplier_id"],
        number="PO-001", order_date=date(2026, 11, 8),
        lines=[{"chemical_id": demo["chemicals"]["RB-01"], "qty": "100", "rate": "1200",
                "tax_code_id": demo["tax_id"]}],
    )
    db.commit()
    assert po.status == DocStatus.DRAFT
    ol.confirm_purchase_order(db, po)
    db.commit()
    assert po.status == DocStatus.CONFIRMED

    line = po.lines[0]
    _grn_against(db, demo, po, line, "60")
    db.refresh(po)
    assert po.status == DocStatus.PARTIAL
    assert ol.po_received_map(db, po)[line.id] == Decimal("60.000000")

    _grn_against(db, demo, po, line, "40")
    db.refresh(po)
    assert po.status == DocStatus.COMPLETED
    assert ol.po_received_map(db, po)[line.id] == Decimal("100.000000")


def test_po_cannot_confirm_twice(db, demo):
    po = ol.create_purchase_order(
        db, company_id=demo["company_id"], supplier_id=demo["supplier_id"],
        number="PO-002", order_date=date(2026, 11, 8),
        lines=[{"chemical_id": demo["chemicals"]["RB-01"], "qty": "10"}],
    )
    ol.confirm_purchase_order(db, po)
    db.commit()
    try:
        ol.confirm_purchase_order(db, po)
        assert False, "expected LifecycleError"
    except ol.LifecycleError:
        pass


# --------------------------------- sales ------------------------------------

def _two_greige_rolls(db, demo):
    kp = KnittingProduction(company_id=demo["company_id"], number="KP-SO", machine_id=demo["machines"]["knit"],
                            yarn_id=demo["yarn_id"], yarn_lot_id=demo["yarn_lot_id"],
                            fabric_type_id=demo["fabric_type_id"], production_date=date(2026, 11, 8),
                            produced_kg=Decimal("250"), yarn_consumed_kg=Decimal("255"))
    db.add(kp)
    db.flush()
    res = record_production(db, kp, [
        {"weight_kg": "120", "warehouse_id": demo["warehouses"]["greige"]},
        {"weight_kg": "130", "warehouse_id": demo["warehouses"]["greige"]},
    ])
    db.commit()
    return res["rolls"]


def test_so_dispatch_posts_cogs_and_completes(db, demo):
    so = ol.create_sales_order(
        db, company_id=demo["company_id"], customer_id=demo["customer_id"],
        number="SO-001", order_date=date(2026, 11, 8),
        lines=[{"fabric_type_id": demo["fabric_type_id"], "qty_kg": "250", "rate": "300",
                "tax_code_id": demo["tax_id"]}],
    )
    ol.confirm_sales_order(db, so)
    db.commit()
    assert so.status == DocStatus.CONFIRMED

    rolls = _two_greige_rolls(db, demo)
    before = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC,
                               item_id=rolls[0].id)["qty_on_hand"]

    res = ol.create_delivery(
        db, company_id=demo["company_id"], customer_id=demo["customer_id"],
        sales_order_id=so.id, number="DC-001", delivery_date=date(2026, 11, 8),
        roll_ids=[r.id for r in rolls],
    )
    db.commit()

    voucher = res["voucher"]
    assert voucher.total_debit == voucher.total_credit          # balanced COGS
    assert voucher.total_debit > 0
    assert res["total_qty_kg"] == Decimal("250.000000")

    after = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC,
                              item_id=rolls[0].id)["qty_on_hand"]
    assert before - after == Decimal("120.000000")             # roll 0 fully shipped

    db.refresh(so)
    assert so.status == DocStatus.COMPLETED
    assert ol.so_delivered_kg(db, so) == Decimal("250.000000")


def test_roll_cannot_be_dispatched_twice(db, demo):
    rolls = _two_greige_rolls(db, demo)
    ol.create_delivery(db, company_id=demo["company_id"], customer_id=demo["customer_id"],
                       sales_order_id=None, number="DC-010", delivery_date=date(2026, 11, 8),
                       roll_ids=[rolls[0].id])
    db.commit()
    try:
        ol.create_delivery(db, company_id=demo["company_id"], customer_id=demo["customer_id"],
                           sales_order_id=None, number="DC-011", delivery_date=date(2026, 11, 8),
                           roll_ids=[rolls[0].id])
        assert False, "expected LifecycleError"
    except ol.LifecycleError:
        db.rollback()


def test_delivery_is_idempotent(db, demo):
    rolls = _two_greige_rolls(db, demo)
    r1 = ol.create_delivery(db, company_id=demo["company_id"], customer_id=demo["customer_id"],
                            sales_order_id=None, number="DC-020", delivery_date=date(2026, 11, 8),
                            roll_ids=[rolls[0].id])
    db.commit()
    r2 = ol.create_delivery(db, company_id=demo["company_id"], customer_id=demo["customer_id"],
                            sales_order_id=None, number="DC-020", delivery_date=date(2026, 11, 8),
                            roll_ids=[rolls[1].id])
    db.commit()
    assert r2["already_posted"] is True
    assert r1["voucher"].id == r2["voucher"].id


def test_sales_order_api_roundtrip(client, auth_headers, db, demo):
    body = {"customer_id": str(demo["customer_id"]), "number": "SO-API-1",
            "order_date": "2026-11-08",
            "lines": [{"fabric_type_id": str(demo["fabric_type_id"]), "qty_kg": "100", "rate": "300"}]}
    r = client.post("/api/sales/orders", json=body, headers=auth_headers)
    assert r.status_code == 201, r.text
    so_id = r.json()["id"]
    assert r.json()["status"] == DocStatus.DRAFT.value

    c = client.post(f"/api/sales/orders/{so_id}/confirm", headers=auth_headers)
    assert c.status_code == 200 and c.json()["status"] == DocStatus.CONFIRMED.value

    lst = client.get("/api/sales/orders", headers=auth_headers).json()
    assert any(s["id"] == so_id for s in lst)
