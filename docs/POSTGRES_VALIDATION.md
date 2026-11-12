# PostgreSQL Validation (Phase 14 evidence)

**Date:** 2026-11-12 · **Branch:** `feat/multi-company-production-ready`
**Engine:** PostgreSQL 16.15 (throwaway cluster, trust auth, unix socket).

The test suite runs on in-memory SQLite by default. This document records the
checks run against **real PostgreSQL** — the production target — to validate
migrations, concurrency and database-per-company on the real dialect. These are
reproducible (commands below); they are not a substitute for the full Phase 14/15
programme on your own infrastructure.

---

## 1. Incremental migrations on PostgreSQL

`alembic upgrade head` on a fresh PostgreSQL database applied `0001`→`0008` and
created **104 tables**; `alembic current` = `0008_company_databases (head)`.

**Reversibility:** `alembic downgrade 0001_initial` reversed `0008`→`0002`
cleanly, then `alembic upgrade head` re-applied them — the additive tables
(`company_databases`, `factory_stock_transfers`, `intercompany_account_mappings`,
`user_company_memberships`, …) were present again. The batch-mode migrations
(written for SQLite portability) execute as **native `ALTER`** on PostgreSQL. No
errors.

## 2. Concurrency on PostgreSQL

`tests/test_pg_concurrency.py` (skipped on SQLite, `PG_TEST_URL`-gated) **PASSED**
on real PostgreSQL — the FIFO stock draw is serialized by the PostgreSQL advisory
lock + `SELECT … FOR UPDATE`, so concurrent consumption cannot double-draw a
layer.

## 3. Representative suite on PostgreSQL

A cross-section of the suite (accounting invariants, GST, tenancy/isolation,
company switching, inter-company config + dual-leg posting, consolidation, factory
transfer, factory-scoped stock, multi-company foundation) was run with
`DATABASE_URL` pointed at PostgreSQL. Result: **66 passed** (0 failed). No
dialect-specific failures were introduced by the multi-company work.

## 4. Database-per-company on PostgreSQL

Provisioned **two separate PostgreSQL databases**, one per company, from a seeded
control database:

- `provision_company_database(A, dsn=erp_coA)` and `(B, dsn=erp_coB)` ran the full
  migrations + company-scoped seeding against each database and registered the
  mapping.
- The router resolved company A → `erp_coA`, company B → `erp_coB`.
- A customer written to **A's** database (`CUST-A`) was present in `erp_coA` and
  **absent from `erp_coB`** → **physical isolation confirmed on real PostgreSQL.**

This is the real-dialect proof of the database-per-company foundation
(`docs/DATABASE_PER_COMPANY.md`).

## 5. Reproduce

```bash
PGBIN=/usr/lib/postgresql/16/bin ; SOCK=/tmp/pgsock
su postgres -c "$PGBIN/initdb -D /tmp/pgdata -A trust -U postgres"
su postgres -c "$PGBIN/pg_ctl -D /tmp/pgdata -o '-p 54330 -k $SOCK -c listen_addresses=' start"
su postgres -c "$PGBIN/createdb -p 54330 -h $SOCK -U postgres erp_mig"
cd backend
export DATABASE_URL="postgresql+psycopg2://postgres@/erp_mig?host=$SOCK&port=54330"
.venv/bin/alembic upgrade head && .venv/bin/alembic downgrade 0001_initial && .venv/bin/alembic upgrade head
export PG_TEST_URL="postgresql+psycopg2://postgres@/erp_conc?host=$SOCK&port=54330"
.venv/bin/pytest tests/test_pg_concurrency.py
```

## 6. Results recorded

- Migrations up/down/up on PostgreSQL: **PASS** (104 tables; reversible).
- `test_pg_concurrency.py` on PostgreSQL: **1 passed**.
- Database-per-company isolation on two PostgreSQL databases: **ISOLATION OK**.
- Representative suite subset on PostgreSQL: **66 passed, 0 failed**.

## 7. Still gated (not done here)

- The **full** 336-test suite on PostgreSQL in CI (this run validated a
  representative subset + the PG-only concurrency path).
- Load/performance testing, backup-restore drills on PostgreSQL, and the
  production database-per-company cutover — Phase 14/15/16 on your infrastructure.
