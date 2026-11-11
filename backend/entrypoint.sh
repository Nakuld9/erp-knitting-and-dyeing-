#!/usr/bin/env bash
set -euo pipefail

echo "[entrypoint] Waiting for database..."
python - <<'PY'
import os, time
from sqlalchemy import create_engine, text

url = os.environ.get("DATABASE_URL", "postgresql+psycopg2://erp:erp@db:5432/erp")
for attempt in range(1, 31):
    try:
        create_engine(url).connect().execute(text("SELECT 1"))
        print("[entrypoint] Database is up.")
        break
    except Exception as exc:  # noqa: BLE001
        print(f"[entrypoint] DB not ready ({attempt}/30): {exc}")
        time.sleep(2)
else:
    raise SystemExit("[entrypoint] Database never became available")
PY

echo "[entrypoint] Running migrations..."
alembic upgrade head

echo "[entrypoint] Seeding baseline data (idempotent)..."
# Fail fast: do not start the API without baseline master/accounting data.
python -m app.seed.seed

echo "[entrypoint] Starting API..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
