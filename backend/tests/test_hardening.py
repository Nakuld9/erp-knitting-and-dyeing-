"""Production config guard + fail-closed GST place-of-supply."""
from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services.tax import GstStateError, resolve_intra_state


# --------------------------- production config ------------------------------

def _prod(**kw):
    base = dict(environment="production", debug=False,
                secret_key="x" * 40, database_url="postgresql+psycopg2://u:strongpass@db:5432/erp")
    base.update(kw)
    return Settings(**base)


def test_production_rejects_debug():
    with pytest.raises(ValueError):
        _prod(debug=True)


def test_production_rejects_weak_secret():
    with pytest.raises(ValueError):
        _prod(secret_key="short")
    with pytest.raises(ValueError):
        _prod(secret_key="CHANGE-ME-IN-PRODUCTION-use-a-32+-byte-random-value")


def test_production_rejects_default_db_creds():
    with pytest.raises(ValueError):
        _prod(database_url="postgresql+psycopg2://erp:erp@db:5432/erp")


def test_production_accepts_strong_config():
    s = _prod()
    assert s.environment == "production" and s.debug is False


def test_development_is_unrestricted():
    s = Settings(environment="development", debug=True, secret_key="CHANGE-ME")
    assert s.debug is True                              # dev/test never fail closed


# ----------------------------- GST fail-closed ------------------------------

def test_intra_state_resolves_when_codes_present():
    assert resolve_intra_state("03", "03") is True
    assert resolve_intra_state("03", "27") is False


def test_intra_state_fails_closed_when_missing():
    with pytest.raises(GstStateError):
        resolve_intra_state("03", None)
    with pytest.raises(GstStateError):
        resolve_intra_state(None, "03")
    with pytest.raises(GstStateError):
        resolve_intra_state("", "  ")
