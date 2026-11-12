"""Per-company database routing (database-per-company).

A company's company-scoped data lives either in the default database or, once
registered in ``company_databases`` (the control plane, in the default DB), in a
dedicated database at its DSN. This module resolves the right engine/session for a
given company and caches one engine per distinct DSN.

Design (catalog + shards):
- The **control/default database** holds the global tables (users, roles,
  memberships, organizations, companies, and the ``company_databases`` registry).
- Each **company database** holds that company's company-scoped data. The schema
  is identical (same Alembic migrations), so provisioning is ``alembic upgrade
  head`` against the company DSN plus company-scoped seeding.

Backward compatible: a company with no active registry row resolves to the default
engine, so single-database operation is unchanged until a company is provisioned.
"""
from __future__ import annotations

import os
import re
import threading
import uuid
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import SessionLocal as ControlSessionLocal
from app.core.database import _make_engine, engine as default_engine

_engine_cache: dict[str, Engine] = {}
_cache_lock = threading.Lock()

_DSN_VAR = re.compile(r"\$\{([A-Z0-9_]+)\}")


def expand_dsn(dsn: str) -> str:
    """Expand ``${ENV_VAR}`` placeholders in a stored DSN from the environment.

    Registry DSNs should carry a placeholder for the password, e.g.
    ``postgresql+psycopg2://erp_app:${ERP_COMPANY_DB_PW}@host:5432/db``, so the
    secret is never stored in the ``company_databases`` table. A missing variable
    expands to empty (fails closed: the connection is refused rather than using a
    default password)."""
    return _DSN_VAR.sub(lambda m: os.environ.get(m.group(1), ""), dsn)


def mask_dsn(dsn: str) -> str:
    """Redact the password in a DSN for safe logging/display.

    ``${VAR}`` placeholders are left visible (they are not secrets); a literal
    password between ``:`` and ``@`` is replaced with ``***``."""
    if dsn is None:
        return ""
    def _redact(m):
        user = m.group(1)
        return f"{user}:***@"
    # user:password@  ->  user:***@   (skip when the password is a ${VAR})
    return re.sub(r"([^/:@]+):(?!\$\{)[^@/]+@", _redact, dsn)


def get_engine_for_dsn(dsn: str) -> Engine:
    """Return a cached engine for ``dsn`` (one engine per distinct stored DSN).

    The stored DSN may contain ``${ENV_VAR}`` placeholders; they are expanded from
    the environment only when the engine is built, so secrets never live in the
    registry or the cache key."""
    with _cache_lock:
        eng = _engine_cache.get(dsn)
        if eng is None:
            eng = _make_engine(expand_dsn(dsn))
            _engine_cache[dsn] = eng
        return eng


def resolve_company_dsn(company_id: uuid.UUID) -> str | None:
    """Look up a company's dedicated-database DSN in the control registry.

    Returns None when the company has no active dedicated database (meaning it
    stays on the default database). The lookup uses a short control-DB session.
    """
    # Imported lazily to avoid a model/engine import cycle at module load.
    from sqlalchemy import select

    from app.models.tenancy import CompanyDatabase

    control = ControlSessionLocal()
    try:
        row = control.execute(
            select(CompanyDatabase).where(
                CompanyDatabase.company_id == company_id,
                CompanyDatabase.is_active.is_(True),
            )
        ).scalar_one_or_none()
        return row.dsn if row is not None else None
    finally:
        control.close()


def engine_for_company(company_id: uuid.UUID | None) -> Engine:
    """The engine that holds ``company_id``'s company-scoped data.

    Falls back to the default engine when the company is unregistered or None.
    """
    if company_id is None:
        return default_engine
    dsn = resolve_company_dsn(company_id)
    if not dsn:
        return default_engine
    return get_engine_for_dsn(dsn)


def sessionmaker_for_company(company_id: uuid.UUID | None) -> sessionmaker:
    return sessionmaker(bind=engine_for_company(company_id),
                        autoflush=False, autocommit=False, future=True)


@contextmanager
def company_session(company_id: uuid.UUID | None) -> Generator[Session, None, None]:
    """Context manager yielding a session bound to the company's database."""
    maker = sessionmaker_for_company(company_id)
    db = maker()
    try:
        yield db
    finally:
        db.close()


def reset_engine_cache() -> None:
    """Dispose and clear cached engines (tests / config reloads)."""
    with _cache_lock:
        for eng in _engine_cache.values():
            try:
                eng.dispose()
            except Exception:
                pass
        _engine_cache.clear()
