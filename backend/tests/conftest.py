"""Pytest fixtures.

Tests run against a shared in-memory SQLite database (fast, no server needed).
The accounting engine validates every invariant in pure Python Decimal and the
Decimal-safe column types round-trip exactly, so these tests genuinely exercise
the production posting logic. Each test gets a freshly created + seeded schema
for isolation.
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef0123456789")
# The test suite shares one client IP and logs in many times; the per-IP limiter
# is proven in its own test, so disable the global one here to avoid 429s.
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal, engine, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from app.models.core import Company, User  # noqa: E402
from app.seed.seed import run_seed  # noqa: E402


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = SessionLocal()
    run_seed(session, fy_start_year=2026)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def company(db):
    return db.execute(select(Company).where(Company.code == "DEMO")).scalar_one()


@pytest.fixture()
def demo(db, company):
    """Sample manufacturing masters + opening stock (Stage 2-4 tests)."""
    from app.seed.demo import seed_manufacturing_demo

    handles = seed_manufacturing_demo(db, company.id)
    db.commit()
    return handles


@pytest.fixture()
def admin(db):
    return db.execute(select(User).where(User.username == "admin")).scalar_one()


@pytest.fixture()
def client(db):
    def _override():
        try:
            yield db
        finally:
            pass  # keep the shared session open across the request

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def token(client):
    resp = client.post("/api/auth/login", data={"username": "admin", "password": "admin123"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture()
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}
