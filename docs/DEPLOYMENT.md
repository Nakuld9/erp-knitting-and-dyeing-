# Deployment, Operations & Recovery

Covers installation, configuration, backups/recovery, and upgrades for the
Textile ERP. The web UI is served by the backend at `/app/`; the interactive
API documentation (Swagger) is at `/docs`.

## 1. Requirements

- **Docker** + Docker Compose (recommended), or
- **Python 3.11+** and **PostgreSQL 16** for a manual install.

## 2. Deploy with Docker (recommended)

```bash
cd erp
cp backend/.env.example backend/.env      # then edit (see §4)
docker compose up --build -d
```

This starts PostgreSQL and the API. The API container waits for the database,
runs `alembic upgrade head`, seeds baseline data idempotently, then serves
uvicorn on port 8000.

- API + UI: `http://<host>:8000/app/`
- API docs: `http://<host>:8000/docs`
- Health: `http://<host>:8000/health`

Put a TLS-terminating reverse proxy (nginx/Caddy/ALB) in front for production,
and restrict the Postgres port to the internal network.

## 3. Manual install

```bash
cd erp/backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL="postgresql+psycopg2://erp:erp@localhost:5432/erp"
export SECRET_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')"
alembic upgrade head        # create schema
python -m app.seed.seed     # seed COA, roles, admin, periods, posting rules
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Run behind a process manager (systemd, supervisor) and a reverse proxy. For
throughput, run multiple workers: `uvicorn ... --workers 4` or
`gunicorn -k uvicorn.workers.UvicornWorker app.main:app`.

## 4. Configuration (environment variables)

Set in `backend/.env` (Docker reads it; export them for a manual install). See
`backend/.env.example`.

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL DSN. **Required in production.** |
| `SECRET_KEY` | JWT signing key — a long random value. **Rotate for production.** |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT lifetime (default 480 = one shift). |
| `DEFAULT_TIMEZONE` / `DEFAULT_CURRENCY` | `Asia/Kolkata` / `INR`. |
| `FISCAL_YEAR_START_MONTH` | `4` (April). |
| `ERP_ADMIN_USERNAME/PASSWORD/EMAIL` | Superuser created by the seeder. **Change the password.** |

Never commit real secrets. The seeded demo admin (`admin`/`admin123`) is for
development only — change it before any real use.

## 5. Database migrations

The ORM models are the source of truth; migrations live in `backend/alembic`.

```bash
alembic upgrade head                         # apply
alembic revision --autogenerate -m "change"  # after editing models
alembic downgrade -1                          # roll back one
```

The baseline migration builds the full schema from metadata; subsequent changes
emit explicit operations.

## 6. Backups & recovery

**What to back up:** the PostgreSQL database holds *all* authoritative data
(ledger, inventory, documents, audit log). The container filesystem is
disposable.

**Scheduled logical backup** — use the committed `scripts/backup.sh`. It dumps
the database named by `DATABASE_URL` (PostgreSQL via `pg_dump --format=custom`,
or SQLite via a consistent `.backup`) to a timestamped file in `BACKUP_DIR`,
then prunes files older than `RETENTION_DAYS`. It exits non-zero on failure so
a scheduler can alert.
```bash
DATABASE_URL=... BACKUP_DIR=/var/backups/erp RETENTION_DAYS=30 ./scripts/backup.sh
```
Schedule it from cron (see `scripts/crontab.example`) and **sync `BACKUP_DIR`
off-host** (object storage, another machine) after each run — a backup on the
same box is not a backup.

**Restore** with `scripts/restore.sh`, which directs a Postgres restore at a
*separate* target database by default so you verify before repointing the app:
```bash
TARGET_URL=postgresql://erp:pass@host:5432/erp_restore \
  ./scripts/restore.sh /var/backups/erp/erp-20261108-021700.dump
# SQLite: TARGET_URL=sqlite:////var/lib/erp/erp_restore.sqlite ./scripts/restore.sh <file>.sqlite
```

**Point-in-time recovery:** for stricter RPO, enable Postgres WAL archiving
(`archive_mode=on`) or use your managed database's PITR. Test a restore
regularly — a backup you have never restored is not a backup.

**Integrity after restore:** sign in and open **Reports → Trial Balance** (must
balance), **Stock vs GL Reconciliation** (must reconcile), and the GST
reconciliation — these confirm the ledger and sub-ledgers are consistent.

## 7. Upgrades

1. Back up the database (§6).
2. Pull the new code.
3. `docker compose up --build -d` (or reinstall deps) — the entrypoint runs
   `alembic upgrade head` automatically; for a manual install run it yourself.
4. Verify `/health`, then the three reconciliation reports.

Rebuild the UI bundle only if `frontend/app.src.jsx` changed:
`cd erp/frontend && ./build-ui.sh` (needs npm). The committed `app.js` and
`vendor/` mean the UI runs with no build step otherwise.

## 8. Monitoring & logs

**Endpoints** (no auth, cheap to poll):
- `GET /health` — liveness: the process is up.
- `GET /health/ready` — readiness: the process **and** its database are up
  (`503` otherwise). Point load balancers and orchestrators here so a broken
  instance is drained instead of served traffic. The Docker image ships a
  `HEALTHCHECK` that hits it; `scripts/healthcheck.sh` is the same probe for
  cron/monitoring.
- `GET /health/metrics` — in-process counters since start: `requests_total`,
  `requests_by_status_class` (`2xx`/`4xx`/`5xx`), `errors_total`,
  `avg_duration_ms`, `max_duration_ms`, `uptime_seconds`. Scrape into your
  monitoring stack; alert on a rising `errors_total` or `5xx` rate.

**Request logging.** Every request is logged as one structured line with its
method, path, status, duration and a correlation id. Each response carries an
`X-Request-ID` header (an inbound one is honoured), and that id appears in the
log line and in the body of any `500` — quote it in a bug report to find the
exact request. Set `LOG_LEVEL` (default `INFO`) and `LOG_JSON=true` to emit one
JSON object per line for a log shipper. Unhandled exceptions are logged with a
full traceback and returned to the caller as a clean JSON `500`.

**Audit trail.** The append-only `audit_logs` table records every posting,
reversal and login; `integration_events` records each Color Service exchange
with status for reconciliation. These are the durable record — the in-process
metrics reset on restart.

See `docs/OPERATIONS.md` for the day-2 runbook (backup schedule, what to alert
on, and the incident playbook).

## 9. Security checklist

- [ ] Strong, rotated `SECRET_KEY`; TLS in front of the API.
- [ ] Changed the seeded admin password; least-privilege roles per user (§
      Administrator guide in `USER_GUIDE.md`).
- [ ] Database not publicly reachable; backups encrypted and off-host.
- [ ] Posted ledger immutability and period locks are enforced in-app; do not
      grant raw database write access to end users.
