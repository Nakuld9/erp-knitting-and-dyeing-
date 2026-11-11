"""Manufacturing account, trading account, and funds-flow statement."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.commercial import SalesInvoice, SalesInvoiceLine
from app.models.manufacturing import KnittingProduction
from app.services import order_lifecycle as ol, sales
from app.services.accounting import financial_statements as fs
from app.services.manufacturing.knitting import record_production

FROM = date(2026, 4, 1)
TO = date(2027, 3, 31)


def _produce(db, demo, weight="250"):
    import uuid as _u
    kp = KnittingProduction(company_id=demo["company_id"], number=f"KP-FA-{_u.uuid4().hex[:6]}",
                            machine_id=demo["machines"]["knit"], yarn_id=demo["yarn_id"],
                            yarn_lot_id=demo["yarn_lot_id"], fabric_type_id=demo["fabric_type_id"],
                            production_date=date(2026, 11, 8), produced_kg=Decimal(weight),
                            yarn_consumed_kg=Decimal("255"))
    db.add(kp); db.flush()
    rolls = record_production(db, kp, [{"weight_kg": weight, "warehouse_id": demo["warehouses"]["greige"]}])["rolls"]
    db.commit()
    return rolls


def test_manufacturing_account_cogm(db, demo):
    _produce(db, demo)
    m = fs.manufacturing_account(db, demo["company_id"], FROM, TO)
    # Yarn (255 @ 240 = 61,200) flows through WIP and out as cost of goods manufactured.
    assert m["cost_of_goods_manufactured"] == Decimal("61200.00")
    assert m["closing_wip"] == Decimal("0.00")           # WIP cleared to greige


def test_trading_account_no_sales(db, demo):
    _produce(db, demo)
    t = fs.trading_account(db, demo["company_id"], FROM, TO)
    # Produced greige sits in closing stock; nothing sold -> COGS 0, gross profit 0.
    assert t["closing_stock"] == Decimal("61200.00")
    assert t["cost_of_goods_sold"] == Decimal("0.00")
    assert t["gross_profit"] == Decimal("0.00")


def test_trading_account_with_sale(db, demo):
    rolls = _produce(db, demo)
    ol.create_delivery(db, company_id=demo["company_id"], customer_id=demo["customer_id"],
                       sales_order_id=None, number="DC-FA", delivery_date=date(2026, 11, 8),
                       roll_ids=[r.id for r in rolls])
    db.commit()
    inv = SalesInvoice(company_id=demo["company_id"], customer_id=demo["customer_id"],
                       number="SI-FA", invoice_date=date(2026, 11, 8))
    inv.lines.append(SalesInvoiceLine(line_no=1, qty=Decimal("250"), rate=Decimal("300"),
                                      tax_code_id=demo["tax_id"]))
    db.add(inv); db.flush()
    sales.post_sales_invoice(db, inv); db.commit()

    t = fs.trading_account(db, demo["company_id"], FROM, TO)
    assert t["sales"] == Decimal("75000.00")             # 250 @ 300
    assert t["cost_of_goods_sold"] == Decimal("61200.00")   # greige dispatched
    assert t["gross_profit"] == Decimal("13800.00")      # 75,000 - 61,200


def test_funds_flow_fixed_asset_and_operations(db, demo):
    from app.services import assets
    # Capitalise a machine: Dr fixed asset / Cr bank -> an application of funds.
    assets.capitalize_asset(db, company_id=demo["company_id"], code="FA-FF", name="Jet",
                            asset_account_code="1532", acquisition_date=date(2026, 11, 8),
                            cost="200000", method="SLM", useful_life_months=120)
    db.commit()
    ff = fs.funds_flow(db, demo["company_id"], FROM, TO)
    apps = {a["item"]: a["amount"] for a in ff["applications"]}
    assert apps.get("Fixed assets purchased") == Decimal("200000.00")
    assert ff["funds_from_operations"] == ff["net_profit"] + ff["depreciation_addback"]
