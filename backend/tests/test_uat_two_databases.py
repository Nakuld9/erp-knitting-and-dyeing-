"""True multi-database UAT on two physically separate PostgreSQL databases.

GATED: runs only when PG_CTL_URL, PG_A_URL and PG_B_URL point at three empty
PostgreSQL databases (control + two company databases) on the same server;
otherwise skipped (like test_pg_concurrency). It provisions the two company
databases and verifies, on real separate databases:

- company A reads only database A, company B only database B;
- company switching changes the actual database connection;
- factory transfers work within a company database;
- inter-company transactions reconcile across the two databases (saga);
- consolidation reads AND eliminates across the two databases;
- concurrent per-company sessions do not leak database/company context.

Reproduce (see docs/POSTGRES_VALIDATION.md): create three empty databases, then
    PG_CTL_URL=... PG_A_URL=... PG_B_URL=... pytest tests/test_uat_two_databases.py
"""
from __future__ import annotations

import os
import threading
import uuid
from datetime import date
from decimal import Decimal

import pytest

PG_CTL = os.environ.get("PG_CTL_URL")
PG_A = os.environ.get("PG_A_URL")
PG_B = os.environ.get("PG_B_URL")

pytestmark = pytest.mark.skipif(
    not (PG_CTL and PG_A and PG_B),
    reason="set PG_CTL_URL / PG_A_URL / PG_B_URL to three empty PostgreSQL databases")


