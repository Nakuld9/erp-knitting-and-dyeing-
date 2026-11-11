# Database-per-Company

**Date:** 2026-11-11 · **Branch:** `feat/multi-company-production-ready`
**Status:** foundation delivered and tested (registry + router + provisioning +
isolation proof). **Live request-path cutover is staged and not yet switched on**
— see §5. Default remains single-database, so nothing existing changes until a
company is provisioned.

You asked for a **separate PostgreSQL database per company**. This documents the
architecture, what is built, the trade-offs (honestly), and the ops.

---

## 1. Architecture (catalog + shards)

```
            ┌──────────────────────────────────────────────┐
            │  CONTROL / DEFAULT database                   │
            │  global tables: users, roles, permissions,    │
            │  user_company_memberships, user_factory_...,  │
            │  organizations, companies, company_databases  │
            └───────────────┬───────────────┬──────────────┘
                            │ registry      │ registry
              ┌─────────────▼──┐        ┌───▼───────────────┐
              │ Company A DB    │        │ Company B DB      │
              │ A's accounting, │        │ B's accounting,   │
              │ inventory, mfg… │        │ inventory, mfg…   │
              └─────────────────┘        └───────────────────┘
```

- **Control database** holds the *global* identity/catalog tables. Authentication
  and membership resolution happen here (a user can span companies, so users
  cannot live inside one company's database).
- **Each company database** holds that company's *company-scoped* data. The schema
  is identical (same Alembic migrations); the company's own `companies` row is
  mirrored into its database so company-scoped foreign keys resolve.
- **`company_databases`** (migration `0008`, in the control DB) maps
  `company_id -> dsn`. A company with no active row stays on the default database.

## 2. What is built (this change)

| Piece | Where |
|---|---|
| Registry model | `app/models/tenancy.py::CompanyDatabase` + migration `0008` |
| Connection router (engine cache, resolve DSN, fall back to default) | `app/core/db_router.py` |
| Request dependency (route a session to the acting company's DB) | `app/core/deps.py::get_company_db` |
| Provisioning (migrate + seed a company DB, register it) | `app/services/company_provisioning.py` |
| CLI | `scripts/provision_company_db.py` |
| Tests (provision, route, **physical isolation**, fallback) | `tests/test_company_db_routing.py` |

Isolation is proven: a customer written to company A's database is **not visible**
in company B's database.

## 3. Provisioning a company's database

PostgreSQL (the operator creates the empty database first):

```bash
# 1) create the empty database as a DB admin
createdb erp_company_acme

# 2) migrate + seed + register it (BACK UP / test on a copy first)
cd backend
.venv/bin/python -m scripts.provision_company_db \
  --company-id <company-uuid> \
  --dsn "postgresql+psycopg2://erp:***@HOST:5432/erp_company_acme"
```

This runs `alembic upgrade head` against the DSN, seeds the company's chart of
accounts / periods / posting rules / voucher types and mirrors its company row,
then records the mapping. Use `--no-register` to migrate+seed without routing to it
yet. Credentials in the DSN are stored in the registry — prefer a DSN whose
password comes from the environment, and treat `company_databases` as
control-plane-only.

## 4. Trade-offs — read before going live

Physical per-company databases give the **strongest isolation**, but they change
what some cross-company features can do, because you can no longer join across
companies in one SQL query:

- **Group consolidation** (`/api/reports/consolidated`) currently sums member
  companies' trial balances in one query. With companies in separate databases it
  must read each company's database separately and aggregate in the application.
  Still possible, but slower and no longer a single transaction.
- **Inter-company posting** currently writes both legs in **one atomic
  transaction**. Across two databases that atomicity is lost; it needs a
  two-phase / saga pattern (post leg A, post leg B, compensate on failure) with a
  reconciliation guarantee. Until that is built, inter-company posting must be
  restricted to companies that share a database.
- **Elimination suggestions** read both companies' control-account balances —
  becomes cross-database reads.
- **Cross-company memberships** still work (they live in the control DB), but the
  data they reach is now in different databases.

These are inherent to database-per-company; they are **not** limitations of the
code. This is exactly the trade-off that was flagged before implementing.

## 5. Staged live cutover (not yet switched on)

The foundation does **not** rip `get_db` out of the existing routes, because a
safe cutover is staged and needs real multi-PostgreSQL validation:

1. **Separate global vs company-scoped tables.** Confirm the global set (users,
   roles, memberships, organizations, companies, registry) and keep them only in
   the control DB; everything else is company-scoped.
2. **Route reads/writes.** Switch company-scoped routes from `get_db` to
   `get_company_db` (already available). Auth stays on the control DB.
3. **Rework cross-company features** (consolidation, inter-company) to the
   cross-database patterns in §4, or gate them.
4. **Per-database migrations.** Every schema change must now
   `alembic upgrade head` against **every** company database (a fan-out step in
   the deploy; see `docs/DATABASE_MIGRATION_RUNBOOK.md`).
5. **Validate on real PostgreSQL** with ≥2 company databases: isolation,
   per-DB migration, backup/restore per database, connection-pool sizing
   (one pool per database).

Until step 2 is executed and validated, companies run on the default database and
behaviour is unchanged. No existing data is moved or deleted by this change.

## 6. Honest status

Foundation: **built and unit-tested** (with simulated separate databases). Full
database-per-company operation: **not production-complete** — it requires the
staged cutover in §5 on real PostgreSQL plus the cross-database rework in §4. The
data-safety rules (no destructive migration, back up first, test on a copy) apply
to every provisioning and cutover step.
