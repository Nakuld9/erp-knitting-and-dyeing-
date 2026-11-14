"""HIGH-3: dyed fabric is a sellable FINISHED FabricRoll that flows through the
EXISTING order-delivery/dispatch/COGS path, with traceability back to the dyeing
job, grey production order and yarn lots."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.accounting import Voucher
from app.models.manufacturing import FabricRoll, RollKind
from app.models.dyeing_cost import DyeingOutput
from app.services.accounting.stock_reconciliation import stock_ledger_reconciliation
from app.services.accounting.trial_balance import trial_balance
from app.services.bom import production, service as bom
from app.services.dyeing import service as dyeing
from app.services import order_lifecycle as ol
from app.services.inventory import service as inventory
from app.models.inventory import ItemType

VD = date(2026, 6, 15)


def _dyed_job(db, demo, *, code, lots):
    b = bom.create_bom(db, company_id=demo["company_id"], code=code, name=code)
    v = b.versions[0]
    bom.set_yarns(db, v.id, [{"yarn_id": demo["yarn_id"], "blend_percent": Decimal("100"),
                              "std_consumption_per_kg": Decimal("1.0"), "std_unit_cost": Decimal("240")}])
    bom.approve_version(db, version_id=v.id, approved_by=None)
    o = production.create_order(db, company_id=demo["company_id"], bom_version_id=v.id,
                               planned_qty_kg=Decimal("100"), number="PO-"+code, order_date=VD)
    r = o.reservations[0]
    production.issue_material(db, order_id=o.id, reservation_id=r.id, qty=r.planned_qty, voucher_date=VD)
    production.complete_order(db, order_id=o.id, produced_qty_kg=Decimal("100"))
    production.record_actual_knitting_conversion_cost(db, order_id=o.id, amount=Decimal("600"))
    production.value_grey_output(db, order_id=o.id, voucher_date=VD)
    job = dyeing.create_job(db, company_id=demo["company_id"], number="DJ-"+code, mode="INTERNAL",
                            grey_production_order_id=o.id, shade_id=demo["shade_id"])
    dyeing.issue_grey(db, job_id=job.id, source_production_order_id=o.id, qty_kg=Decimal("100"), voucher_date=VD)
    dyeing.record_costs(db, job_id=job.id, dyeing_charge=Decimal("3000"), chemical_cost=Decimal("1500"))
    dyeing.record_output(db, job_id=job.id, lots=lots)
    dyeing.complete_job(db, job_id=job.id, voucher_date=VD)
    return o, job


def test_dyed_output_is_finished_fabric_roll(db, demo):
    o, job = _dyed_job(db, demo, code="DSP1", lots=[{"lot_code": "L1", "qty_kg": Decimal("95")}])
    out = db.execute(select(DyeingOutput).where(DyeingOutput.job_id == job.id)).scalar_one()
    roll = db.get(FabricRoll, out.dyed_item_id)
    assert roll is not None
    assert (roll.kind == RollKind.FINISHED or roll.kind == RollKind.FINISHED.value)
    assert Decimal(str(roll.weight_kg)) == Decimal("95")
    # inventory for the roll == allocated cost (total 29100 for the single lot)
    bal = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC, item_id=roll.id)
    assert bal["value"] == Decimal("29100.00")


def test_dispatch_dyed_fabric_posts_cogs_and_clears_finished(db, demo):
    o, job = _dyed_job(db, demo, code="DSP2", lots=[{"lot_code": "L1", "qty_kg": Decimal("95")}])
    roll = db.execute(select(DyeingOutput.dyed_item_id).where(DyeingOutput.job_id == job.id)).scalar_one()
    before = stock_ledger_reconciliation(db, demo["company_id"])
    fg_before = {r["account"]: r for r in before["rows"]}["1085"]["ledger_balance"]
    assert fg_before == Decimal("29100.00")
    res = ol.create_delivery(db, company_id=demo["company_id"], customer_id=demo["customer_id"],
                             sales_order_id=None, number="DN-DSP2", delivery_date=VD, roll_ids=[roll])
    db.commit()
    # COGS voucher: Dr 5150 COGS == Cr 1085 finished == roll cost
    dn_v = db.execute(select(Voucher).where(Voucher.company_id == demo["company_id"],
                                            Voucher.posting_reference == "DN:DN-DSP2")).scalar_one()
    def line(code):
        from app.models.accounting import ChartOfAccount, JournalLine
        a = db.execute(select(ChartOfAccount).where(ChartOfAccount.company_id == demo["company_id"],
                                                    ChartOfAccount.code == code)).scalar_one()
        d = c = Decimal("0")
        for ln in dn_v.lines:
            if ln.account_id == a.id:
                d += Decimal(str(ln.debit)); c += Decimal(str(ln.credit))
        return d, c
    assert line("5150")[0] == Decimal("29100.00")          # COGS debit
    assert line("1085")[1] == Decimal("29100.00")          # finished credit
    assert res["cogs_value"] == Decimal("29100.00")
    # finished inventory cleared; reconciliation still holds
    bal = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC, item_id=roll)
    assert bal["qty_on_hand"] == Decimal("0.000000")
    rec = stock_ledger_reconciliation(db, demo["company_id"])
    assert rec["is_reconciled"] is True
    assert trial_balance(db, demo["company_id"])["is_balanced"]


def test_multiple_lots_partial_dispatch_and_no_oversell(db, demo):
    o, job = _dyed_job(db, demo, code="DSP3",
                       lots=[{"lot_code": "A", "qty_kg": Decimal("60")}, {"lot_code": "B", "qty_kg": Decimal("30")}])
    outs = db.execute(select(DyeingOutput).where(DyeingOutput.job_id == job.id)).scalars().all()
    rolls = {o2.lot_code: o2.dyed_item_id for o2 in outs}
    # partial dispatch: only lot A
    ol.create_delivery(db, company_id=demo["company_id"], customer_id=demo["customer_id"],
                       sales_order_id=None, number="DN-DSP3A", delivery_date=VD, roll_ids=[rolls["A"]])
    db.commit()
    # lot B still in finished stock
    assert inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC,
                             item_id=rolls["B"])["qty_on_hand"] == Decimal("30.000000")
    # duplicate dispatch of A is rejected (roll already on a delivery note)
    with pytest.raises(ol.LifecycleError):
        ol.create_delivery(db, company_id=demo["company_id"], customer_id=demo["customer_id"],
                           sales_order_id=None, number="DN-DSP3A2", delivery_date=VD, roll_ids=[rolls["A"]])
    db.rollback()
    # dispatch B too, then reconciliation holds (all finished stock sold)
    ol.create_delivery(db, company_id=demo["company_id"], customer_id=demo["customer_id"],
                       sales_order_id=None, number="DN-DSP3B", delivery_date=VD, roll_ids=[rolls["B"]])
    db.commit()
    rec = stock_ledger_reconciliation(db, demo["company_id"])
    assert rec["is_reconciled"] is True


def test_traceability_dyed_roll_to_grey_to_yarn(db, demo):
    o, job = _dyed_job(db, demo, code="DSP4", lots=[{"lot_code": "L1", "qty_kg": Decimal("95")}])
    out = db.execute(select(DyeingOutput).where(DyeingOutput.job_id == job.id)).scalar_one()
    roll_id = out.dyed_item_id
    # dyed roll -> dyeing output -> job -> grey PO -> yarn lots, via the trace API
    tr = dyeing.trace(db, job.id)
    assert any(x["dyed_item_id"] == str(roll_id) for x in tr["output_lots"])
    assert tr["grey_sources"][0]["production_order_id"] == str(o.id)
    assert len(tr["grey_sources"][0]["yarn_issues"]) >= 1
    assert tr["grey_sources"][0]["yarn_issues"][0]["cost"] > 0
