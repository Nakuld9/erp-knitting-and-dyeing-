"""Stage 6F-P2-FIX — PostgreSQL concurrency acceptance for opening YarnLot
registration.

SQLite has neither row-level locks nor a genuine unique-index insertion race, so
these proofs need a real PostgreSQL server. They run ONLY when PG_TEST_URL points
at one; otherwise they skip. A disposable cluster must be approved first, e.g.:

    PG_TEST_URL='postgresql+psycopg2://erp@/erp_test?host=/tmp/pgtest_p2fix/sock&port=54339' \
        pytest tests/test_opening_stock_pg_concurrency.py

Each concurrent operation runs in its OWN thread, with its OWN SQLAlchemy session
and transaction; assertions check the final DATABASE state. A ``threading.Barrier``
forces the two transactions to enter the approval/commit path together so they
genuinely overlap, and each worker records the wall-clock interval it was inside
the operation so the tests can assert the intervals overlapped.

The race under test: two DRAFT opening batches registering the same NEW internal
lot number for one company. The database unique index
``uq_yarn_lot_company_internal_no`` is the authority — the losing INSERT fails,
its transaction rolls back, and no lot / stock layer / inventory transaction is
left behind.

NOTE: not executed in the standard SQLite suite. Do not claim concurrency safety
unless these actually ran against a real PostgreSQL cluster.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

PG_URL = os.environ.get("PG_TEST_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="PG_TEST_URL not set (needs a real PostgreSQL)")

ED = date(2026, 11, 15)


# ------------------------------ harness -------------------------------------

def _engine_session():
    from app.models import Base
    engine = create_engine(PG_URL, future=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, autoflush=False, future=True)


def _seed_company(Session, code):
    from app.models.core import Company
    from app.models.hybrid import WarehouseCategory
    from app.models.masters import UnitOfMeasure, Warehouse, YarnMaster
    s = Session()
    comp = Company(code=code, name=code); s.add(comp); s.flush()
    uom = s.execute(select(UnitOfMeasure).where(UnitOfMeasure.code == "KG")).scalars().first()
    if uom is None:
        uom = UnitOfMeasure(code="KG", name="Kilogram", dimension="MASS", base_factor=1); s.add(uom)
    wh = Warehouse(company_id=comp.id, code="YWH", name="Yarn", kind="STORE",
                   category=WarehouseCategory.YARN_STORE.value)
    yarn = YarnMaster(company_id=comp.id, code="Y1", name="Cotton", is_active=True)
    s.add_all([wh, yarn]); s.commit()
    out = (comp.id, yarn.id)
    s.close()
    return out


def _yrow(lot, row=3):
    return {"yarn_code": "Y1", "internal_lot_no": lot, "warehouse_code": "YWH",
            "factory_code": None, "quantity": "100", "uom_code": "KG", "unit_cost": "250",
            "total_value": "25000", "effective_date": None, "source_document": "GRN",
            "source_reference": None, "legacy_unknown": None, "_row": row}


def _draft_import_submit(Session, cid, rows, submit=True):
    import hashlib
    from app.models.opening_stock import OpeningImportFile, OpeningLineType
    from app.services import opening as batch_svc
    from app.services import opening_stock as stock_svc
    maker = uuid.uuid4()
    s = Session()
    batch = batch_svc.create_batch(s, cid, batch_type="YARN", effective_date=ED, created_by=maker)
    f = OpeningImportFile(company_id=cid, batch_id=batch.id, line_type=OpeningLineType.YARN.value,
                          file_name="t.xlsx", file_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                          content=b"x", uploaded_by=maker)
    s.add(f); s.flush()
    stock_svc.import_rows(s, batch, line_type=OpeningLineType.YARN.value, rows=rows,
                          import_file=f, actor=maker)
    if submit:
        stock_svc.submit_batch(s, cid, batch.id, submitted_by=maker)
    s.commit()
    bid = batch.id
    s.close()
    return bid


def _approve_worker(Session, cid, bid, barrier, results, label):
    """Approve+commit one batch in its own session/transaction. Records the
    wall-clock interval spent inside the operation (for overlap evidence)."""
    from app.services import opening_stock as stock_svc
    s = Session()
    outcome = {"label": label}
    try:
        barrier.wait(timeout=30)   # release both workers together
        outcome["t0"] = time.time()
        stock_svc.approve_batch(s, cid, bid, approved_by=uuid.uuid4())
        s.commit()
        outcome["result"] = "ok"
    except IntegrityError:
        s.rollback(); outcome["result"] = "conflict"
    except Exception as exc:  # noqa: BLE001
        s.rollback(); outcome["result"] = type(exc).__name__
    finally:
        outcome["t1"] = time.time()
        s.close()
        results.append(outcome)


def _run_two(Session, cid, bids, repetitions=1):
    """Run two approve workers concurrently `repetitions` times (fresh schema per
    call is the caller's job). Returns the list of per-worker outcomes."""
    results: list[dict] = []
    barrier = threading.Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as ex:
        ex.submit(_approve_worker, Session, cid, bids[0], barrier, results, "A")
        ex.submit(_approve_worker, Session, cid, bids[1], barrier, results, "B")
    return results


def _overlapped(results) -> bool:
    """Intervals [t0,t1] of the two workers overlap in wall-clock time."""
    a, b = results
    return max(a["t0"], b["t0"]) <= min(a["t1"], b["t1"])


def _counts(Session, cid):
    from app.models.inventory import InventoryTransaction, StockLayer
    from app.models.masters import YarnLot
    s = Session()
    lots = s.execute(select(func.count()).select_from(YarnLot).where(
        YarnLot.company_id == cid)).scalar()
    layers = s.execute(select(func.count()).select_from(StockLayer).where(
        StockLayer.company_id == cid)).scalar()
    txns = s.execute(select(func.count()).select_from(InventoryTransaction).where(
        InventoryTransaction.company_id == cid,
        InventoryTransaction.event_type == "OPENING")).scalar()
    s.close()
    return {"lots": lots, "layers": layers, "txns": txns}


# ------------------------------- Test A -------------------------------------

REPS = int(os.environ.get("PG_RACE_REPS", "5"))


def test_A_same_company_same_new_lot_two_batches():
    """Same company + same new lot in two DRAFT batches, approved concurrently:
    exactly one succeeds, the other gets a deterministic conflict, and exactly one
    lot / layer / opening transaction exists. Repeated to shake out ordering."""
    overlaps = 0
    for i in range(REPS):
        engine, Session = _engine_session()
        cid, _ = _seed_company(Session, f"RA{i}")
        bid1 = _draft_import_submit(Session, cid, [_yrow("DUP-1")])
        bid2 = _draft_import_submit(Session, cid, [_yrow("DUP-1")])
        results = _run_two(Session, cid, [bid1, bid2])
        outcomes = sorted(r["result"] for r in results)
        assert outcomes == ["conflict", "ok"], f"iter {i}: {outcomes}"
        if _overlapped(results):
            overlaps += 1
        c = _counts(Session, cid)
        assert c == {"lots": 1, "layers": 1, "txns": 1}, f"iter {i}: {c}"
        engine.dispose()
    # The concurrent windows genuinely overlapped at least once.
    assert overlaps >= 1, "workers never overlapped — concurrency not demonstrated"


def test_B_same_company_different_lots_both_succeed():
    engine, Session = _engine_session()
    cid, _ = _seed_company(Session, "RB")
    bid1 = _draft_import_submit(Session, cid, [_yrow("LOT-A")])
    bid2 = _draft_import_submit(Session, cid, [_yrow("LOT-B")])
    results = _run_two(Session, cid, [bid1, bid2])
    assert sorted(r["result"] for r in results) == ["ok", "ok"]
    c = _counts(Session, cid)
    assert c == {"lots": 2, "layers": 2, "txns": 2}
    engine.dispose()


def test_C_different_companies_same_lot_both_succeed():
    engine, Session = _engine_session()
    cid1, _ = _seed_company(Session, "RC1")
    cid2, _ = _seed_company(Session, "RC2")
    bid1 = _draft_import_submit(Session, cid1, [_yrow("SHARED")])
    bid2 = _draft_import_submit(Session, cid2, [_yrow("SHARED")])
    results: list[dict] = []
    barrier = threading.Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as ex:
        ex.submit(_approve_worker, Session, cid1, bid1, barrier, results, "C1")
        ex.submit(_approve_worker, Session, cid2, bid2, barrier, results, "C2")
    assert sorted(r["result"] for r in results) == ["ok", "ok"]
    assert _counts(Session, cid1) == {"lots": 1, "layers": 1, "txns": 1}
    assert _counts(Session, cid2) == {"lots": 1, "layers": 1, "txns": 1}
    engine.dispose()


def test_D_retry_after_conflict_still_safe():
    engine, Session = _engine_session()
    from app.services import opening_stock as stock_svc
    cid, _ = _seed_company(Session, "RD")
    bid1 = _draft_import_submit(Session, cid, [_yrow("DUP-1")])
    bid2 = _draft_import_submit(Session, cid, [_yrow("DUP-1")])
    results = _run_two(Session, cid, [bid1, bid2])
    assert sorted(r["result"] for r in results) == ["conflict", "ok"]
    # Identify the loser and retry it now that the winner has committed.
    loser = bid2 if any(r["label"] == "A" and r["result"] == "ok" for r in results) else bid1
    s = Session()
    outcome = "ok?"
    try:
        stock_svc.approve_batch(s, cid, loser, approved_by=uuid.uuid4())
        s.commit(); outcome = "ok"
    except (IntegrityError, stock_svc.OpeningStockError) as exc:
        s.rollback(); outcome = type(exc).__name__
    s.close()
    assert outcome != "ok", "retry must not silently succeed and double-count"
    assert _counts(Session, cid) == {"lots": 1, "layers": 1, "txns": 1}
    engine.dispose()


def test_E_existing_lot_unaffected():
    engine, Session = _engine_session()
    from app.models.inventory import ItemType
    from app.models.masters import YarnLot
    from app.services import inventory, opening_stock as stock_svc
    cid, yid = _seed_company(Session, "RE")
    # Pre-existing lot WITHOUT opening stock.
    s = Session()
    from app.models.masters import Warehouse
    wh = s.execute(select(Warehouse).where(Warehouse.company_id == cid)).scalars().first()
    lot = YarnLot(company_id=cid, yarn_id=yid, internal_lot_no="EX-1",
                  supplier_lot_no="SUP-ORIG", warehouse_id=wh.id, unit_cost=200)
    s.add(lot); s.commit()
    lot_id, sup_ref = lot.id, lot.supplier_lot_no
    s.close()

    bid = _draft_import_submit(Session, cid, [_yrow("EX-1")])
    s = Session()
    stock_svc.approve_batch(s, cid, bid, approved_by=uuid.uuid4()); s.commit(); s.close()

    s = Session()
    lots = s.execute(select(YarnLot).where(YarnLot.company_id == cid)).scalars().all()
    assert len(lots) == 1 and lots[0].id == lot_id and lots[0].supplier_lot_no == sup_ref
    bal = inventory.balance(s, company_id=cid, item_type=ItemType.YARN, item_id=yid, lot_id=lot_id)
    assert str(bal["qty_on_hand"]) in ("100.000000", "100.000")
    s.close()
    engine.dispose()


def test_F_rollback_on_midway_failure():
    """Force a failure AFTER the YarnLot is registered but before the opening
    receipt completes; the whole transaction must roll back leaving nothing."""
    engine, Session = _engine_session()
    from unittest.mock import patch
    from app.services import opening_stock as stock_svc
    cid, _ = _seed_company(Session, "RF")
    bid = _draft_import_submit(Session, cid, [_yrow("FAIL-1")])
    s = Session()
    boom = RuntimeError("injected failure after lot registration")
    with patch.object(stock_svc.inventory, "receive", side_effect=boom):
        raised = False
        try:
            stock_svc.approve_batch(s, cid, bid, approved_by=uuid.uuid4())
            s.commit()
        except RuntimeError:
            s.rollback(); raised = True
    s.close()
    assert raised
    assert _counts(Session, cid) == {"lots": 0, "layers": 0, "txns": 0}
    engine.dispose()
