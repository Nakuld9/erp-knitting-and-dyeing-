"""Stage 6F-P4 — PostgreSQL concurrency acceptance for vendor-custody opening.

Needs a real PostgreSQL server; runs only when PG_TEST_URL is set, e.g.:

    PG_TEST_URL='postgresql+psycopg2://erp@/erp_test?host=/tmp/pgtest_p4/sock&port=54343' \
        pytest tests/test_opening_custody_pg_concurrency.py

Barrier-synchronised workers in their own sessions/transactions; wall-clock
overlap asserted. The company-scoped unique indexes
(uq_fabric_rolls_company_legacy_label / uq_fabric_rolls_company_roll_no /
uq_yarn_lot_company_internal_no) are the concurrency authority: the losing INSERT
rolls back leaving no roll/lot, no stock layer, no inventory transaction and no
opening-origin JobWorkIssue. Final DB counts asserted.

NOTE: not executed in the standard SQLite suite.
"""
from __future__ import annotations

import hashlib
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
REPS = int(os.environ.get("PG_RACE_REPS", "5"))


def _engine_session():
    from app.models import Base
    engine = create_engine(PG_URL, future=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, autoflush=False, future=True)


def _seed_company(Session, code):
    from app.models.core import Branch, Company
    from app.models.masters import FabricType, Supplier, UnitOfMeasure, YarnMaster
    s = Session()
    comp = Company(code=code, name=code); s.add(comp); s.flush()
    uom = s.execute(select(UnitOfMeasure).where(UnitOfMeasure.code == "KG")).scalars().first()
    if uom is None:
        uom = UnitOfMeasure(code="KG", name="Kilogram", dimension="MASS", base_factor=1); s.add(uom)
    b = Branch(company_id=comp.id, code="FAC1", name="F1"); s.add(b); s.flush()
    s.add_all([Supplier(company_id=comp.id, code="JW1", name="V1", is_job_worker=True),
               YarnMaster(company_id=comp.id, code="Y1", name="Cotton", is_active=True),
               FabricType(company_id=comp.id, code="SJ160", name="SJ160")])
    s.commit()
    out = comp.id
    s.close()
    return out


def _yrow(lot="DUP", row=3):
    return {"line_kind": "VENDOR_CUSTODY", "custody_type": "YARN", "vendor_code": "JW1",
            "process": "KNITTING", "factory_code": "FAC1", "yarn_code": "Y1",
            "internal_lot_no": lot, "net_weight_kg": "50", "uom_code": "KG", "unit_cost": "250",
            "total_value": "12500", "valuation_source": "conf", "effective_date": None,
            "source_document": "OPEN", "_row": row}


def _rrow(legacy="DUP", row=3):
    return {"line_kind": "VENDOR_HELD_WIP", "custody_type": "FABRIC_ROLL", "vendor_code": "JW1",
            "process": "DYEING", "factory_code": "FAC1", "legacy_label": legacy, "roll_kind": "GREIGE",
            "fabric_sku_or_structure": "SJ160", "net_weight_kg": "40", "uom_code": "KG",
            "unit_cost": "300", "total_value": "12000", "valuation_source": "sheet",
            "effective_date": None, "source_document": "OPEN", "_row": row}


def _draft_submit(Session, cid, rows):
    from app.models.opening_stock import OpeningImportFile, OpeningLineType
    from app.services import opening as batch_svc
    from app.services import opening_custody as cust
    maker = uuid.uuid4()
    s = Session()
    batch = batch_svc.create_batch(s, cid, batch_type="VENDOR_CUSTODY", effective_date=ED, created_by=maker)
    f = OpeningImportFile(company_id=cid, batch_id=batch.id,
                          line_type=OpeningLineType.VENDOR_CUSTODY.value, file_name="c.xlsx",
                          file_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                          content=b"x", uploaded_by=maker)
    s.add(f); s.flush()
    cust.import_rows(s, batch, rows=rows, import_file=f, actor=maker)
    cust.submit_batch(s, cid, batch.id, submitted_by=maker)
    s.commit()
    bid = batch.id
    s.close()
    return bid


def _worker(Session, cid, bid, barrier, results, label):
    from app.services import opening_custody as cust
    s = Session()
    out = {"label": label}
    try:
        barrier.wait(timeout=30)
        out["t0"] = time.time()
        cust.approve_batch(s, cid, bid, approved_by=uuid.uuid4())
        s.commit(); out["result"] = "ok"
    except IntegrityError:
        s.rollback(); out["result"] = "conflict"
    except Exception as exc:  # noqa: BLE001
        s.rollback(); out["result"] = type(exc).__name__
    finally:
        out["t1"] = time.time(); s.close(); results.append(out)


def _run_two(Session, pairs):
    results: list[dict] = []
    barrier = threading.Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as ex:
        for cid, bid, lbl in pairs:
            ex.submit(_worker, Session, cid, bid, barrier, results, lbl)
    return results


def _overlapped(results):
    a, b = results
    return max(a["t0"], b["t0"]) <= min(a["t1"], b["t1"])


