# Production Acceptance Checklist (Phase 16)

**Date:** 2026-11-12 · **Branch:** `feat/multi-company-production-ready`
**Current status: DEVELOPMENT READY** (not PRODUCTION READY).

The system may be marked **PRODUCTION READY** only when **every** criterion below
is met with objective evidence, including successful migration/recovery
validation, security sign-off and business-owner acceptance. This checklist maps
each of the 18 criteria to its honest status and evidence. It is deliberately not
marked PRODUCTION READY.

Legend: ✅ done (evidence) · 🟡 partial / validated in sandbox only · ⛔ gated —
needs your infrastructure, a qualified reviewer, or sign-off.

| # | Criterion | Status | Evidence / what's left |
|---|---|---|---|
| 1 | Existing business data safely migrated | 🟡 | Additive migrations `0002`→`0012`, no data touched; **legacy backfill tooling built, not run on your data** (`docs/LEGACY_FACTORY_BACKFILL.md`). Run + reconcile on a copy. |
| 2 | Incremental migrations work on existing DB copies | ✅ | Upgrade/downgrade proven on SQLite **and real PostgreSQL 16** (`docs/POSTGRES_VALIDATION.md`); `tests/test_migrations.py`. |
| 3 | Multi-company isolation verified | ✅ | Membership-validated scope, forged-id → 403, cross-company guard; `tests/test_company_switching.py`, `test_tenancy_api.py`. **Database-per-company LIVE routing** (every company-scoped route → the acting company's database) + saga + cross-database consolidation, validated on two real PostgreSQL databases (`docs/ROUTE_DB_ROUTING.md`, `docs/POSTGRES_VALIDATION.md` §6b, `tests/test_uat_two_databases.py`). |
| 4 | Multi-factory isolation verified | 🟡 | Factory access policy (explicit membership / `factory.access_all`) + opt-in factory data-filtering; `tests/test_factory_filtering.py`. Full enforcement across all operational reads is staged (post legacy backfill). |
| 5 | Accounting balances independently per company | ✅ | `tests/test_uat_multicompany.py` (each company's trial balance balances); immutable ledger, balanced-only posting. |
| 6 | GST calculations professionally reviewed | ⛔ | Computation + draft GSTR-1/3B + fail-closed place-of-supply implemented; **a qualified GST professional must review before live filing** (`docs/INTEGRATIONS.md`). |
| 7 | Inventory valuation reconciles | 🟡 | Perpetual FIFO/FEFO, stock-on-hand by warehouse/factory, GL reconciliation in tests. Reconcile against **your** opening data during UAT. |
| 8 | Manufacturing traceability end-to-end | ✅ | 18–19/20 acceptance tests (`tests/test_operability.py`); roll genealogy + batch traceability. **Fabric BOM** (version-controlled, yarn-only at this stage, migrations `0011`+`0012`): factory-authorized production orders, honest actual-vs-standard costing, lifecycle controls, validation — `docs/FABRIC_BOM.md`. **Grey fabric actual cost + chemical-free dyeing** (migration `0013`): FIFO yarn + independent actual conversion, grey inventory valuation, genuine actual dyed cost/kg with INCOMPLETE flagging, grey→yarn-lot traceability, **balanced double-entry GL postings** (yarn→WIP→greige; greige→dyeing WIP; charges→WIP; WIP→finished — WIP nets to zero, idempotent + DB-unique-constrained, period-locked, reversible) and **inventory-to-GL reconciliation** — `docs/GREY_AND_DYEING_COST.md`, `tests/test_dyeing_*.py`, `tests/test_production_accounting.py` (also green on PostgreSQL). Color-Service steps use the **simulator**. |
| 9 | Inter-company transactions reconcile | ✅ | Dual-leg atomic posting + reconciliation + elimination suggestions; `tests/test_intercompany_posting.py`, `test_uat_multicompany.py`. |
| 10 | Factory transfers work correctly | ✅ | Goods-in-transit workflow, GL-neutral, discrepancy flagged; `tests/test_factory_transfer.py`. |
| 11 | Consolidated reports correct | ✅ | Consolidated trial balance + P&L + eliminations; `tests/test_consolidation.py`, `test_uat_multicompany.py`. |
| 12 | PostgreSQL concurrency tests pass | ✅ | `tests/test_pg_concurrency.py` **passed on real PostgreSQL** (`docs/POSTGRES_VALIDATION.md`). |
| 13 | Security review — no unresolved critical/high | ⛔ | Config guard, RBAC, isolation, rate limiting, strong-admin-password, immutable ledger in place; **independent security review / pen-test not performed**. |
| 14 | Backup restoration proven | ✅ | `scripts/backup.sh` / `restore.sh` + per-company `scripts/backup_restore_drill.sh`. **Independent per-company restore drilled on real PostgreSQL** (`docs/POSTGRES_VALIDATION.md` §6c): A restored to its backup point (ledger re-balanced, inventory qty/value exact); **B untouched**; platform-DB recovery dependency documented. Rehearse once on your own infrastructure. |
| 15 | Load tests pass for agreed workload | ⛔ | **Not performed.** Define the workload and run a load test. |
| 16 | Production deployment + rollback documented | ✅ | `docs/PRODUCTION_DEPLOYMENT_RUNBOOK.md` (deploy/upgrade/rollback/backup, per-DB migration fan-out). Rehearse on staging. |
| 17 | Factory users complete acceptance testing | ⛔ | Plan + automated acceptance ready (`docs/USER_ACCEPTANCE_TEST_PLAN.md`); **real users must run UAT and accept.** |
| 18 | Management sign-off | ⛔ | Not obtained. |

---

## Summary

- **Verified now:** 2, 3, 5, 8, 9, 10, 11, 12, 14, 16 (and the migration/
  concurrency/isolation/**backup-restore** evidence on **real PostgreSQL**).
- **Partial / to run on your data:** 1, 4, 7.
- **Gated on you / external parties:** 6 (GST professional), 13 (security review),
  15 (load test), 17 (user UAT), 18 (management sign-off).

**Do not set PRODUCTION READY** until criteria 1, 4, 6, 7, 13, 15, 17 and 18
are satisfied with evidence. Until then the honest status is **DEVELOPMENT
READY**, progressing toward **PRODUCTION CANDIDATE** once 1/4/7 are completed
on your infrastructure.
