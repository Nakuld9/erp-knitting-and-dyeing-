"""Provision a dedicated database for a company (database-per-company).

Given a company that exists in the control/default database and an empty target
database DSN, this:
  1. runs the full Alembic migrations against the DSN (identical schema), then
  2. seeds that company's company-scoped reference data in its own database
     (a mirror of the company row, chart of accounts, periods, posting rules,
     voucher types), then
  3. records the mapping in the control registry (``company_databases``).

The empty target database itself must already exist (PostgreSQL: the operator
runs ``CREATE DATABASE`` first; see docs/DATABASE_PER_COMPANY.md). For SQLite the
file is created on connect, which is what the tests use to simulate separate
databases.

Idempotent-ish: re-running against an already-migrated DB upgrades to head and
re-seeds only what is missing (the seed helpers are get-or-create).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from alembic import command
from alembic.config import Config

import app.core.config as config_mod
from app.core.database import SessionLocal as ControlSessionLocal
from app.core.db_router import get_engine_for_dsn

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ProvisioningError(RuntimeError):
    pass


def _alembic_upgrade(dsn: str) -> None:
    """Run migrations to head against an arbitrary DSN.

    env.py reads ``settings.database_url`` at runtime, so we point it at the
    target DSN for the duration of the upgrade (provisioning is a serial admin
    operation, not concurrent)."""
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
    cfg.set_main_option("sqlalchemy.url", dsn)
    original = config_mod.settings.database_url
    try:
        config_mod.settings.database_url = dsn
        command.upgrade(cfg, "head")
    finally:
        config_mod.settings.database_url = original


def _seed_company_scoped(dsn: str, company_id: uuid.UUID, fy_start_year: int) -> None:
    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker

    from app.models.core import Company
    from app.seed.seed import (
        seed_chart_of_accounts,
        seed_periods,
        seed_voucher_types,
    )
    from app.services.accounting.posting_rules import seed_default_posting_rules

    # read the canonical company row from the control DB
    control = ControlSessionLocal()
    try:
        src = control.get(Company, company_id)
        if src is None:
            raise ProvisioningError("Company not found in the control database")
        snapshot = {c.name: getattr(src, c.name) for c in Company.__table__.columns}
        # organization_id is a CENTRAL-only concept (the company database has no
        # organizations table rows); never carry that FK into the company DB.
        snapshot["organization_id"] = None
    finally:
        control.close()

    engine = get_engine_for_dsn(dsn)
    Maker = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = Maker()
    try:
        # mirror the company row (same id) so company-scoped FKs resolve
        if db.get(Company, company_id) is None:
            db.add(Company(**snapshot))
            db.flush()
        company = db.get(Company, company_id)
        seed_voucher_types(db)                      # global-ish, needed for vouchers
        if not db.execute(select(Company).where(Company.id == company_id)).first():
            raise ProvisioningError("company mirror failed")
        # company-scoped reference data (get-or-create inside the helpers)
        seed_chart_of_accounts(db, company)
        seed_default_posting_rules(db, company.id)
        seed_periods(db, company, fy_start_year)
        db.commit()
    finally:
        db.close()


def provision_company_database(company_id: uuid.UUID, dsn: str, *,
                               fy_start_year: int = 2026, register: bool = True) -> dict:
    """Migrate + seed a company's dedicated database and register the mapping."""
    _alembic_upgrade(dsn)
    _seed_company_scoped(dsn, company_id, fy_start_year)

    registered = False
    if register:
        from sqlalchemy import select

        from app.models.tenancy import CompanyDatabase
        control = ControlSessionLocal()
        try:
            row = control.execute(select(CompanyDatabase).where(
                CompanyDatabase.company_id == company_id)).scalar_one_or_none()
            if row is None:
                row = CompanyDatabase(company_id=company_id, dsn=dsn, is_active=True,
                                      provisioned_at=datetime.now(timezone.utc))
                control.add(row)
            else:
                row.dsn = dsn
                row.is_active = True
                row.provisioned_at = datetime.now(timezone.utc)
            control.commit()
            registered = True
        finally:
            control.close()

    return {"company_id": str(company_id), "dsn": dsn, "registered": registered}
