# Production Deployment Runbook (Phase 13)

**Date:** 2026-11-11 · **Branch:** `feat/multi-company-production-ready`

Operational runbook for deploying, upgrading, backing up, restoring and rolling
back the ERP. It consolidates `docs/DEPLOYMENT.md`, `docs/OPERATIONS.md`,
`docs/DATABASE_MIGRATION_RUNBOOK.md` and `docs/DATABASE_PER_COMPANY.md`. Items
that still require your sign-off (load test, independent security review,
management acceptance) are marked **GATED** — do not treat the system as
production-accepted until those are done (Phase 16).

> Data-safety rules apply to every step: back up before any migration, test on a
> disposable copy first, never reset/recreate production data.

---

## 1. Assets in the repo

| Asset | Purpose |
|---|---|
| `backend/Dockerfile`, `backend/entrypoint.sh` | container image + startup |
| `docker-compose.yml` | local/reference stack (app + PostgreSQL) with `HEALTHCHECK` |
| `scripts/backup.sh` | database backup (`pg_dump`) |
| `scripts/restore.sh` | database restore (`pg_restore`) |
| `scripts/healthcheck.sh` | liveness/readiness probe |
| `backend/alembic/` | migrations `0001`→`0008` |
| `scripts/provision_company_db.py` | provision a company's dedicated database |

Built-in endpoints: `/health` (liveness), `/health/ready` (DB-checked readiness),
`/health/metrics` (in-process metrics), with per-request `X-Request-ID`.

## 2. Production configuration (enforced)

`app/core/config.py::_validate_production_security` **fails startup** in
production when any of these is unsafe. Set via environment (never commit):

- `ENVIRONMENT=production`
- `DEBUG=false`
- `SECRET_KEY` = a 32+ byte random value (not the default)
- `DATABASE_URL` = real credentials (not the default dev DSN)
- `ERP_ADMIN_PASSWORD` = strong (≥12 chars, not a known-weak value) — enforced by
  `seed_admin`
- GSP / integration secrets (`einvoice_*`, `ewb_*`) from the environment only
- `RATE_LIMIT_ENABLED=true` in production (tight login bucket; `/health` exempt)

TLS/HTTPS is terminated at the reverse proxy / load balancer in front of the app;
configure CORS to the known frontends only.

## 3. First production deploy

```bash
# 1) provision PostgreSQL, create the app database and role.
# 2) set the environment (section 2) in your secret store.
# 3) run migrations to head:
cd backend && .venv/bin/alembic upgrade head
# 4) seed the first company + admin (ERP_ADMIN_PASSWORD must be set & strong):
.venv/bin/python -m app.seed.seed        # or your seeding entrypoint
# 5) start the app (gunicorn/uvicorn workers behind the proxy); verify:
curl -fsS https://<host>/health/ready
```

## 4. Upgrade (existing database)

```bash
# BACK UP FIRST
./scripts/backup.sh                       # pg_dump -Fc
# dry-run migrations on a restored COPY:
createdb erp_copy && ./scripts/restore.sh erp_copy backup_*.dump
DATABASE_URL=...erp_copy .venv/bin/alembic upgrade head   # verify success on copy
# reconcile on the copy: row counts, trial-balance totals, inventory valuation
# then, in a maintenance window, apply to production:
cd backend && .venv/bin/alembic upgrade head
.venv/bin/alembic current                 # should show the new head
curl -fsS https://<host>/health/ready
```

**Database-per-company:** when companies run on dedicated databases, every schema
change must be applied to **each** company database as well (fan-out):
`alembic upgrade head` against each DSN in `company_databases`. See
`docs/DATABASE_PER_COMPANY.md` §5.

## 5. Rollback

Application: redeploy the previous image tag (stateless app; the DB is the state).

Database: prefer **restore-from-backup** over a destructive down-migration.

```bash
# a) app rollback: deploy previous tag.
# b) schema rollback of the last migration (reversible, additive migrations only):
cd backend && .venv/bin/alembic downgrade -1
# c) if data is affected or a migration failed mid-way: restore the pre-upgrade backup:
./scripts/restore.sh erp backup_<pre-upgrade>.dump
```

All migrations `0002`→`0008` are additive with tested `downgrade()`; they do not
drop existing business tables. A failed migration is recovered from the backup
taken in step 4, not by hand-editing the schema.

## 6. Backup & restore

```bash
./scripts/backup.sh                        # scheduled via cron (see OPERATIONS.md)
./scripts/restore.sh <dbname> <dump-file>  # into a fresh/disposable database
```

- Back up **every** database when using database-per-company (control DB + each
  company DB).
- **Restore drills are mandatory and GATED**: prove a full restore into a clean
  database and reconcile balances before declaring production-ready (Phase 16 #14).

## 7. Monitoring & operations

- Liveness `/health`, readiness `/health/ready` (checks DB), metrics
  `/health/metrics`; wire these to your orchestrator and alerting.
- Structured JSON logs with `X-Request-ID` correlation (`log_json=true` in prod).
- Connection pooling: one pool per database; size per expected concurrency (and
  per company database under database-per-company).
- Append-only audit log for posted vouchers and approvals.

## 8. Security checklist (state)

Implemented & tested: JWT auth, 15-role RBAC + granular permissions, company
isolation (membership-validated, forged-id rejection), factory access policy,
production config guard, strong-admin-password guard, per-IP rate limiting,
immutable posted ledger, fail-closed GST place-of-supply, input validation
(Pydantic), parameterized queries (SQLAlchemy — no string-built SQL).

**GATED (require your action before production):**
- Independent **security review / penetration test** — no unresolved critical/high
  findings (Phase 16 #13). Not yet performed.
- **Load test** at the agreed workload (Phase 16 #15). Not yet performed.
- Password-reset flow, session/token-expiry policy review, CSRF posture for any
  cookie-based surface, file-upload scanning — verify against your threat model.
- **GST professional review** before live statutory filing; **GSP sandbox** tests
  before enabling live e-invoice/e-way.

## 9. Production-acceptance gate (Phase 16)

Do **not** mark PRODUCTION READY until, in addition to the above: multi-company &
multi-factory isolation verified on real data, every company's accounting balances
independently, inventory valuation reconciles, manufacturing traceability verified
end-to-end, inter-company reconciles, consolidated reports verified, PostgreSQL
concurrency tests pass, backup restoration proven, deployment+rollback rehearsed,
factory users complete UAT, and management signs off. Current status:
**DEVELOPMENT READY.**
