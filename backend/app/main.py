"""FastAPI application entrypoint."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    accounts,
    admin,
    assets,
    auth,
    banking,
    costing,
    dashboard,
    hardware,
    knitting,
    masters,
    operations,
    orders,
    payroll,
    quality,
    reports,
    rolls,
    vouchers,
)
from app.core.config import settings

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
app.include_router(banking.router)
app.include_router(dashboard.router)
app.include_router(quality.router)
app.include_router(costing.router)
app.include_router(knitting.router)
app.include_router(hardware.router)
app.include_router(rolls.router)
app.include_router(admin.router)
app.include_router(assets.router)


@app.get("/health", tags=["system"])
def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}


# Serve the web UI (static SPA) at /app, if present.
_FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
if _FRONTEND.is_dir():
    app.mount("/app", StaticFiles(directory=str(_FRONTEND), html=True), name="frontend")
