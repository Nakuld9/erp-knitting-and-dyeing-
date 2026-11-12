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
- **Inter-company posting** is now a cross-database **saga** (see §5), not a single
  transaction. Two legs, each idempotent, with compensation; live cross-database
  posting is gated by `settings.intercompany_live_posting` (default off).
- **Consolidation / elimination suggestions** read each company's control-account
  balances from that company's own database (cross-database reads; see §6).
- **Cross-company memberships** still work (they live in the control DB), but the
  data they reach is now in different databases.

These are inherent to database-per-company; they are **not** limitations of the
code. This is exactly the trade-off that was flagged before implementing — now
built out.

## 5. Live routing (done) & the cross-database saga

The live cutover is implemented and validated on real PostgreSQL:

1. **Global vs company-scoped tables** are separated — see
   `docs/ROUTE_DB_ROUTING.md` for the route-by-route classification.
2. **Routing is live.** Company-scoped routes use `get_company_db` (routes to the
   acting company's database, falls back to the central DB when a company is
   unregistered); auth/identity/catalog stay on the central DB.
3. **Inter-company posting = saga** (`services/accounting/intercompany_saga.py`):
   an `intercompany_txns` outbox keyed by an idempotency key; seller leg in A's
   DB, buyer leg in B's DB (each idempotent on a derived posting reference); a
   buyer-leg failure **compensates** by reversing the seller leg; `reconcile()`
   drives non-terminal sagas. Mappings are **code-based** (account ids differ per
   DB). **Gated off by default** until proven in prod.
4. **Per-database migrations.** Every schema change must `alembic upgrade head`
   against **every** company database (deploy fan-out; see
   `docs/DATABASE_MIGRATION_RUNBOOK.md`).
5. **Validated on real PostgreSQL** with two databases — see
   `docs/POSTGRES_VALIDATION.md` and `tests/test_uat_two_databases.py`.

## 6. Cross-database consolidation

`services/accounting/consolidation.py` reads **each** member company's trial
balance from that company's own database via `db_router.company_session`, and
aggregates in the reporting service. Eliminations are code-based and apply to the
consolidated figures only — nothing is posted to any company's statutory ledger.
Each company's books stay independent.

## 7. Security — database credentials

- **No plaintext passwords in the registry.** A `company_databases.dsn` should
  carry a `${ENV_VAR}` placeholder for the password, e.g.
  `postgresql+psycopg2://erp_app:${ERP_COMPANY_DB_PW}@host:5432/erp_company_acme`.
  `db_router.expand_dsn` substitutes the secret **only** when the engine is built;
  the secret never lives in the database or the engine cache key. A missing
  variable fails closed (empty, not a default).
- **Masking.** `db_router.mask_dsn` redacts the password for any logging/display;
  the provisioning CLI prints a masked DSN. Credentials must never appear in logs.
- **Restricted roles.** The application connects with a **least-privilege,
  non-superuser** role per database (CRUD on its own schema only). Reserve
  superuser for provisioning/DDL run out of band. Each company database may use a
  distinct role so a leaked credential cannot reach another company's data.

## 8. Honest status

Live routing, cross-database consolidation and the inter-company saga are
**implemented and validated on real PostgreSQL** (migrations up/down, two-database
isolation, saga reconcile, cross-database consolidation + elimination, concurrency
— `docs/POSTGRES_VALIDATION.md`). Still required before production: enabling the
saga flag after a prod pilot, per-database backup/restore drills on your
infrastructure, connection-pool sizing, and the Phase 15/16 sign-offs. The
data-safety rules apply to every provisioning and cutover step.
