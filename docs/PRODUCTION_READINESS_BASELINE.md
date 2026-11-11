# Production Readiness Baseline (Phase 0 audit)

**Date:** 2026-11-11
**Baseline commit:** `de20dac4796c5b57617aca7702827f806f0846da` ("Phase 10: evidence-based remediation report + honest status (263 tests)")
**Baseline branch:** `feat/operability-ui`
**Working branch (this effort):** `feat/multi-company-production-ready` (cut from the baseline commit)
**Working-tree state at audit:** clean (no uncommitted changes); branch in sync with `origin/feat/operability-ui`.

This document is the Phase 0 deliverable of the multi-company / multi-factory
production-readiness programme. It records the **actual** state of the repository
as inspected on the date above — not what prior documentation claims. It is the
reference point against which every later phase is measured.

---

## 1. Current architecture (as built)

| Layer | Reality |
|---|---|
| Backend | Python **FastAPI**, SQLAlchemy 2 ORM, Pydantic schemas. |
| Database | PostgreSQL in production; **SQLite in-memory (StaticPool)** for the test suite. |
| Migrations | **Alembic present but single baseline only** — `0001_initial` whose `upgrade()` calls `Base.metadata.create_all()` and `downgrade()` calls `drop_all()`. No incremental migrations exist yet. |
| Schema bootstrap | Tests (`conftest.py`) and the seed (`app/seed/seed.py`) both build the schema via `Base.metadata.create_all()`, i.e. straight from ORM metadata — **not** via `alembic upgrade`. |
| Accounting | Double-entry posting engine (balanced-only, atomic, idempotent on `posting_reference`, reversal-only corrections, period locks). |
| Inventory | Perpetual FIFO/FEFO + weighted-average, value-based stock layers; PostgreSQL advisory-lock + `FOR UPDATE` on consumption. |
| Manufacturing | Knitting → greige rolls → batching → dyeing (recipe scaling, simulated dispensing) → finishing → quality → FG → dispatch, with roll genealogy. |
| Tax | Indian GST (CGST/SGST/IGST), fail-closed place-of-supply, GSTR-1/3B draft, CN/DN in registers; e-invoice/e-way payload engines + simulator + configurable GSP adapter. |
| Frontend | Self-contained React SPA (`frontend/app.src.jsx` → Babel → `app.js`); vendored React/Recharts; reusable DataTable + LineItems; 4 dashboards. |
| Models | 22 model modules under `backend/app/models/`. |
| Routes | 26 route modules under `backend/app/api/routes/`. |
| Tests | 54 test modules under `backend/tests/`. |

---

## 2. Tenancy reality (the central finding)

The system is **single-company-per-user**, with a *partial* org hierarchy already present:

- `Company` exists — carries `gstin`, `pan`, `state_code` **inline** (one GST registration per company row).
- `Branch` exists — `company_id`, own `gstin`/`state_code`/`address`. This is effectively the "factory/branch" operational entity, already modelled.
- `Department`, `Employee` exist under a company.
- `Warehouse` (`masters.py`) has both `company_id` **and a nullable `branch_id`** — so warehouse→branch linkage already exists in the schema.
- `Role`, `Permission`, `user_roles`, `role_permissions` exist (RBAC is real).
- **`User.company_id` is a single nullable FK.** Authorization resolves the acting company as `user.company_id` directly (23 route files read `user.company_id`). There is **no** `UserCompanyMembership` / `UserFactoryMembership`, **no** group/`Organization` entity, and **no** request-scoped multi-tenant context.
- `require_company_record()` (`core/tenancy.py`) is a fail-closed ownership guard already applied at service boundaries — it checks `model.company_id == company_id`. Solid foundation, but keyed to the single `user.company_id`.
- `company_id` appears on **81** model columns; `factory`/`warehouse` references appear **14** times in models. GST, ledgers, vouchers, masters are company-scoped; production records are **not** factory-scoped yet.

**Implication:** the data model already has Company→Branch→Warehouse bones, but the *access-control* model is single-company. True multi-company/multi-factory requires (a) membership tables, (b) a request-scoped tenant context, (c) factory ownership on operational records, (d) an Organization/group layer, (e) a separate GSTRegistration entity. None of these are destructive to add; the risk is entirely in **backfilling ownership of existing data**, which is a business decision (see §6).

