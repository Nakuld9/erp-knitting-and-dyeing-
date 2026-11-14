"""FastAPI application entrypoint."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.routes import (
    accounting_ops,
    accounts,
    admin,
    approvals,
    assets,
    auth,
    banking,
    bom,
    branding,
    costing,
    dashboard,
    dyeing,
    einvoice,
    ewaybill,
    hardware,
    jobwork,
    inbox,
    inventory_ops,
    knitting,
    masters,
    operations,
    orders,
    payroll,
    presales,
    quality,
    reports,
    rolls,
    stores,
    tenancy,
    transfers,
    vouchers,
)
from app.core.config import settings
from app.core.database import engine
from app.core import observability

observability.configure_logging(settings.log_level, settings.log_json)

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Integrated Textile Manufacturing ERP with a double-entry accounting "
        "engine. Stage 1 foundation: auth/RBAC, master data, chart of accounts, "
        "voucher posting engine, trial balance."
    ),
)

app.include_router(auth.router)
app.include_router(masters.router)
app.include_router(accounts.router)
app.include_router(vouchers.router)
app.include_router(reports.router)
app.include_router(operations.router)
app.include_router(orders.router)
app.include_router(payroll.router)
app.include_router(presales.router)
app.include_router(banking.router)
app.include_router(bom.router)
app.include_router(dyeing.router)
app.include_router(dashboard.router)
app.include_router(quality.router)
app.include_router(costing.router)
app.include_router(knitting.router)
app.include_router(hardware.router)
app.include_router(rolls.router)
app.include_router(inventory_ops.router)
app.include_router(inbox.router)
app.include_router(admin.router)
app.include_router(assets.router)
app.include_router(approvals.router)
app.include_router(accounting_ops.router)
app.include_router(einvoice.router)
app.include_router(ewaybill.router)
app.include_router(jobwork.router)
app.include_router(stores.router)
app.include_router(tenancy.router)
app.include_router(transfers.router)
app.include_router(branding.router)

observability.install_observability(app)

from app.core.ratelimit import install_rate_limiting  # noqa: E402
install_rate_limiting(app, settings)


from fastapi import Request  # noqa: E402
from app.core.tenancy import CompanyOwnershipError  # noqa: E402


@app.exception_handler(CompanyOwnershipError)
def _ownership_handler(request: Request, exc: CompanyOwnershipError):
    """A referenced record is missing or outside the caller's company → 422."""
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/health", tags=["system"])
def health() -> dict:
    """Liveness: the process is up and serving."""
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}


@app.get("/health/ready", tags=["system"])
def readiness():
    """Readiness: the process can reach its database. 503 if it cannot, so an
    orchestrator or load balancer stops routing traffic to a broken instance."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ready", "database": "ok"}
    except Exception as exc:  # noqa: BLE001
        observability.error_logger.warning("readiness check failed: %s", exc)
        return JSONResponse(status_code=503, content={"status": "not-ready", "database": "error"})


@app.get("/health/metrics", tags=["system"])
def metrics() -> dict:
    """In-process request counters since start (requests, status classes,
    error count, latency). Scrape into your monitoring stack."""
    return observability.metrics.snapshot()


# Serve the web UI (static SPA) at /app, if present.
_FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
if _FRONTEND.is_dir():
    app.mount("/app", StaticFiles(directory=str(_FRONTEND), html=True), name="frontend")
