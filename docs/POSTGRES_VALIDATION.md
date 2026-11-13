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

`alembic upgrade head` on a fresh PostgreSQL database applies `0001`→`0012` and
creates **113 base tables**; `alembic current` = `0012_fabric_bom_unique (head)`.
(The original database-per-company foundation run stopped at `0008` / 104 tables;
the later additive migrations `0009`→`0012` added the inter-company saga, the
code-based mappings, the Fabric BOM tables and the BOM uniqueness constraints.)

**Reversibility:** verified **up → down → up to head** on PostgreSQL for the full
`0001`→`0012` range this session, including the Fabric BOM migrations. The
batch-mode migrations (written for SQLite portability) execute as **native
`ALTER`** on PostgreSQL. One dialect detail handled: a `create_index(unique=True)`
surfaces on PostgreSQL as a unique *constraint*, so `0012`'s downgrade drops it
via `DROP CONSTRAINT` on PostgreSQL and `DROP INDEX` on SQLite. No errors.

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

- Migrations up/down/up on PostgreSQL: **PASS** (`0001`→`0012`, 113 base tables; reversible).
- `test_pg_concurrency.py` on PostgreSQL: **1 passed**.
- Database-per-company isolation on two PostgreSQL databases: **ISOLATION OK**.
- Representative suite subset on PostgreSQL: **66 passed, 0 failed**.

## 6b. True two-database cutover validation (database-per-company live routing)

Ran the complete cross-database scenario against **two physically separate
PostgreSQL databases** (control + company A DB + company B DB on one server),
exercising the live routing, the inter-company saga and cross-database
consolidation. Result: **16/16 checks passed** (standalone run) and the gated
pytest `tests/test_uat_two_databases.py` **passed**:

- company A reads only database A; company B only database B;
- switching company changes the actual database connection (distinct engines);
- factory transfer works within a company database;
- the inter-company **saga** posts the seller leg in A's DB and the buyer leg in
  B's DB, COMPLETED and **idempotent** (no duplicate on re-run);
- **consolidation reads both databases** and approved eliminations **remove** the
  inter-company income/expense (income 500000→0, expense 500000→0);
- concurrent per-company sessions **do not leak** database/company context.

Reproduce:
```bash
# three empty databases on the same server, then:
PG_CTL_URL=... PG_A_URL=... PG_B_URL=... \
  .venv/bin/pytest tests/test_uat_two_databases.py
```

Two real cross-database defects were found and fixed during this validation:
provisioning copied `organization_id` into the company DB (central-only FK), and
the inter-company mapping held an FK to `chart_of_accounts` (which lives in the
company DB) — now code-based with the FK dropped on PostgreSQL (migration 0010).

## 6c. Independent backup/restore drill (database-per-company, criterion 14)

Ran a per-company backup/restore drill against real PostgreSQL to prove a
single company's database can be restored **independently** of every other
company, and that restored **accounting balances and inventory quantities**
match the backup point. Script: `scripts/backup_restore_drill.sh` (idempotent,
exits non-zero on any mismatch). Result: **DRILL PASSED**.

Scenario and evidence:

1. Seeded company **A**'s database with a balanced voucher (`journal_lines`
   debit 10000 = credit 10000) and an inventory layer
   (`inventory_valuation_layers` qty 500, value 25000). Company **B**'s
   database held its own unrelated rows.
2. `pg_dump -Fc` of **A only**.
3. **Diverged A** (added an unbalanced 9999 debit line; drained inventory to 0)
   and **changed B** (inserted a sentinel row *after* A's backup).
4. Restored **A** independently: `dropdb A` → `createdb A` → `pg_restore`.
   B was never touched by this.
5. Verified after restore:
   - A ledger **balanced again** at debit 10000 = credit 10000; the 9999
     corruption line is **gone** (2 lines, the backup state);
   - A inventory **restored** to qty 500 / value 25000;
   - B still has its own rows **and** the sentinel inserted after A's backup —
     i.e. restoring A did **not** roll back or overwrite B.

**Platform (control) database recovery dependency.** A restored company
database is only *usable* once the control/platform database is itself
recoverable: authentication, `user_company_memberships`, and the
`company_databases` registry (company → DSN) live there. Recovery order is
therefore **platform DB first, then each company DB**; a company restore
without a matching platform record is inert (no user can be routed to it).
Back up the platform DB on the same schedule as the company DBs.

Reproduce:
```bash
PGHOST=/tmp/pgv2_sock PGPORT=54331 CTL_DB=erp_ctl A_DB=erp_A B_DB=erp_B \
  PGADMIN='su postgres -c' ./scripts/backup_restore_drill.sh
```

## 6d. Fabric BOM on PostgreSQL

The Fabric BOM module (version control, standard cost roll-up, production orders,
factory authorization, honest actual-vs-standard costing, lifecycle controls and
validation) was run against **real PostgreSQL**: **39 BOM + migration tests
passed** (`tests/test_fabric_bom*.py` + `tests/test_migrations.py`), and
migrations `0011`+`0012` round-tripped up/down/up on PostgreSQL.

## 7. Still gated (not done here)

- The **full** suite on PostgreSQL in CI (these runs validated a representative
  subset, the PG-only concurrency path, the two-database cutover, the
  backup/restore drill and the Fabric BOM suite; the full suite runs green on
  SQLite — **376 passed, 2 PG-gated skips**).
- Load/performance testing and the production database-per-company cutover —
  Phase 15/16 on your infrastructure. (Backup/restore is now drilled on real
  PostgreSQL — §6c — but should be rehearsed on your own infrastructure too.)
