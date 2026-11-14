"""Acceptance TEST 17: stock valuation reconciles with inventory control accounts."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.models.manufacturing import BatchStatus, DyeBatch, DyeBath
from app.services.accounting import stock_reconciliation as sr
from app.services.colorservice import ColorServiceSimulator
from app.services.colorservice import ingest as cs


def test_reconciles_after_opening_stock(db, demo):
    rec = sr.stock_ledger_reconciliation(db, demo["company_id"])
    by_acct = {r["account"]: r for r in rec["rows"]}
    assert by_acct["1090"]["stock_value"] == Decimal("1350000.00")   # dyes
    assert by_acct["1100"]["stock_value"] == Decimal("17500.00")     # chemicals
    assert by_acct["1060"]["stock_value"] == Decimal("480000.00")    # yarn
    for r in rec["rows"]:
        assert r["ledger_balance"] == r["stock_value"]
        assert r["difference"] == Decimal("0.00")
    assert rec["is_reconciled"] is True


def test_reconciles_after_consumption(db, demo):
    batch = DyeBatch(company_id=demo["company_id"], batch_code="DY-REC", shade_id=demo["shade_id"],
                     fabric_kg=Decimal("250"), status=BatchStatus.RUNNING)
    db.add(batch)
    db.flush()
    bath = DyeBath(company_id=demo["company_id"], batch_id=batch.id, bath_code="DY-REC-B01",
                   machine_id=demo["machines"]["jet"], recipe_version_id=demo["recipe_version_id"],
                   fabric_kg=Decimal("250"),
                   actual_start=datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc))
    db.add(bath)
    db.flush()
    adapter = ColorServiceSimulator(lot_by_material=demo["lot_refs"])
    _ack, order = cs.send_dose_order(db, bath, adapter)
    cs.ingest_results(db, demo["company_id"], bath, adapter.fetch_results(order))
    db.commit()

    rec = sr.stock_ledger_reconciliation(db, demo["company_id"])
    # consumption reduced both stock value and the GL control accounts equally
    assert rec["is_reconciled"] is True
    assert rec["total_difference"] == Decimal("0.00")


def test_stock_reconciliation_over_api(client, auth_headers, demo):
    r = client.get("/api/reports/stock-reconciliation", headers=auth_headers).json()
    assert r["is_reconciled"] is True
    x = client.get("/api/reports/export/stock-reconciliation?format=xlsx", headers=auth_headers)
    assert x.status_code == 200 and x.content[:2] == b"PK"


def test_reconciles_after_full_knit_dye_flow(db, demo):
    """HIGH-1: a complete grey-BOM → knitting → grey fabric → dyeing → dyed
    fabric flow must leave stock-vs-GL reconciled, with dyed fabric valued against
    FINISHED (1085), not greige (1070)."""
    from datetime import date
    from app.services.bom import production, service as bom
    from app.services.dyeing import service as dyeing

    cid = demo["company_id"]; vd = date(2026, 6, 15)
    b = bom.create_bom(db, company_id=cid, code="SR-GD", name="SR grey→dyed")
    v = b.versions[0]
    bom.set_yarns(db, v.id, [{"yarn_id": demo["yarn_id"], "blend_percent": Decimal("100"),
                              "std_consumption_per_kg": Decimal("1.0"), "std_unit_cost": Decimal("240")}])
    bom.approve_version(db, version_id=v.id, approved_by=None)
    o = production.create_order(db, company_id=cid, bom_version_id=v.id,
                               planned_qty_kg=Decimal("100"), number="PO-SR-GD", order_date=vd)
    res = o.reservations[0]
    production.issue_material(db, order_id=o.id, reservation_id=res.id, qty=res.planned_qty)
    production.complete_order(db, order_id=o.id, produced_qty_kg=Decimal("100"))
    production.record_actual_knitting_conversion_cost(db, order_id=o.id, amount=Decimal("600"))
    production.value_grey_output(db, order_id=o.id, voucher_date=vd)

    # after grey receipt: greige sub-ledger == GL 1070, overall reconciled
    rec = sr.stock_ledger_reconciliation(db, cid)
    by = {r["account"]: r for r in rec["rows"]}
    assert by["1070"]["stock_value"] == by["1070"]["ledger_balance"] == Decimal("24600.00")
    assert by["1085"]["stock_value"] == Decimal("0.00")
    assert rec["is_reconciled"] is True, rec

    # dye it: grey moves out of 1070 into dyeing; dyed fabric lands in 1085
    job = dyeing.create_job(db, company_id=cid, number="DJ-SR-GD", mode="INTERNAL",
                            grey_production_order_id=o.id)
    dyeing.issue_grey(db, job_id=job.id, source_production_order_id=o.id, qty_kg=Decimal("100"), voucher_date=vd)
    dyeing.record_costs(db, job_id=job.id, dyeing_charge=Decimal("3000"), chemical_cost=Decimal("1500"))
    dyeing.record_output(db, job_id=job.id, lots=[{"lot_code": "L1", "qty_kg": Decimal("95")}])
    dyeing.complete_job(db, job_id=job.id, voucher_date=vd)
    db.commit()

    rec2 = sr.stock_ledger_reconciliation(db, cid)
    by2 = {r["account"]: r for r in rec2["rows"]}
    assert by2["1070"]["stock_value"] == by2["1070"]["ledger_balance"] == Decimal("0.00")   # grey consumed
    # dyed fabric value (grey 24600 + dyeing 3000 + chemical 1500 = 29100) is in 1085
    assert by2["1085"]["stock_value"] == Decimal("29100.00")
    assert by2["1085"]["ledger_balance"] == Decimal("29100.00")
    assert by2["1085"]["difference"] == Decimal("0.00")
    assert rec2["is_reconciled"] is True, rec2
