"""END-TO-END acceptance: yarn purchase -> GRN -> FIFO yarn issue -> knitting ->
grey fabric receipt -> dyeing -> multiple dyed output lots -> sales order ->
dispatch -> COGS -> stock-vs-GL reconciliation.

At every stage we verify physical quantity, FIFO inventory value, WIP, grey and
dyed fabric value, GL account balances, and stock-vs-GL reconciliation.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.inventory import ItemType
from app.models.dyeing_cost import DyeingOutput
from app.services import order_lifecycle as ol
from app.services import yarn as yarn_svc
from app.services.accounting.stock_reconciliation import (
    _ledger_balance, stock_ledger_reconciliation)
from app.services.accounting.trial_balance import trial_balance
from app.services.accounting import production_reconciliation as prec
from app.services.bom import production, service as bom
from app.services.dyeing import service as dyeing
from app.services.inventory import service as inventory
from sqlalchemy import select

VD = date(2026, 6, 15)


def _gl(db, cid, code):
    return _ledger_balance(db, cid, code, None)


def _assert_reconciled(db, cid):
    rec = stock_ledger_reconciliation(db, cid)
    assert rec["is_reconciled"] is True, rec
    assert trial_balance(db, cid)["is_balanced"] is True
    return rec


def test_full_yarn_to_dyed_sale(db, demo):
    cid = demo["company_id"]
    yarn_id = demo["yarn_id"]

    # ---- 0. baseline reconciles (demo opening stock) ----
    _assert_reconciled(db, cid)
    yarn_gl0 = _gl(db, cid, "1060")
    yarn_qty0 = inventory.balance(db, company_id=cid, item_type=ItemType.YARN, item_id=yarn_id)["qty_on_hand"]

    # ---- 1. yarn purchase -> GRN -> yarn inventory ----
    _lot, _v = yarn_svc.receive_yarn_lot(
        db, cid, yarn_id=yarn_id, internal_lot_no="YL-E2E", qty=Decimal("200"),
        unit_cost=Decimal("240"), supplier_id=demo["supplier_id"], received_date=VD)
    bal = inventory.balance(db, company_id=cid, item_type=ItemType.YARN, item_id=yarn_id)
    assert bal["qty_on_hand"] == yarn_qty0 + Decimal("200.000000")
    assert _gl(db, cid, "1060") == yarn_gl0 + Decimal("48000.00")      # 200*240
    _assert_reconciled(db, cid)

    # ---- 2. grey BOM + order + FIFO yarn issue ----
    b = bom.create_bom(db, company_id=cid, code="E2E", name="E2E")
    v = b.versions[0]
    bom.set_yarns(db, v.id, [{"yarn_id": yarn_id, "blend_percent": Decimal("100"),
                              "std_consumption_per_kg": Decimal("1.0"), "std_unit_cost": Decimal("240")}])
    bom.approve_version(db, version_id=v.id, approved_by=None)
    o = production.create_order(db, company_id=cid, bom_version_id=v.id,
                               planned_qty_kg=Decimal("100"), number="PO-E2E", order_date=VD)
    r = o.reservations[0]
    yarn_gl_pre = _gl(db, cid, "1060")
    production.issue_material(db, order_id=o.id, reservation_id=r.id, qty=Decimal("100"), voucher_date=VD)
    # physical + FIFO value + GL all move together; WIP holds the yarn
    bal = inventory.balance(db, company_id=cid, item_type=ItemType.YARN, item_id=yarn_id)
    assert bal["qty_on_hand"] == yarn_qty0 + Decimal("100.000000")     # +200 GRN -100 issued
    assert _gl(db, cid, "1060") == yarn_gl_pre - Decimal("24000.00")   # 100*240 (FIFO from opening @240)
    assert _gl(db, cid, "1080") == Decimal("24000.00")                 # WIP holds yarn
    _assert_reconciled(db, cid)

    # ---- 3. knitting complete + grey fabric receipt ----
    production.complete_order(db, order_id=o.id, produced_qty_kg=Decimal("100"))
    production.record_actual_knitting_conversion_cost(db, order_id=o.id, amount=Decimal("600"))
    production.value_grey_output(db, order_id=o.id, voucher_date=VD)
    grey_bal = inventory.balance(db, company_id=cid, item_type=ItemType.FABRIC, item_id=o.id)
    assert grey_bal["qty_on_hand"] == Decimal("100.000000")
    assert grey_bal["value"] == Decimal("24600.00")                    # yarn 24000 + conversion 600
    assert _gl(db, cid, "1070") == Decimal("24600.00")                 # greige inventory
    assert _gl(db, cid, "1080") == Decimal("0.00")                     # WIP cleared
    _assert_reconciled(db, cid)

    # ---- 4. dyeing: grey in, costs, multiple dyed output lots ----
    job = dyeing.create_job(db, company_id=cid, number="DJ-E2E", mode="INTERNAL",
                            grey_production_order_id=o.id, shade_id=demo["shade_id"])
    dyeing.issue_grey(db, job_id=job.id, source_production_order_id=o.id, qty_kg=Decimal("100"), voucher_date=VD)
    assert _gl(db, cid, "1070") == Decimal("0.00")                     # greige consumed into dyeing
    dyeing.record_costs(db, job_id=job.id, dyeing_charge=Decimal("3000"), chemical_cost=Decimal("1500"))
    dyeing.record_output(db, job_id=job.id, lots=[{"lot_code": "A", "qty_kg": Decimal("60")},
                                                  {"lot_code": "B", "qty_kg": Decimal("40")}])
    dyeing.complete_job(db, job_id=job.id, voucher_date=VD)
    total_dyed = Decimal("24600") + Decimal("3000") + Decimal("1500")  # 29100
    assert _gl(db, cid, "1085") == total_dyed                          # finished fabric inventory
    assert _gl(db, cid, "1080") == Decimal("0.00")                     # dyeing WIP cleared
    _assert_reconciled(db, cid)
    assert prec.production_ledger_reconciliation(db, cid)["is_reconciled"] is True

    outs = {x.lot_code: x for x in db.execute(
        select(DyeingOutput).where(DyeingOutput.job_id == job.id)).scalars()}

    # ---- 5. sales order -> dispatch lot A -> COGS ----
    so = ol.create_sales_order(db, company_id=cid, customer_id=demo["customer_id"], number="SO-E2E",
                               order_date=VD, lines=[{"fabric_type_id": demo["fabric_type_id"],
                                                      "shade_id": demo["shade_id"], "qty_kg": "100", "rate": "400"}])
    cogs0 = _gl(db, cid, "5150")
    ol.create_delivery(db, company_id=cid, customer_id=demo["customer_id"], sales_order_id=so.id,
                       number="DN-E2E-A", delivery_date=VD, roll_ids=[outs["A"].dyed_item_id])
    # lot A (60/100 of 29100 = 17460) dispatched: COGS up, finished down, A stock 0
    a_cost = Decimal("29100") * Decimal("60") / Decimal("100")
    assert _gl(db, cid, "5150") == cogs0 + a_cost
    assert inventory.balance(db, company_id=cid, item_type=ItemType.FABRIC,
                             item_id=outs["A"].dyed_item_id)["qty_on_hand"] == Decimal("0.000000")
    assert _gl(db, cid, "1085") == total_dyed - a_cost                 # only B remains
    _assert_reconciled(db, cid)

    # ---- 6. partial dispatch: B dispatched later ----
    ol.create_delivery(db, company_id=cid, customer_id=demo["customer_id"], sales_order_id=so.id,
                       number="DN-E2E-B", delivery_date=VD, roll_ids=[outs["B"].dyed_item_id])
    assert _gl(db, cid, "1085") == Decimal("0.00")                     # all finished stock sold
    assert _gl(db, cid, "5150") == cogs0 + total_dyed                  # full dyed cost in COGS
    _assert_reconciled(db, cid)


def test_partial_production_and_rejection_reprocess(db, demo):
    """Partial production (produce < planned), plus rejection and reprocessing on
    the dyeing side; reconciliation holds throughout."""
    cid = demo["company_id"]
    b = bom.create_bom(db, company_id=cid, code="E2E2", name="E2E2")
    v = b.versions[0]
    bom.set_yarns(db, v.id, [{"yarn_id": demo["yarn_id"], "blend_percent": Decimal("100"),
                              "std_consumption_per_kg": Decimal("1.0"), "std_unit_cost": Decimal("240")}])
    bom.approve_version(db, version_id=v.id, approved_by=None)
    o = production.create_order(db, company_id=cid, bom_version_id=v.id,
                               planned_qty_kg=Decimal("100"), number="PO-E2E2", order_date=VD)
    r = o.reservations[0]
    production.issue_material(db, order_id=o.id, reservation_id=r.id, qty=Decimal("100"), voucher_date=VD)
    # partial production: 92 kg good grey out of 100 kg yarn (8 kg knitting loss absorbed)
    production.complete_order(db, order_id=o.id, produced_qty_kg=Decimal("92"))
    production.record_actual_knitting_conversion_cost(db, order_id=o.id, amount=Decimal("500"))
    production.value_grey_output(db, order_id=o.id, voucher_date=VD)
    grey = inventory.balance(db, company_id=cid, item_type=ItemType.FABRIC, item_id=o.id)
    assert grey["qty_on_hand"] == Decimal("92.000000")
    assert grey["value"] == Decimal("24500.00")                       # 24000 yarn + 500 conversion
    _assert_reconciled(db, cid)

    job = dyeing.create_job(db, company_id=cid, number="DJ-E2E2", mode="INTERNAL",
                            grey_production_order_id=o.id)
    dyeing.issue_grey(db, job_id=job.id, source_production_order_id=o.id, qty_kg=Decimal("92"), voucher_date=VD)
    dyeing.record_costs(db, job_id=job.id, dyeing_charge=Decimal("2800"), chemical_cost=Decimal("1200"),
                        reprocess_cost=Decimal("400"))
    # 85 kg saleable, 3 kg rejected, 4 kg loss (92 - 85 - 3)
    dyeing.record_output(db, job_id=job.id, lots=[{"lot_code": "R1", "qty_kg": Decimal("85")}])
    dyeing.complete_job(db, job_id=job.id, rejection_kg=Decimal("3"), reprocess_qty_kg=Decimal("5"),
                        voucher_date=VD)
    cost = dyeing.dyed_actual_cost(db, job.id)
    assert cost["saleable_output_kg"] == Decimal("85.000000")
    assert cost["rejection_kg"] == Decimal("3.000000")
    assert cost["process_loss_kg"] == Decimal("4.000000")             # 92 - 85 - 3
    # total = grey 24500 + dyeing 2800 + chemical 1200 + reprocess 400 = 28900, all in finished GL
    assert _gl(db, cid, "1085") == Decimal("28900.00")
    _assert_reconciled(db, cid)
    assert prec.production_ledger_reconciliation(db, cid)["is_reconciled"] is True
