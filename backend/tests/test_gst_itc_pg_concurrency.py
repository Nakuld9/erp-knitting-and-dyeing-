"""Stage 6F-P5 GST — PostgreSQL concurrency acceptance for the GST/ITC module.

Needs a real PostgreSQL server; runs only when PG_TEST_URL is set, e.g.:

    PG_TEST_URL='postgresql+psycopg2://erp@/erp_test?host=/tmp/pgtest_gst/sock&port=54355' \
        pytest tests/test_gst_itc_pg_concurrency.py

Barrier-synchronised workers in their own sessions/transactions; wall-clock overlap
asserted. The company-scoped unique indexes are the concurrency authority:

* ``uq_gst_import_period_hash`` — an identical return file can be imported at most once
  per company/type/period (the loser's INSERT rolls back; exactly one import row);
* ``uq_gst_itc_rr_reference`` — a reversal/reclaim reference is unique per company, so a
  duplicate reclaim racing on the same reference cannot double-reclaim ITC.

Isolation: two different companies importing the same period+hash BOTH succeed (the
uniqueness is per company). Not executed in the standard SQLite suite.
"""
from __future__ import annotations

import json
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

REPS = int(os.environ.get("PG_RACE_REPS", "5"))
GSTIN = "27AAAPL1234C1ZV"
SUP_A = "29AABCS1681Q1Z9"
FY = "2026-27"


def _engine_session():
    from app.models import Base
    engine = create_engine(PG_URL, future=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, autoflush=False, future=True)


def _company(Session, code, gstin):
    from app.models.core import Company
    s = Session()
    c = Company(code=code, name=code, gstin=gstin, state_code="27")
    s.add(c)
    s.commit()
    cid = c.id
    s.close()
    return cid


def _2b_bytes(period="102026"):
    return json.dumps({"data": {"gstin": GSTIN, "rtnprd": period, "gendt": "14-11-2026",
        "docdata": {"b2b": [{"ctin": SUP_A, "trdnm": "S", "inv": [
            {"inum": "INV/1", "idt": "05-10-2026", "val": 11800, "pos": "27", "rev": "N",
             "itcavl": "Y", "items": [{"num": 1, "rt": 18, "txval": 10000, "igst": 0,
                                       "cgst": 900, "sgst": 900, "cess": 0}]}]}]}}}).encode()


def _barrier_run(n, work):
    barrier = threading.Barrier(n)
    starts, results = [0.0] * n, [None] * n

    def runner(i):
        barrier.wait()
        starts[i] = time.monotonic()
        results[i] = work(i)

    with ThreadPoolExecutor(max_workers=n) as ex:
        list(ex.map(runner, range(n)))
    overlap = max(starts) - min(starts)
    return results, overlap


@pytest.mark.parametrize("rep", range(REPS))
def test_concurrent_identical_import_one_wins(rep):
    from app.services.gst_portal import importer as imp
    from app.models.gst_itc import GstReturnImport, GstReturnType
    engine, Session = _engine_session()
    cid = _company(Session, f"C{rep}", GSTIN)
    content = _2b_bytes()

    def work(i):
        s = Session()
        try:
            imp.import_return(s, cid, gstin=GSTIN, financial_year=FY, return_period="102026",
                              return_type=GstReturnType.GSTR2B.value, content=content,
                              file_name="2b.json", source="MANUAL_JSON")
            s.commit()
            return "ok"
        except (imp.DuplicateImport, IntegrityError):
            s.rollback()
            return "rejected"
        finally:
            s.close()

    results, overlap = _barrier_run(2, work)
    assert overlap < 0.5
    assert results.count("ok") == 1 and results.count("rejected") == 1
    chk = Session()
    n = chk.execute(select(func.count()).select_from(GstReturnImport).where(
        GstReturnImport.company_id == cid)).scalar()
    chk.close()
    assert n == 1
    engine.dispose()


@pytest.mark.parametrize("rep", range(REPS))
def test_concurrent_duplicate_reclaim_reference_one_wins(rep):
    from app.services.gst_portal import books as books_svc
    from app.services.gst_portal import itc as itc_svc
    from app.models.gst_itc import ItcReversalReclaim, ItcAction
    engine, Session = _engine_session()
    cid = _company(Session, f"R{rep}", GSTIN)

    # Set up a claimed invoice with 900 reversed (cgst 450 / sgst 450).
    s = Session()
    inv = books_svc.capture_purchase(s, cid, gstin=GSTIN, supplier_gstin=SUP_A,
                                     supplier_invoice_no="INV/1", invoice_date=date(2026, 10, 5),
                                     booked_period="102026", taxable_value="10000", cgst="900",
                                     sgst="900")
    from app.models.gst_itc import ItcStatus
    inv.status = ItcStatus.ITC_CLAIMED.value
    s.flush()
    itc_svc.reverse_itc(s, cid, inv.id, tax_period="112026", reference="REV", cgst="450", sgst="450")
    s.commit()
    inv_id = inv.id
    s.close()

    def work(i):
        s = Session()
        try:
            itc_svc.reclaim_itc(s, cid, inv_id, tax_period="122026", reference="RCL-RACE",
                                cgst="450", sgst="450")
            s.commit()
            return "ok"
        except (itc_svc.GstItcStateError, IntegrityError):
            s.rollback()
            return "rejected"
        finally:
            s.close()

    results, overlap = _barrier_run(2, work)
    assert overlap < 0.5
    assert results.count("ok") == 1 and results.count("rejected") == 1
    chk = Session()
    n = chk.execute(select(func.count()).select_from(ItcReversalReclaim).where(
        ItcReversalReclaim.invoice_status_id == inv_id,
        ItcReversalReclaim.action == ItcAction.RECLAIM.value)).scalar()
    # reclaimed_total must never exceed the 900 reversed (no double reclaim).
    from app.models.gst_itc import ItcInvoiceStatus
    row = chk.get(ItcInvoiceStatus, inv_id)
    reclaimed = row.reclaimed_total
    chk.close()
    assert n == 1
    assert reclaimed == __import__("decimal").Decimal("900.00")
    engine.dispose()


def test_company_isolation_same_file_both_import():
    from app.services.gst_portal import importer as imp
    from app.models.gst_itc import GstReturnImport, GstReturnType
    engine, Session = _engine_session()
    c1 = _company(Session, "ISO1", GSTIN)
    c2 = _company(Session, "ISO2", GSTIN)
    content = _2b_bytes()

    def work_for(cid):
        def work(i):
            s = Session()
            try:
                imp.import_return(s, cid, gstin=GSTIN, financial_year=FY, return_period="102026",
                                  return_type=GstReturnType.GSTR2B.value, content=content,
                                  file_name="2b.json", source="MANUAL_JSON")
                s.commit()
                return "ok"
            except (imp.DuplicateImport, IntegrityError):
                s.rollback()
                return "rejected"
            finally:
                s.close()
        return work

    barrier = threading.Barrier(2)

    def runner(cid, out, idx):
        barrier.wait()
        out[idx] = work_for(cid)(0)

    out = [None, None]
    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(runner, c1, out, 0)
        f2 = ex.submit(runner, c2, out, 1)
        f1.result(); f2.result()
    assert out == ["ok", "ok"]   # per-company uniqueness: both succeed
    chk = Session()
    total = chk.execute(select(func.count()).select_from(GstReturnImport)).scalar()
    chk.close()
    assert total == 2
    engine.dispose()
