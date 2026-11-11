"""Seed security: no predictable administrator password in production."""
from __future__ import annotations

import pytest

from app.core.config import settings
from app.seed import seed


def test_production_requires_strong_admin_password(db, company, monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setenv("ERP_ADMIN_USERNAME", "prodadmin1")
    monkeypatch.delenv("ERP_ADMIN_PASSWORD", raising=False)
    with pytest.raises(RuntimeError):
        seed.seed_admin(db, company, {})


def test_production_rejects_known_weak_password(db, company, monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setenv("ERP_ADMIN_USERNAME", "prodadmin2")
    monkeypatch.setenv("ERP_ADMIN_PASSWORD", "admin123")
    with pytest.raises(RuntimeError):
        seed.seed_admin(db, company, {})


def test_production_accepts_strong_password(db, company, monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setenv("ERP_ADMIN_USERNAME", "prodadmin3")
    monkeypatch.setenv("ERP_ADMIN_EMAIL", "prodadmin3@example.test")
    monkeypatch.setenv("ERP_ADMIN_PASSWORD", "A-Very-Strong-Pass-9271")
    user = seed.seed_admin(db, company, {})
    assert user.username == "prodadmin3" and user.is_superuser is True


def test_development_keeps_convenience_default(db, company, monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setenv("ERP_ADMIN_USERNAME", "devadmin")
    monkeypatch.setenv("ERP_ADMIN_EMAIL", "devadmin@example.test")
    monkeypatch.delenv("ERP_ADMIN_PASSWORD", raising=False)
    user = seed.seed_admin(db, company, {})      # dev may fall back to a default
    assert user.username == "devadmin"
