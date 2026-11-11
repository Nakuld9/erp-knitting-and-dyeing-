"""Operational scaffolding: readiness, metrics, request tracing, error capture."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.observability import Metrics, install_observability


def test_health_is_liveness(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_readiness_reports_database_ok(client):
    r = client.get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready" and body["database"] == "ok"


def test_metrics_endpoint_counts_requests(client):
    client.get("/health")                       # generate some traffic
    r = client.get("/health/metrics")
    assert r.status_code == 200
    snap = r.json()
    assert snap["requests_total"] >= 1
    assert "2xx" in snap["requests_by_status_class"]
    assert set(snap) >= {"uptime_seconds", "errors_total", "avg_duration_ms", "max_duration_ms"}


def test_every_response_carries_a_request_id(client):
    r = client.get("/health")
    assert r.headers.get("X-Request-ID")        # middleware stamps one


def test_inbound_request_id_is_honoured(client):
    r = client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
    assert r.headers.get("X-Request-ID") == "trace-abc-123"


def _isolated_app() -> tuple[TestClient, Metrics]:
    reg = Metrics()
    app = FastAPI()

    @app.get("/boom")
    def boom():
        raise RuntimeError("kaboom")

    @app.get("/ok")
    def ok():
        return {"ok": True}

    install_observability(app, registry=reg)
    return TestClient(app, raise_server_exceptions=False), reg


def test_unhandled_error_becomes_clean_json_500_with_request_id():
    client, reg = _isolated_app()
    r = client.get("/boom")
    assert r.status_code == 500
    body = r.json()
    assert body["detail"] == "Internal server error"
    assert body["request_id"] and r.headers.get("X-Request-ID") == body["request_id"]
    assert reg.errors_total == 1               # counted as a 5xx


def test_metrics_registry_tracks_status_classes():
    client, reg = _isolated_app()
    client.get("/ok")
    client.get("/missing")                      # 404
    client.get("/boom")                         # 500
    snap = reg.snapshot()
    assert snap["requests_total"] == 3
    assert snap["requests_by_status_class"].get("2xx") == 1
    assert snap["requests_by_status_class"].get("4xx") == 1
    assert snap["requests_by_status_class"].get("5xx") == 1
    assert snap["errors_total"] == 1