---

## 3. Test results (executed this audit)

Command: `cd backend && .venv/bin/pytest`
Result: **263 passed, 1 skipped, 0 failed, 0 errors.** (exit code 0)

- The 1 skipped test is `tests/test_pg_concurrency.py`, gated on `PG_TEST_URL` (needs a live PostgreSQL). It has been run separately against a real PostgreSQL cluster and passes (proves the FIFO advisory lock prevents double-draw).
- No PostgreSQL-specific tests run in the default suite (SQLite in-memory). This is a gap for a production PostgreSQL target (§5).

---

## 4. Implemented features (verified by a passing test or executed check)

Accounting core, GST registers + CN/DN, perpetual FIFO/FEFO inventory, the
knitting→dyeing→finishing→dispatch manufacturing chain with roll genealogy,
costing/overhead absorption, maker-checker approvals, fixed assets, payroll,
job work, stores/spares, observability (health/ready/metrics, request IDs),
per-IP rate limiting, production config guard, strong-admin-password guard. See
`docs/PROJECT_STATUS.md` for the per-area matrix; those claims were re-confirmed
against the passing suite during this audit.

---

## 5. Production blockers & risks (honest)

| # | Severity | Risk |
|---|---|---|
| B1 | **HIGH** | **No incremental migrations.** Only `0001` (create_all/drop_all). A new *column* on an existing table would never reach an existing production DB. Must switch to real additive Alembic revisions before any schema change ships. |
| B2 | **HIGH** | **No multi-company/factory access control.** Single `user.company_id`; no memberships, no request-scoped tenant context. Cross-company isolation is therefore unproven for multi-tenant use. |
| B3 | **HIGH** | **Legacy data ownership mapping undecided.** Existing rows have no factory/org assignment; backfilling requires a business decision and must not be guessed (data-safety rule). |
| B4 | MED | **No PostgreSQL in default CI.** Concurrency/RLS behaviour is only spot-checked; sequence/number allocation not stress-tested under PG concurrency. |
| B5 | MED | **No inter-company accounting / consolidation / elimination** entries. |
| B6 | MED | **Live integrations unverified** (Color Service, GSP e-invoice/e-way, bank feeds) — simulator/adapters only; vendor-blocked. |
| B7 | MED | **GST not professionally reviewed** for live statutory filing. |
| B8 | LOW | **No independent security review / load test.** HTTPS/CORS are deploy-config concerns. |

---

## 6. Existing-data migration risks (data-safety critical)

- The only safe, non-destructive first step is **additive**: new tables + **nullable** ownership columns, via an incremental Alembic revision, with existing data untouched.
- **Backfill of ownership (company/factory/warehouse) onto existing records is a business decision.** Per the data-safety rules, if a record cannot be mapped with certainty, we stop and ask. We will **not** invent historical factory ownership.
- `NOT NULL` / composite-FK constraints and any column removal come **only after** validated backfill and explicit approval.
- Every schema migration must be preceded by a DB backup and tested on a disposable copy.

---

## 7. Phase gate decision

Phase 0 is **complete**. The system is classified **DEVELOPMENT READY** (single-company,
functionally broad, test-backed) — **not** UAT/production ready for multi-company use.

**Safely executable now (no business decision, non-destructive):**
1. Phase 1 *design* — multi-company/factory ownership matrix + migration strategy (`docs/MULTI_COMPANY_ARCHITECTURE.md`).
2. Phase 1 *additive foundation* — `Organization`, `GSTRegistration`, `UserCompanyMembership`, `UserFactoryMembership` models; nullable `Company.organization_id`; a request-scoped tenant-context resolver that is **backward-compatible** (falls back to `user.company_id`); the first **incremental** Alembic migration (`0002`, additive only); tests.

**Requires business-owner approval before proceeding (Phase 2+):**
- The legacy-data ownership mapping (which existing records belong to which company/factory/warehouse).
- Whether factory-level isolation should be *enforced* on existing production records (mis-mapping could lock users out of their own data).
- Inter-company transaction accounting rules and consolidation/elimination policy.
- Any `NOT NULL`/constraint tightening or column removal on existing tables.

We proceed with the safely executable items and stop at the approval gate.
