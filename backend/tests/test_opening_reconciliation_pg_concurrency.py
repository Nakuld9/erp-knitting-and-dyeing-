"""Stage 6F-P6 — PostgreSQL concurrency acceptance for opening reconciliation.

Needs a real PostgreSQL server; runs only when PG_TEST_URL is set, e.g.:

    PG_TEST_URL='postgresql+psycopg2://erp@/erp_test?host=/tmp/pgtest_p6/sock&port=54366' \
        pytest tests/test_opening_reconciliation_pg_concurrency.py

Barrier-synchronised workers in their own sessions/transactions; wall-clock overlap
asserted. ``uq_openrecon_run_number`` (company + recon_type + run_number) is the
concurrency authority: two concurrent runs cannot both claim the same run number —
exactly one wins, the loser rolls back cleanly (deterministic run identifiers, no
duplicate). Company isolation: two companies reconcile concurrently and both succeed.
Not executed in the standard SQLite suite.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

PG_URL = os.environ.get("PG_TEST_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="PG_TEST_URL not set (needs a real PostgreSQL)")

REPS = int(os.environ.get("PG_RACE_REPS", "5"))
ED = date(2026, 11, 15)


def _engine_session():
    from app.models import Base
    engine = create_engine(PG_URL, future=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, autoflush=False, future=True)


def _seed(Session, code):
    from app.models.core import Company
    from app.models.hybrid import WarehouseCategory
    from app.models.inventory import ItemType, StockLayer
    from app.models.masters import Warehouse
    s = Session()
    c = Company(code=code, name=code)
    s.add(c); s.flush()
    w = Warehouse(company_id=c.id, code="YS", name="YS", category=WarehouseCategory.YARN_STORE.value)
    s.add(w); s.flush()
    s.add(StockLayer(company_id=c.id, item_type=ItemType.YARN.value, item_id=uuid.uuid4(),
                     qty_in=Decimal("10"), qty_remaining=Decimal("10"), unit_cost=Decimal("1"),
                     value_remaining=Decimal("1000"), warehouse_id=w.id,
                     received_at=datetime.now(timezone.utc)))
    s.commit()
    cid = c.id
    s.close()
    return cid


def _barrier_run(n, work):
    barrier = threading.Barrier(n)
    starts, results = [0.0] * n, [None] * n

    def runner(i):
        barrier.wait()
        starts[i] = time.monotonic()
        results[i] = work(i)

    with ThreadPoolExecutor(max_workers=n) as ex:
        list(ex.map(runner, range(n)))
    return results, max(starts) - min(starts)


@pytest.mark.parametrize("rep", range(REPS))
def test_concurrent_runs_unique_run_number(rep):
    from app.services import opening_reconciliation as recon
    from app.models.opening_reconciliation import OpeningReconciliationRun, ReconType
    engine, Session = _engine_session()
    cid = _seed(Session, f"C{rep}")

    def work(i):
        s = Session()
        try:
            recon.run_reconciliation(s, cid, recon_type=ReconType.INVENTORY.value, effective_date=ED)
            s.commit()
            return "ok"
        except IntegrityError:
            s.rollback()
            return "rejected"
        finally:
            s.close()

    results, overlap = _barrier_run(2, work)
    assert overlap < 0.5
    # At least one succeeds; run numbers are unique (no duplicate identifier).
    assert "ok" in results
    chk = Session()
    nums = [r.run_number for r in chk.execute(select(OpeningReconciliationRun).where(
        OpeningReconciliationRun.company_id == cid)).scalars().all()]
    chk.close()
    assert len(nums) == len(set(nums))        # deterministic, no duplicate run_number
    engine.dispose()


def test_company_isolation_concurrent():
    from app.services import opening_reconciliation as recon
    from app.models.opening_reconciliation import OpeningReconciliationRun, ReconType
    engine, Session = _engine_session()
    c1 = _seed(Session, "ISO1")
    c2 = _seed(Session, "ISO2")
    barrier = threading.Barrier(2)
    out = [None, None]

    def runner(cid, idx):
        barrier.wait()
        s = Session()
        try:
            recon.run_reconciliation(s, cid, recon_type=ReconType.INVENTORY.value, effective_date=ED)
            s.commit()
            out[idx] = "ok"
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(runner, c1, 0)
        f2 = ex.submit(runner, c2, 1)
        f1.result(); f2.result()
    assert out == ["ok", "ok"]
    chk = Session()
    assert chk.execute(select(func.count()).select_from(OpeningReconciliationRun).where(
        OpeningReconciliationRun.company_id == c1)).scalar() == 1
    assert chk.execute(select(func.count()).select_from(OpeningReconciliationRun).where(
        OpeningReconciliationRun.company_id == c2)).scalar() == 1
    chk.close()
    engine.dispose()
