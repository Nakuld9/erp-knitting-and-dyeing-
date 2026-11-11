"""Operational observability: structured logging, request tracing and metrics.

Dependency-free (stdlib ``logging`` only). ``install_observability(app)`` adds a
middleware that
  * assigns every request a short ``X-Request-ID`` (honouring an inbound one),
  * logs one structured line per request (method, path, status, duration),
  * catches any unhandled exception, logs it with its request id and a
    traceback, and returns a clean JSON 500 that carries the request id, and
  * feeds an in-process :class:`Metrics` registry exposed at
    ``GET /health/metrics`` for scraping or a quick liveness glance.

The request id flows to every log record through a :class:`~contextvars.ContextVar`,
so a module logging deep in a service call is correlated to the HTTP request
without threading the id through every signature.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections import defaultdict
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

access_logger = logging.getLogger("erp.access")
error_logger = logging.getLogger("erp.error")


# ------------------------------- metrics ------------------------------------

class Metrics:
    """Lightweight in-process counters. Reset when the process restarts; for a
    durable history, scrape ``/health/metrics`` into your monitoring stack."""

    def __init__(self) -> None:
        self.started = time.time()
        self.requests_total = 0
        self.by_status_class: dict[str, int] = defaultdict(int)
        self.errors_total = 0
        self._duration_sum_ms = 0.0
        self.max_duration_ms = 0.0

    def record(self, status_code: int, duration_ms: float) -> None:
        self.requests_total += 1
        self.by_status_class[f"{status_code // 100}xx"] += 1
        if status_code >= 500:
            self.errors_total += 1
        self._duration_sum_ms += duration_ms
        self.max_duration_ms = max(self.max_duration_ms, duration_ms)

    def snapshot(self) -> dict:
        avg = self._duration_sum_ms / self.requests_total if self.requests_total else 0.0
        return {
            "uptime_seconds": round(time.time() - self.started, 1),
            "requests_total": self.requests_total,
            "requests_by_status_class": dict(sorted(self.by_status_class.items())),
            "errors_total": self.errors_total,
            "avg_duration_ms": round(avg, 2),
            "max_duration_ms": round(self.max_duration_ms, 2),
        }


metrics = Metrics()


# ------------------------------- logging ------------------------------------

class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO", as_json: bool = False) -> None:
    """Install a single stdout handler on the root logger. Idempotent."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()
    handler.addFilter(_RequestIdFilter())
    if as_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-5s %(name)s [%(request_id)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"))
    root.addHandler(handler)


# ------------------------------ middleware ----------------------------------

class ObservabilityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, registry: Metrics) -> None:
        super().__init__(app)
        self.registry = registry

    async def dispatch(self, request: Request, call_next) -> Response:
        rid = (request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16])[:40]
        token = request_id_var.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001 — log and convert to a clean 500
            duration_ms = (time.perf_counter() - start) * 1000
            self.registry.record(500, duration_ms)
            error_logger.exception(
                "unhandled error method=%s path=%s status=500 dur_ms=%.1f",
                request.method, request.url.path, duration_ms)
            request_id_var.reset(token)
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal server error", "request_id": rid},
                headers={"X-Request-ID": rid})
        duration_ms = (time.perf_counter() - start) * 1000
        self.registry.record(response.status_code, duration_ms)
        response.headers["X-Request-ID"] = rid
        level = logging.WARNING if response.status_code >= 500 else logging.INFO
        access_logger.log(
            level, "method=%s path=%s status=%s dur_ms=%.1f",
            request.method, request.url.path, response.status_code, duration_ms)
        request_id_var.reset(token)
        return response


def install_observability(app, registry: Metrics | None = None) -> None:
    app.add_middleware(ObservabilityMiddleware, registry=registry or metrics)
