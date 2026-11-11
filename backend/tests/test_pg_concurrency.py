"""Concurrency safety of FIFO consumption under *real* PostgreSQL.

SQLite implements neither advisory locks nor row-level FOR UPDATE, so this proof
needs a Postgres server. It runs only when PG_TEST_URL points at one; otherwise
it is skipped. Start a throwaway cluster and:

    PG_TEST_URL='postgresql+psycopg2://erp@/erp_test?host=/tmp/pgtest/sock&port=54329' \
        pytest tests/test_pg_concurrency.py

Invariant proven: with many threads drawing the same item at once, the stock
engine never double-draws a layer — total consumed never exceeds what was on
hand, the remainder never goes negative, and consumed + remaining == available.
Remove the advisory lock / FOR UPDATE and this fails (races over-consume).
"""
from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

PG_URL = os.environ.get("PG_TEST_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="PG_TEST_URL not set (needs a real PostgreSQL)")


def test_concurrent_fifo_consumption_never_double_draws():
    from app.models import Base
    from app.models.core import Company
    from app.models.inventory import ItemType, StockLayer
    from app.services import inventory
    from app.services.inventory.service import InsufficientStockError

    engine = create_engine(PG_URL, future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)

    item_id = uuid.uuid4()
    AVAILABLE = Decimal("100")
    with Session() as s:
        company = Company(code=f"PGT-{uuid.uuid4().hex[:6]}", name="PG Test Co")
        s.add(company); s.flush()
        s.add(StockLayer(company_id=company.id, item_type=ItemType.CHEMICAL.value, item_id=item_id,
                         qty_in=AVAILABLE, qty_remaining=AVAILABLE, unit_cost=Decimal("10"),
                         value_remaining=Decimal("1000")))
        s.commit()
        company_id = company.id

    THREADS, EACH = 12, Decimal("12")     # demand 144 > 100 available

    def worker(_):
        with Session() as s:
            try:
                res = inventory.consume(s, company_id=company_id, item_type=ItemType.CHEMICAL,
                                        item_id=item_id, qty=EACH)
                s.commit()
                return Decimal(str(res["qty"]))
            except InsufficientStockError:
                s.rollback()
                return Decimal("0")

    with ThreadPoolExecutor(max_workers=THREADS) as ex:
        consumed = [r for r in ex.map(worker, range(THREADS))]

    total_consumed = sum(consumed, Decimal("0"))
    with Session() as s:
        remaining = Decimal(str(s.execute(
            select(func.coalesce(func.sum(StockLayer.qty_remaining), 0))
            .where(StockLayer.company_id == company_id)).scalar() or 0))

    assert remaining >= 0, "stock went negative — a race over-consumed"
    assert total_consumed <= AVAILABLE, "consumed more than was available — double draw"
    assert total_consumed + remaining == AVAILABLE, "stock not conserved under concurrency"
    assert total_consumed > 0 and remaining < AVAILABLE   # work actually happened
    assert consumed.count(Decimal("0")) >= 1              # some threads were correctly refused
