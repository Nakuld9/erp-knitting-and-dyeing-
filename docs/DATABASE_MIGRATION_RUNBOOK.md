# Database Migration Runbook

**Scope:** how to evolve the schema safely, with the incremental Alembic flow
introduced on `feat/multi-company-production-ready`. These commands are tested
(SQLite round-trip proven; PostgreSQL is the production dialect).

> **Golden rules (data-safety):** back up first; test on a disposable copy first;
> additive-then-backfill-then-constrain; never `drop`/`truncate`/`reset` a
> production database; never use `create_all` as a substitute for a migration.

---

## 1. Migration model

- `alembic/versions/0001_initial.py` — baseline. Builds the full schema from ORM
  metadata (`Base.metadata.create_all`). Used for **fresh** databases.
- `alembic/versions/0002_multicompany_foundation.py` — first **incremental**
  revision. Additive only: creates `organizations`, `gst_registrations`,
  `user_company_memberships`, `user_factory_memberships`, and a nullable
  `companies.organization_id`. Guarded (`_has_table`/`_has_column`) so it is
  idempotent, and uses Alembic **batch mode** for the `companies` column so it
  runs on both PostgreSQL (native ALTER) and SQLite (copy-and-move).
- The DB URL comes from `settings.database_url` (env `DATABASE_URL`), never from
  `alembic.ini` (see `alembic/env.py`).

From 0002 onward, **every schema change is a new revision with explicit `op.*`**
(prefer `alembic revision --autogenerate`, then review the generated ops).

---

## 2. Fresh database (new deployment)

```bash
cd backend
export DATABASE_URL="postgresql+psycopg2://USER:PASS@HOST:5432/DBNAME"
.venv/bin/alembic upgrade head
```

The seed (`app/seed/seed.py`) is applied separately for a brand-new install.

## 3. Existing database (upgrade in place)

```bash
# 1) BACK UP FIRST (see scripts/ backup helper or):
pg_dump "$DATABASE_URL" -Fc -f backup_$(date +%Y%m%d_%H%M%S).dump

# 2) Dry-run on a disposable copy:
createdb erp_copy && pg_restore -d erp_copy backup_*.dump
DATABASE_URL="postgresql+psycopg2://USER:PASS@HOST:5432/erp_copy" \
  .venv/bin/alembic upgrade head          # verify success on the copy
# reconcile: row counts, trial balance, inventory valuation unchanged

# 3) Apply to production (in a maintenance window):
.venv/bin/alembic upgrade head

# 4) Verify:
.venv/bin/alembic current                 # should show the new head
```

## 4. Rollback

```bash
.venv/bin/alembic downgrade -1            # one revision back
# or to a specific revision:
.venv/bin/alembic downgrade 0001_initial
```

`0002`'s `downgrade()` drops only the objects it created (and the nullable
column); it does **not** touch existing business tables. If a migration fails
mid-way, restore from the backup taken in step 1 — do not hand-edit the schema.

## 5. Verify a revision before shipping (automated)

`backend/tests/test_migrations.py` runs `upgrade head → downgrade 0001 → upgrade
head` against a temp SQLite DB and asserts the foundation tables/column appear
and disappear correctly. Run it in CI:

```bash
cd backend && .venv/bin/pytest tests/test_migrations.py -q
```

For PostgreSQL-specific DDL, also run the upgrade/downgrade against a throwaway
PostgreSQL instance before release.

---

## 6. Staged evolution for ownership columns (Phase 2 — approval-gated)

Adding factory ownership to existing operational tables follows the staged plan
(see `docs/MULTI_COMPANY_ARCHITECTURE.md` §5):

1. Add **nullable** `factory_id` (new revision, additive). No data touched.
2. Backfill from an **owner-confirmed** mapping, on a copy first; reconcile.
3. Add indexes / composite FKs.
4. Add `NOT NULL` only once backfill is proven complete for every row.
5. Remove obsolete columns only after verification and sign-off.

Do **not** proceed past step 1 without the business mapping decision.
