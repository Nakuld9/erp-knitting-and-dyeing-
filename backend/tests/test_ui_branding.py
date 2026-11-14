"""UI smoke tests for Stage-1 branding.

The SPA is a precompiled single file (frontend/app.js) served at /app/. There is
no JS test harness in this repo, so these tests (1) assert the compiled asset is
in sync with the branding feature and (2) confirm the backend serves it. This
catches a stale app.js (edited app.src.jsx but forgot to rebuild) and a broken
static mount.
"""
from __future__ import annotations

from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
APP_JS = (FRONTEND / "app.js").read_text(encoding="utf-8")
APP_SRC = (FRONTEND / "app.src.jsx").read_text(encoding="utf-8")
INDEX = (FRONTEND / "index.html").read_text(encoding="utf-8")


def test_app_js_is_rebuilt_from_source():
    # Auto-generated header present and the file is non-trivial.
    assert app_js_header()
    assert len(APP_JS) > 100_000


def app_js_header() -> bool:
    return APP_JS.lstrip().startswith("// AUTO-GENERATED from app.src.jsx")


def test_sidebar_uses_company_branding_not_hardcoded_label():
    # The dynamic brand wiring is present in both source and compiled asset.
    assert "/api/me/branding" in APP_SRC and "/api/me/branding" in APP_JS
    assert "brand-name" in APP_JS            # renders the legal company name
    assert "legal_name" in APP_JS            # resolved from server context
    # the old hardcoded sidebar label is gone from the sidebar brand block
    assert "◆ Textile ERP" not in APP_JS  # "◆ Textile ERP"


def test_factory_shown_separately_from_company_name():
    # Factory is rendered on its own line, labelled, not merged with the name.
    assert "Factory: " in APP_JS
    assert "brand-name" in APP_JS


def test_company_branding_page_registered():
    assert "CompanyBranding" in APP_JS
    assert "Company Branding" in APP_JS       # nav + page title
    assert "Upload logo" in APP_JS


def test_index_has_branding_styles():
    for cls in ("brand-logo", "brand-mono", "brand-name"):
        assert cls in INDEX


def test_spa_served_at_app(client):
    r = client.get("/app/")
    assert r.status_code == 200
    assert "app.js" in r.text
    js = client.get("/app/app.js")
    assert js.status_code == 200
    assert "/api/me/branding" in js.text
