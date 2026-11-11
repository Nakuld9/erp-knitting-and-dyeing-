"""In-process sliding-window rate limiting (stdlib only).

A per-client-IP limit protects the API from floods and brute-force login
attempts. Health probes are exempt so monitoring is never throttled. Two
buckets: a tight one for the login endpoint, a general one for everything else.

In-process means the counters are per worker; behind multiple workers this gives
each worker its own allowance, which is fine for abuse protection. For a strict
global limit across a fleet, front it with a gateway/Redis limiter — this is the
app-level backstop.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class SlidingWindowLimiter:
    def __init__(self, limit: int, window: float = 60.0) -> None:
        self.limit = max(1, int(limit))
        self.window = float(window)
        self._hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        now = time.monotonic()
        dq = self._hits[key]
        cutoff = now - self.window
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= self.limit:
            return False, int(self.window - (now - dq[0])) + 1
        dq.append(now)
        return True, 0


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, general: SlidingWindowLimiter, login: SlidingWindowLimiter,
                 login_paths: tuple[str, ...] = ("/api/auth/login",),
                 exempt_prefixes: tuple[str, ...] = ("/health",)) -> None:
        super().__init__(app)
        self.general = general
        self.login = login
        self.login_paths = login_paths
        self.exempt = exempt_prefixes

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if any(path.startswith(p) for p in self.exempt):
            return await call_next(request)
        ip = request.client.host if request.client else "unknown"
        is_login = any(path.startswith(p) for p in self.login_paths)
        limiter = self.login if is_login else self.general
        key = f"{'login' if is_login else 'gen'}:{ip}"
        allowed, retry = limiter.allow(key)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests — slow down and retry shortly."},
                headers={"Retry-After": str(retry)})
        return await call_next(request)


def install_rate_limiting(app, settings) -> bool:
    """Install the limiter if enabled. Returns whether it was installed."""
    if not getattr(settings, "rate_limit_enabled", True):
        return False
    app.add_middleware(
        RateLimitMiddleware,
        general=SlidingWindowLimiter(settings.rate_limit_per_minute),
        login=SlidingWindowLimiter(settings.rate_limit_login_per_minute))
    return True