def _counts(Session, cid):
    from app.models.inventory import InventoryTransaction, StockLayer
    from app.models.manufacturing import FabricRoll
    from app.models.masters import YarnLot
    from app.models.jobwork import JobWorkIssue
    from app.models.opening_custody import OpeningCustodyLine
    s = Session()
    out = {
        "rolls": s.execute(select(func.count()).select_from(FabricRoll).where(
            FabricRoll.company_id == cid)).scalar(),
        "lots": s.execute(select(func.count()).select_from(YarnLot).where(
            YarnLot.company_id == cid)).scalar(),
        "layers": s.execute(select(func.count()).select_from(StockLayer).where(
            StockLayer.company_id == cid)).scalar(),
        "txns": s.execute(select(func.count()).select_from(InventoryTransaction).where(
            InventoryTransaction.company_id == cid,
            InventoryTransaction.event_type == "OPENING")).scalar(),
        "issues": s.execute(select(func.count()).select_from(JobWorkIssue).where(
            JobWorkIssue.company_id == cid)).scalar(),
        "committed": s.execute(select(func.count()).select_from(OpeningCustodyLine).where(
            OpeningCustodyLine.company_id == cid,
            OpeningCustodyLine.validation_status == "COMMITTED")).scalar(),
    }
    s.close()
    return out


def test_A_concurrent_same_legacy_roll():
    overlaps = 0
    for i in range(REPS):
        engine, Session = _engine_session()
        cid = _seed_company(Session, f"QA{i}")
        b1 = _draft_submit(Session, cid, [_rrow(legacy="DUP")])
        b2 = _draft_submit(Session, cid, [_rrow(legacy="DUP")])
        results = _run_two(Session, [(cid, b1, "A"), (cid, b2, "B")])
        assert sorted(r["result"] for r in results) == ["conflict", "ok"], f"iter {i}: {results}"
        if _overlapped(results):
            overlaps += 1
        c = _counts(Session, cid)
        assert c == {"rolls": 1, "lots": 0, "layers": 1, "txns": 1, "issues": 1, "committed": 1}, f"{i}:{c}"
        engine.dispose()
    assert overlaps >= 1


def test_B_concurrent_same_yarn_lot():
    engine, Session = _engine_session()
    cid = _seed_company(Session, "QB")
    b1 = _draft_submit(Session, cid, [_yrow(lot="DUP")])
    b2 = _draft_submit(Session, cid, [_yrow(lot="DUP")])
    results = _run_two(Session, [(cid, b1, "A"), (cid, b2, "B")])
    assert sorted(r["result"] for r in results) == ["conflict", "ok"]
    c = _counts(Session, cid)
    assert c == {"rolls": 0, "lots": 1, "layers": 1, "txns": 1, "issues": 1, "committed": 1}
    engine.dispose()


def test_C_different_companies_isolated():
    engine, Session = _engine_session()
    c1 = _seed_company(Session, "QC1")
    c2 = _seed_company(Session, "QC2")
    b1 = _draft_submit(Session, c1, [_rrow(legacy="SHARED")])
    b2 = _draft_submit(Session, c2, [_rrow(legacy="SHARED")])
    results = _run_two(Session, [(c1, b1, "A"), (c2, b2, "B")])
    assert sorted(r["result"] for r in results) == ["ok", "ok"]
    assert _counts(Session, c1)["rolls"] == 1 and _counts(Session, c2)["rolls"] == 1
    engine.dispose()


def test_D_retry_after_conflict_safe():
    engine, Session = _engine_session()
    from app.services import opening_custody as cust
    cid = _seed_company(Session, "QD")
    b1 = _draft_submit(Session, cid, [_yrow(lot="DUP")])
    b2 = _draft_submit(Session, cid, [_yrow(lot="DUP")])
    results = _run_two(Session, [(cid, b1, "A"), (cid, b2, "B")])
    assert sorted(r["result"] for r in results) == ["conflict", "ok"]
    loser = b2 if any(r["label"] == "A" and r["result"] == "ok" for r in results) else b1
    s = Session()
    outcome = "ok?"
    try:
        cust.approve_batch(s, cid, loser, approved_by=uuid.uuid4()); s.commit(); outcome = "ok"
    except (IntegrityError, cust.OpeningCustodyError) as exc:
        s.rollback(); outcome = type(exc).__name__
    s.close()
    assert outcome != "ok"
    assert _counts(Session, cid)["lots"] == 1
    engine.dispose()


def test_E_rollback_on_failure():
    engine, Session = _engine_session()
    from unittest.mock import patch
    from app.services import opening_custody as cust
    cid = _seed_company(Session, "QE")
    b = _draft_submit(Session, cid, [_yrow(lot="FAIL")])
    s = Session()
    with patch.object(cust.inventory, "receive", side_effect=RuntimeError("boom")):
        raised = False
        try:
            cust.approve_batch(s, cid, b, approved_by=uuid.uuid4()); s.commit()
        except RuntimeError:
            s.rollback(); raised = True
    s.close()
    assert raised
    assert _counts(Session, cid) == {"rolls": 0, "lots": 0, "layers": 0, "txns": 0,
                                     "issues": 0, "committed": 0}
    engine.dispose()
