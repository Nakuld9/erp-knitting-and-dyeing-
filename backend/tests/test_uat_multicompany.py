"""Phase 15 — automated multi-company/multi-factory UAT acceptance scenario.

Sets up two legal companies (each with its own chart of accounts and factories)
in one group, then runs the cross-cutting flows introduced by the multi-company
programme and reconciles each result against an independently-calculated expected
value:

  1. company isolation (stock in A is invisible to B);
  2. same-company factory transfer moves stock, GL-neutral;
  3. inter-company charge posts BOTH companies' ledgers, each balanced;
  4. every company's trial balance balances independently;
  5. group consolidated P&L reflects both companies (inter-company nets out);
  6. inter-company balances are reconcilable (ready for elimination).

This is automated acceptance evidence. Business-owner sign-off (Phase 15) and the
external reviews (Phase 16) are separate and not asserted here.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models.accounting import ChartOfAccount
from app.models.core import Branch, Company
from app.models.inventory import ItemType, StockLayer
from app.models.masters import Warehouse
from app.models.tenancy import IntercompanyAccountMapping, Organization
from app.seed.seed import seed_chart_of_accounts, seed_periods
from app.services.accounting.posting_rules import seed_default_posting_rules
from app.services import factory_transfer as ft
from app.services.accounting import intercompany as ic
from app.services.accounting import consolidation as consol
from app.services.accounting import trial_balance as tb
from app.services.inventory.service import receive

YARN = uuid.UUID("33333333-3333-3333-3333-333333333333")


def _seed_company(db, code, state="03"):
    c = Company(code=code, name=code, state_code=state)
    db.add(c); db.flush()
    seed_chart_of_accounts(db, c); seed_default_posting_rules(db, c.id); seed_periods(db, c, 2026)
    db.commit()
    return c


def _acct(db, cid, codestr):
    return db.execute(select(ChartOfAccount).where(
        ChartOfAccount.company_id == cid, ChartOfAccount.code == codestr)).scalar_one().id


def _onhand(db, cid, wid):
    return sum(Decimal(str(l.qty_remaining)) for l in db.execute(select(StockLayer).where(
        StockLayer.company_id == cid, StockLayer.warehouse_id == wid,
        StockLayer.item_id == YARN, StockLayer.qty_remaining > 0)).scalars().all())


def _uat_setup(db):
    a = db.execute(select(Company).where(Company.code == "DEMO")).scalar_one()
    b = _seed_company(db, "UATB", "27")
    org = Organization(code="UATG", name="UAT Group"); db.add(org); db.flush()
    # assign both companies to the group (direct UPDATE: deterministic in the harness)
    from sqlalchemy import update as _upd
    db.execute(_upd(Company).where(Company.id.in_([a.id, b.id])).values(organization_id=org.id))
    # company A: two factories + warehouses, stock in F1's warehouse
    f1 = Branch(company_id=a.id, code="A-F1", name="A Unit 1")
    f2 = Branch(company_id=a.id, code="A-F2", name="A Unit 2")
    db.add_all([f1, f2]); db.flush()
    w1 = Warehouse(company_id=a.id, branch_id=f1.id, code="A-W1", name="A WH1", kind="STORE")
    w2 = Warehouse(company_id=a.id, branch_id=f2.id, code="A-W2", name="A WH2", kind="STORE")
    db.add_all([w1, w2]); db.flush()
    receive(db, company_id=a.id, item_type=ItemType.YARN, item_id=YARN, qty=1000,
            unit_cost=200, warehouse_id=w1.id, event_type="GRN")
    # approved reciprocal inter-company mappings
    for (x, y) in [(a, b), (b, a)]:
        db.add(IntercompanyAccountMapping(company_id=x.id, counterparty_company_id=y.id,
               due_from_account_id=_acct(db, x.id, "1050"), due_to_account_id=_acct(db, x.id, "2070"),
               is_approved=True))
    db.commit()
    return a, b, org, f1, f2, w1, w2


def test_uat_company_isolation_and_factory_transfer(db):
    a, b, org, f1, f2, w1, w2 = _uat_setup(db)
    # (1) isolation: company B has no stock of this yarn anywhere
    b_stock = db.execute(select(StockLayer).where(StockLayer.company_id == b.id,
              StockLayer.item_id == YARN)).scalars().all()
    assert b_stock == []
    # (2) factory transfer F1 -> F2 (300 kg), GL-neutral
    t = ft.create_transfer_request(db, company_id=a.id, item_type=ItemType.YARN, item_id=YARN,
                                   from_warehouse_id=w1.id, to_warehouse_id=w2.id, qty=300,
                                   requested_by=uuid.uuid4())
    ft.authorize_transfer(db, t.id, a.id, uuid.uuid4())
    ft.dispatch_transfer(db, t.id, a.id, uuid.uuid4())
    ft.receive_transfer(db, t.id, a.id, uuid.uuid4(), received_qty=300)
    assert _onhand(db, a.id, w1.id) == Decimal("700")   # 1000 - 300
    assert _onhand(db, a.id, w2.id) == Decimal("300")
    from app.models.accounting import Voucher
    assert db.execute(select(Voucher).where(Voucher.source_module == "factory_transfer")).scalars().all() == []


def test_uat_intercompany_and_consolidation(db):
    a, b, org, f1, f2, w1, w2 = _uat_setup(db)
    # (3) inter-company charge A -> B of 500000 (no tax code -> no GST)
    res = ic.post_intercompany_charge(db, seller_company_id=a.id, buyer_company_id=b.id,
                                      taxable=Decimal("500000"), voucher_date=date(2026, 4, 15), reference="UAT1")
    assert res["gross"] == "500000.00"
    # (4) each company's trial balance balances independently
    tba = tb.trial_balance(db, a.id); tbb = tb.trial_balance(db, b.id)
    assert tba["is_balanced"] is True
    assert tbb["is_balanced"] is True
    # A recognised income; B recognised expense (reconcile exact amounts)
    def _net(tbres, code):
        row = next((r for r in tbres["rows"] if r["account_code"] == code), None)
        if not row: return Decimal("0")
        return Decimal(str(row["closing_debit"])) - Decimal(str(row["closing_credit"]))
    assert _net(tba, "4010") == Decimal("-500000.00")   # income (credit)
    assert _net(tbb, "5150") == Decimal("500000.00")    # expense (debit)
    assert _net(tba, "1050") == Decimal("500000.00")    # A due-from (asset)
    assert _net(tbb, "2070") == Decimal("-500000.00")   # B due-to (liability)
    # (5) consolidated P&L: income and expense both 500000, nets out at group level
    pnl = consol.consolidated_profit_and_loss(db, org.id)
    assert pnl["total_income"] == Decimal("500000")
    assert pnl["total_expense"] == Decimal("500000")
    assert pnl["net_profit"] == Decimal("0")
    # (6) inter-company balances reconcile -> ready for elimination (both legs approved)
    recon = ic.reconciliation(db)
    assert len(recon) == 2 and all(r["ready_for_elimination"] for r in recon)
