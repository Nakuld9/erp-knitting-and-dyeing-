"""Database-per-company: provisioning + routing + isolation.

Simulates separate databases with two SQLite files (the control/default DB is the
in-memory test DB). Proves:
- a company with no registry row falls back to the default engine;
- provisioning migrates + seeds a company's own database and registers it;
- a routed session reaches that company's database;
- data in company A's database is NOT visible in company B's database.
"""
from __future__ import annotations

import os
import tempfile

import pytest
from sqlalchemy import select

from app.core import db_router
from app.core.database import engine as default_engine
from app.models.core import Company
from app.models.masters import Customer


@pytest.fixture()
def temp_dsns():
    paths = []
    def _mk():
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        paths.append(p)
        return f"sqlite:///{p}"
    yield _mk
    db_router.reset_engine_cache()
    for p in paths:
        if os.path.exists(p):
            os.unlink(p)


def _company(db, code):
    c = Company(code=code, name=f"Co {code}", state_code="03")
    db.add(c)
    db.flush()
    return c


def test_unregistered_company_uses_default_engine(db):
    c = _company(db, "DEF1")
    db.commit()
    assert db_router.resolve_company_dsn(c.id) is None
    assert db_router.engine_for_company(c.id) is default_engine
    assert db_router.engine_for_company(None) is default_engine


def test_provision_and_route_to_dedicated_db(db, temp_dsns):
    from app.services.company_provisioning import provision_company_database
    a = _company(db, "SHARDA")
    b = _company(db, "SHARDB")
    db.commit()  # make A/B visible to the control-session used by provisioning

    dsn_a = temp_dsns()
    dsn_b = temp_dsns()
    provision_company_database(a.id, dsn_a, fy_start_year=2026)
    provision_company_database(b.id, dsn_b, fy_start_year=2026)

    # routing resolves each company to its own DSN
    assert db_router.resolve_company_dsn(a.id) == dsn_a
    assert db_router.resolve_company_dsn(b.id) == dsn_b
    assert db_router.engine_for_company(a.id) is not default_engine
    assert db_router.engine_for_company(a.id) is not db_router.engine_for_company(b.id)

    # write a customer into A's database via the routed session
    with db_router.company_session(a.id) as sa:
        sa.add(Customer(company_id=a.id, code="CUST-A", name="Alpha"))
        sa.commit()

    # A's database has it; B's database does NOT (physical isolation)
    with db_router.company_session(a.id) as sa:
        codes_a = {c.code for c in sa.execute(select(Customer)).scalars().all()}
    with db_router.company_session(b.id) as sb:
        codes_b = {c.code for c in sb.execute(select(Customer)).scalars().all()}
    assert "CUST-A" in codes_a
    assert "CUST-A" not in codes_b

    # each company's own chart of accounts was seeded in its own database
    with db_router.company_session(a.id) as sa:
        from app.models.accounting import ChartOfAccount
        n = sa.execute(select(ChartOfAccount).where(ChartOfAccount.company_id == a.id)).scalars().all()
        assert len(n) > 0


def test_deactivated_registry_falls_back(db, temp_dsns):
    from app.services.company_provisioning import provision_company_database
    from app.models.tenancy import CompanyDatabase
    c = _company(db, "DEACT")
    db.commit()
    dsn = temp_dsns()
    provision_company_database(c.id, dsn, fy_start_year=2026)
    assert db_router.engine_for_company(c.id) is not default_engine
    # deactivate the mapping -> routes back to the default engine
    row = db.execute(select(CompanyDatabase).where(CompanyDatabase.company_id == c.id)).scalar_one()
    row.is_active = False
    db.commit()
    assert db_router.resolve_company_dsn(c.id) is None
    assert db_router.engine_for_company(c.id) is default_engine
