"""Stage 6F-P3 — PostgreSQL concurrency acceptance for manufactured-roll opening.

These need a real PostgreSQL server and run only when PG_TEST_URL is set, e.g.:

    PG_TEST_URL='postgresql+psycopg2://erp@/erp_test?host=/tmp/pgtest_p3/sock&port=54341' \
        pytest tests/test_opening_rolls_pg_concurrency.py

Each worker uses its own session/transaction; a ``threading.Barrier`` forces the
two approvals to overlap, and each records its wall-clock interval. The database
unique indexes (uq_fabric_rolls_company_roll_no / uq_fabric_rolls_company_legacy_label)
are the authority — the losing INSERT fails, rolls back and leaves no roll, layer
or inventory transaction. Final DB counts are asserted.

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
    from app.models.hybrid import WarehouseCategory
    from app.models.masters import FabricType, UnitOfMeasure, Warehouse
    s = Session()
    comp = Company(code=code, name=code); s.add(comp); s.flush()
    uom = s.execute(select(UnitOfMeasure).where(UnitOfMeasure.code == "KG")).scalars().first()
    if uom is None:
        uom = UnitOfMeasure(code="KG", name="Kilogram", dimension="MASS", base_factor=1); s.add(uom)
    fac = Branch(company_id=comp.id, code="FAC1", name="F1"); s.add(fac); s.flush()
    s.add_all([
        Warehouse(company_id=comp.id, code="GWH", name="Greige", kind="GREIGE",
                  category=WarehouseCategory.GREIGE.value, branch_id=fac.id),
        FabricType(company_id=comp.id, code="SJ160", name="SJ160"),
    ])
    s.commit()
    out = comp.id
    s.close()
    return out


def _rrow(legacy="HW-1", row=3):
    return {"erp_roll_no": None, "legacy_label": legacy, "roll_kind": "GREIGE",
            "origin_stream": None, "fabric_sku_or_structure": "SJ160", "gsm": "160",
            "width_cm": "180", "net_weight_kg": "42.5", "uom_code": "KG",
            "warehouse_code": "GWH", "factory_code": "FAC1", "quality_status": "OK",
            "unit_cost": "300", "total_value": "12750", "effective_date": None,
            "source_document": "OPEN", "physical_verification_status": "VERIFIED", "_row": row}


def _draft_submit(Session, cid, rows):
    from app.models.opening_stock import OpeningImportFile, OpeningLineType
    from app.services import opening as batch_svc
    from app.services import opening_rolls as roll_svc
    maker = uuid.uuid4()
    s = Session()
    batch = batch_svc.create_batch(s, cid, batch_type="MANUFACTURED_ROLLS",
                                   effective_date=ED, created_by=maker)
    f = OpeningImportFile(company_id=cid, batch_id=batch.id,
                          line_type=OpeningLineType.MANUFACTURED_ROLLS.value, file_name="r.xlsx",
                          file_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                          content=b"x", uploaded_by=maker)
    s.add(f); s.flush()
    roll_svc.import_rows(s, batch, rows=rows, import_file=f, actor=maker)
    roll_svc.submit_batch(s, cid, batch.id, submitted_by=maker)
    s.commit()
    bid = batch.id
    s.close()
    return bid


def _approve_worker(Session, cid, bid, barrier, results, label):
    from app.services import opening_rolls as roll_svc
    s = Session()
    out = {"label": label}
    try:
        barrier.wait(timeout=30)
        out["t0"] = time.time()
        roll_svc.approve_batch(s, cid, bid, approved_by=uuid.uuid4())
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
            ex.submit(_approve_worker, Session, cid, bid, barrier, results, lbl)
    return results


def _overlapped(results):
    a, b = results
    return max(a["t0"], b["t0"]) <= min(a["t1"], b["t1"])


def _counts(Session, cid):
    from app.models.inventory import InventoryTransaction, StockLayer
    from app.models.manufacturing import FabricRoll
    from app.models.opening_roll import OpeningRollLine
    from app.models.genealogy import RollGenealogyEdge
    s = Session()
    out = {
        "rolls": s.execute(select(func.count()).select_from(FabricRoll).where(
            FabricRoll.company_id == cid)).scalar(),
        "layers": s.execute(select(func.count()).select_from(StockLayer).where(
            StockLayer.company_id == cid)).scalar(),
        "txns": s.execute(select(func.count()).select_from(InventoryTransaction).where(
            InventoryTransaction.company_id == cid,
            InventoryTransaction.event_type == "OPENING")).scalar(),
        "import_edges": s.execute(select(func.count()).select_from(RollGenealogyEdge).where(
            RollGenealogyEdge.company_id == cid, RollGenealogyEdge.process_type == "IMPORT")).scalar(),
        "committed": s.execute(select(func.count()).select_from(OpeningRollLine).where(
            OpeningRollLine.company_id == cid, OpeningRollLine.validation_status == "COMMITTED")).scalar(),
    }
    s.close()
    return out


def test_A_concurrent_same_legacy_label():
    overlaps = 0
    for i in range(REPS):
        engine, Session = _engine_session()
        cid = _seed_company(Session, f"PA{i}")
        b1 = _draft_submit(Session, cid, [_rrow(legacy="DUP")])
        b2 = _draft_submit(Session, cid, [_rrow(legacy="DUP")])
        results = _run_two(Session, [(cid, b1, "A"), (cid, b2, "B")])
        assert sorted(r["result"] for r in results) == ["conflict", "ok"], f"iter {i}"
        if _overlapped(results):
            overlaps += 1
        assert _counts(Session, cid) == {"rolls": 1, "layers": 1, "txns": 1,
                                         "import_edges": 1, "committed": 1}, f"iter {i}"
        engine.dispose()
    assert overlaps >= 1


def test_B_concurrent_different_rolls_both_succeed():
    engine, Session = _engine_session()
    cid = _seed_company(Session, "PB")
    b1 = _draft_submit(Session, cid, [_rrow(legacy="L-A")])
    b2 = _draft_submit(Session, cid, [_rrow(legacy="L-B")])
    results = _run_two(Session, [(cid, b1, "A"), (cid, b2, "B")])
    assert sorted(r["result"] for r in results) == ["ok", "ok"]
    assert _counts(Session, cid) == {"rolls": 2, "layers": 2, "txns": 2,
                                     "import_edges": 2, "committed": 2}
    engine.dispose()


def test_C_different_companies_same_label_isolated():
    engine, Session = _engine_session()
    c1 = _seed_company(Session, "PC1")
    c2 = _seed_company(Session, "PC2")
    b1 = _draft_submit(Session, c1, [_rrow(legacy="SHARED")])
    b2 = _draft_submit(Session, c2, [_rrow(legacy="SHARED")])
    results = _run_two(Session, [(c1, b1, "A"), (c2, b2, "B")])
    assert sorted(r["result"] for r in results) == ["ok", "ok"]
    assert _counts(Session, c1)["rolls"] == 1 and _counts(Session, c2)["rolls"] == 1
    engine.dispose()


def test_D_retry_after_conflict_safe():
    engine, Session = _engine_session()
    from app.services import opening_rolls as roll_svc
    cid = _seed_company(Session, "PD")
    b1 = _draft_submit(Session, cid, [_rrow(legacy="DUP")])
    b2 = _draft_submit(Session, cid, [_rrow(legacy="DUP")])
    results = _run_two(Session, [(cid, b1, "A"), (cid, b2, "B")])
    assert sorted(r["result"] for r in results) == ["conflict", "ok"]
    loser = b2 if any(r["label"] == "A" and r["result"] == "ok" for r in results) else b1
    s = Session()
    outcome = "ok?"
    try:
        roll_svc.approve_batch(s, cid, loser, approved_by=uuid.uuid4()); s.commit(); outcome = "ok"
    except (IntegrityError, roll_svc.OpeningRollError) as exc:
        s.rollback(); outcome = type(exc).__name__
    s.close()
    assert outcome != "ok"
    assert _counts(Session, cid)["rolls"] == 1
    engine.dispose()


def test_E_reference_existing_no_duplicate():
    engine, Session = _engine_session()
    from app.models.manufacturing import FabricRoll, RollKind
    from app.models.masters import Warehouse
    from app.services import inventory, opening_rolls as roll_svc
    cid = _seed_company(Session, "PE")
    s = Session()
    wh = s.execute(select(Warehouse).where(Warehouse.company_id == cid)).scalars().first()
    roll = FabricRoll(company_id=cid, roll_no="GR-EXIST", kind=RollKind.GREIGE,
                      weight_kg=40, warehouse_id=wh.id)
    s.add(roll); s.commit()
    rid = roll.id
    s.close()

    def _row_ref():
        r = _rrow(legacy=None); r["erp_roll_no"] = "GR-EXIST"; return r

    b = _draft_submit(Session, cid, [_row_ref()])
    s = Session()
    roll_svc.approve_batch(s, cid, b, approved_by=uuid.uuid4()); s.commit(); s.close()
    c = _counts(Session, cid)
    # Still exactly one roll (the existing one), one opening layer, NO import edge.
    assert c["rolls"] == 1 and c["layers"] == 1 and c["import_edges"] == 0
    s = Session()
    bal = inventory.balance(s, company_id=cid, item_type=__import__(
        "app.models.inventory", fromlist=["ItemType"]).ItemType.FABRIC, item_id=rid)
    assert float(bal["qty_on_hand"]) == 42.5
    s.close()
    engine.dispose()


def test_F_rollback_on_failure():
    engine, Session = _engine_session()
    from unittest.mock import patch
    from app.services import opening_rolls as roll_svc
    cid = _seed_company(Session, "PF")
    b = _draft_submit(Session, cid, [_rrow(legacy="FAIL")])
    s = Session()
    with patch.object(roll_svc.inventory, "receive", side_effect=RuntimeError("boom")):
        raised = False
        try:
            roll_svc.approve_batch(s, cid, b, approved_by=uuid.uuid4()); s.commit()
        except RuntimeError:
            s.rollback(); raised = True
    s.close()
    assert raised
    assert _counts(Session, cid) == {"rolls": 0, "layers": 0, "txns": 0,
                                     "import_edges": 0, "committed": 0}
    engine.dispose()
