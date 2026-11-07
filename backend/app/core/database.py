"""Database engine, session factory and FastAPI dependency."""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def _make_engine(url: str) -> Engine:
    connect_args = {}
    kwargs: dict = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        kwargs.pop("pool_pre_ping", None)
        from sqlalchemy.pool import NullPool, StaticPool

        is_memory = (":memory:" in url) or url.rstrip("/").endswith("sqlite:")
        # In-memory (tests): one shared connection so every session sees the same DB.
        # File-backed (dev/demo): a connection per checkout so concurrent requests
        # don't collide on a single SQLite connection.
        kwargs["poolclass"] = StaticPool if is_memory else NullPool
    return create_engine(url, connect_args=connect_args, **kwargs)


engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):  # pragma: no cover - sqlite only
    # Enforce foreign keys on SQLite (off by default) so constraint tests are meaningful.
    module = type(dbapi_connection).__module__
    if "sqlite3" in module:
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a transactional session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