def test_two_physical_databases(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", PG_CTL)
    # import after DATABASE_URL is set so the central engine binds to the control DB
    import importlib
    import app.core.config as cfg
    importlib.reload(cfg)
    import app.core.database as database
    importlib.reload(database)
    import app.core.db_router as db_router
    importlib.reload(db_router)

    from sqlalchemy import select, update as sqlupdate
    from app.models import Base
    from app.seed.seed import run_seed
    from app.core.db_router import company_session, resolve_company_dsn, engine_for_company
    from app.models.core import Company, Branch
    from app.models.masters import Customer, Warehouse
    from app.models.accounting import ChartOfAccount, Voucher
    from app.models.inventory import ItemType, StockLayer
    from app.models.tenancy import (Organization, IntercompanyAccountMapping,
                                    ConsolidationElimination, IntercompanyTxnStatus)
    from app.services.company_provisioning import provision_company_database
    from app.services import factory_transfer as ft
    from app.services.inventory.service import receive
    from app.services.accounting import intercompany_saga as saga
    from app.services.accounting import consolidation as consol

    Base.metadata.create_all(database.engine)
    run_seed(database.SessionLocal(), fy_start_year=2026)
    db = database.SessionLocal()
    a = db.execute(select(Company).where(Company.code == "DEMO")).scalar_one()
    b = Company(code="CO2", name="Second Co", state_code="03"); db.add(b); db.flush()
    org = Organization(code="GRP", name="Group"); db.add(org); db.flush()
    db.execute(sqlupdate(Company).where(Company.id.in_([a.id, b.id])).values(organization_id=org.id))
    db.commit()
    aid, bid = a.id, b.id

    provision_company_database(aid, PG_A, fy_start_year=2026)
    provision_company_database(bid, PG_B, fy_start_year=2026)
    assert engine_for_company(aid) is not engine_for_company(bid)

    for (x, y) in [(aid, bid), (bid, aid)]:
        db.add(IntercompanyAccountMapping(company_id=x, counterparty_company_id=y,
               due_from_account_code="1050", due_to_account_code="2070", is_approved=True))
    db.commit()

    # isolation
    with company_session(aid) as sa:
        sa.add(Customer(company_id=aid, code="CUST-A", name="Alpha")); sa.commit()
    with company_session(aid) as sa:
        assert "CUST-A" in {c.code for c in sa.execute(select(Customer)).scalars().all()}
    with company_session(bid) as sb:
        assert "CUST-A" not in {c.code for c in sb.execute(select(Customer)).scalars().all()}

    # factory transfer within A's database
    YARN = uuid.UUID("44444444-4444-4444-4444-444444444444")
    with company_session(aid) as sa:
        f1 = Branch(company_id=aid, code="F1", name="U1"); f2 = Branch(company_id=aid, code="F2", name="U2")
        sa.add_all([f1, f2]); sa.flush()
        w1 = Warehouse(company_id=aid, branch_id=f1.id, code="W1", name="WH1", kind="STORE")
        w2 = Warehouse(company_id=aid, branch_id=f2.id, code="W2", name="WH2", kind="STORE")
        sa.add_all([w1, w2]); sa.flush()
        receive(sa, company_id=aid, item_type=ItemType.YARN, item_id=YARN, qty=1000, unit_cost=200,
                warehouse_id=w1.id, event_type="GRN")
        sa.commit(); w1id, w2id = w1.id, w2.id
    with company_session(aid) as sa:
        t = ft.create_transfer_request(sa, company_id=aid, item_type=ItemType.YARN, item_id=YARN,
                                       from_warehouse_id=w1id, to_warehouse_id=w2id, qty=300,
                                       requested_by=uuid.uuid4())
        ft.authorize_transfer(sa, t.id, aid, uuid.uuid4()); ft.dispatch_transfer(sa, t.id, aid, uuid.uuid4())
        ft.receive_transfer(sa, t.id, aid, uuid.uuid4(), received_qty=300)
    with company_session(aid) as sa:
        onh = lambda wid: sum(Decimal(str(l.qty_remaining)) for l in sa.execute(select(StockLayer).where(
            StockLayer.warehouse_id == wid, StockLayer.qty_remaining > 0)).scalars().all())
        assert onh(w1id) == Decimal("700") and onh(w2id) == Decimal("300")

    # inter-company saga across the two databases
    cfg.settings.intercompany_live_posting = True
    txn = saga.post_intercompany_saga(db, seller_company_id=aid, buyer_company_id=bid,
                                      taxable=Decimal("500000"), idempotency_key="K1",
                                      voucher_date=date(2026, 4, 15), require_live_flag=True)
    assert IntercompanyTxnStatus(txn.status) == IntercompanyTxnStatus.COMPLETED
    with company_session(aid) as sa:
        assert len(sa.execute(select(Voucher).where(Voucher.posting_reference == "ICSAGA:K1:SELL")).scalars().all()) == 1
    with company_session(bid) as sb:
        assert len(sb.execute(select(Voucher).where(Voucher.posting_reference == "ICSAGA:K1:BUY")).scalars().all()) == 1

    # consolidation across both databases + real elimination
    pnl = consol.consolidated_profit_and_loss(db, org.id)
    assert pnl["total_income"] == Decimal("500000") and pnl["total_expense"] == Decimal("500000")
    db.add_all([ConsolidationElimination(organization_id=org.id, account_code="4010",
                                         debit=Decimal("500000"), credit=Decimal("0"), is_approved=True),
                ConsolidationElimination(organization_id=org.id, account_code="5150",
                                         debit=Decimal("0"), credit=Decimal("500000"), is_approved=True)])
    db.commit()
    pnl2 = consol.consolidated_profit_and_loss(db, org.id)
    assert pnl2["total_income"] == Decimal("0") and pnl2["total_expense"] == Decimal("0")

    # concurrency: parallel per-company sessions do not leak
    errors = []
    def worker(cid, expect):
        try:
            for _ in range(15):
                with company_session(cid) as s:
                    has = "CUST-A" in {c.code for c in s.execute(select(Customer)).scalars().all()}
                    if has != expect:
                        errors.append(cid)
        except Exception as e:  # noqa
            errors.append(str(e))
    ths = [threading.Thread(target=worker, args=(aid, True)) for _ in range(8)] + \
          [threading.Thread(target=worker, args=(bid, False)) for _ in range(8)]
    [t.start() for t in ths]; [t.join() for t in ths]
    assert errors == []
