"""Rate limiting: per-IP sliding window, tight login bucket, health exempt."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.ratelimit import RateLimitMiddleware, SlidingWindowLimiter


def test_sliding_window_blocks_over_limit():
    lim = SlidingWindowLimiter(limit=3, window=60)
    assert [lim.allow("k")[0] for _ in range(3)] == [True, True, True]
    allowed, retry = lim.allow("k")
    assert allowed is False and retry >= 1


def test_sliding_window_is_per_key():
    lim = SlidingWindowLimiter(limit=1, window=60)
    assert lim.allow("a")[0] is True
    assert lim.allow("b")[0] is True        # different key, own allowance
    assert lim.allow("a")[0] is False


def _app(general=100, login=2):
    app = FastAPI()

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    @app.post("/api/auth/login")
    def login_ep():
        return {"token": "x"}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    app.add_middleware(RateLimitMiddleware,
                       general=SlidingWindowLimiter(general), login=SlidingWindowLimiter(login))
    return TestClient(app)


def test_login_bucket_is_tight():
    c = _app(general=100, login=2)
    assert c.post("/api/auth/login").status_code == 200
    assert c.post("/api/auth/login").status_code == 200
    r = c.post("/api/auth/login")
    assert r.status_code == 429 and r.headers.get("Retry-After")


def test_general_bucket_separate_from_login():
    c = _app(general=100, login=1)
    c.post("/api/auth/login")               # exhaust the login bucket
    assert c.post("/api/auth/login").status_code == 429
    assert c.get("/api/ping").status_code == 200   # general bucket unaffected


def test_health_is_exempt():
    c = _app(general=1, login=1)
    c.get("/api/ping")                      # uses the single general slot
    assert c.get("/api/ping").status_code == 429
    for _ in range(5):                      # health never throttled
        assert c.get("/health").status_code == 200


def test_disabled_install_is_noop():
    from types import SimpleNamespace

    from app.core.ratelimit import install_rate_limiting
    app = FastAPI()
    installed = install_rate_limiting(app, SimpleNamespace(rate_limit_enabled=False))
    assert installed is False
