"""Real-PostgreSQL concurrency proof for document numbering.

SQLite serialises writes, so a genuine race needs Postgres. This runs only when
PG_TEST_URL points at one; otherwise it is skipped (like test_pg_concurrency).

Invariant proven: when many threads issue a number for the SAME source document at
once, exactly ONE number is minted and all callers agree on it — the unique
constraint + sequence row-lock make a duplicate impossible. And when many threads
issue numbers for DISTINCT sources, every number is unique (no collisions).
"""
from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

PG_URL = os.environ.get("PG_TEST_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="PG_TEST_URL not set (needs a real PostgreSQL)")


def _setup():
    from app.models import Base
    from app.models.core import Company
    engine = create_engine(PG_URL, future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    with Session() as s:
        c = Company(code=f"PGP-{uuid.uuid4().hex[:6]}", name="PG Print Co")
        s.add(c); s.commit()
        cid = c.id
    return Session, cid


def test_concurrent_issue_same_source_mints_one_number():
    from app.models.printing import PrintedDocument
    from app.services.printing import numbering as num
    Session, cid = _setup()

    def worker(_):
        with Session() as s:
            row, _created = num.issue_document(
                s, cid, doc_type="PGSAME", source_type="sample", source_id="ONE",
                fiscal_year="2026-2027", prefix="SMP-")
            s.commit()
            return row.number

    with ThreadPoolExecutor(max_workers=16) as ex:
        numbers = set(ex.map(worker, range(16)))
    assert len(numbers) == 1                      # all agree on one number
    with Session() as s:
        n = s.execute(select(func.count()).select_from(PrintedDocument).where(
            PrintedDocument.company_id == cid, PrintedDocument.source_id == "ONE",
            PrintedDocument.doc_type == "PGSAME")).scalar()
        assert n == 1                             # exactly one registry row


def test_concurrent_issue_distinct_sources_no_collision():
    from app.services.printing import numbering as num
    Session, cid = _setup()

    def worker(i):
        with Session() as s:
            row, _ = num.issue_document(
                s, cid, doc_type="PGDIST", source_type="sample", source_id=f"S{i}",
                fiscal_year="2026-2027", prefix="SMP-")
            s.commit()
            return row.number

    with ThreadPoolExecutor(max_workers=16) as ex:
        numbers = list(ex.map(worker, range(40)))
    assert len(numbers) == len(set(numbers)) == 40   # all unique, none duplicated
