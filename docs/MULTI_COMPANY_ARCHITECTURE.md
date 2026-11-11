# Multi-Company / Multi-Factory Architecture (Phase 1 design)

**Date:** 2026-11-11 · **Branch:** `feat/multi-company-production-ready`
**Status:** design + additive foundation. Enforcement and legacy backfill are gated on business approval (see §6).

This document defines the target organisation model, the ownership matrix for
every record class, the access-control model, and the **non-destructive**
migration strategy. It supersedes the single-`company_id` assumption documented
in the Phase 0 baseline.

---

## 1. Organisation hierarchy

```
Organization (optional group / holding)
└── Company            legal entity — owns the accounts & GST registration(s)
    └── Factory(≈Branch) operational location — owns production, machines, batches
        └── Warehouse    physical inventory location — owns stock & movements
```

- **Organization** — optional top node for group-level consolidated reporting. A
  company with no organization is a standalone legal entity. *New, additive.*
- **Company** — the legal accounting entity. One chart of accounts, one set of
  financial years, its own ledgers and GST. *Exists today.*
- **Factory** — the operational site. **We reuse the existing `Branch` model as
  the Factory/Branch entity** rather than introducing a parallel table; `Branch`
  already carries `company_id`, `gstin`, `state_code`, `address`. A later alias/
  rename is cosmetic and deferred to avoid churn. *Exists today.*
- **Warehouse** — physical stock location; already has `company_id` + nullable
  `branch_id`. *Exists today.*
- **GSTRegistration** — a company may hold more than one GSTIN (per state). We
  extract GST registration into its own table so a company/branch can reference
  the correct registration per transaction. *New, additive; inline `gstin`
  columns are retained for backward compatibility and treated as the "primary".*

---

## 2. Access-control model

Replace "the user's one company" with **explicit memberships**:

- **`UserCompanyMembership`** — (user, company, role-set, is_default). A user may
  belong to several companies.
- **`UserFactoryMembership`** — (user, factory/branch, role-set). Optional; when a
  user has company membership but no factory rows, they see all factories of that
  company (configurable). When factory rows exist, access is restricted to them.
- **Request-scoped `TenantContext`** — resolved per request from the JWT + the
  requested `X-Company-Id` / `X-Factory-Id` (or defaults), **validated against the
  user's memberships server-side**. The frontend is never trusted to assert
  authorization; it only *requests* a scope.

**Backward compatibility (critical):** `User.company_id` is retained as the
user's *default/primary* company. The resolver falls back to it when no
membership rows exist, so **all existing single-company behaviour and the 263
passing tests keep working unchanged**. Memberships are additive on top.

Resolution order for the acting company:
1. Explicit requested company → must be in the user's company memberships (else 403).
2. Else the membership marked `is_default`.
3. Else `User.company_id` (legacy fallback).
4. Else, for a superuser, any company; otherwise 403.

---

## 3. Ownership matrix

| Record class | Owned by | Column(s) | Today | Target |
|---|---|---|---|---|
| Chart of accounts, ledgers, vouchers, periods, FY | **Company** | `company_id` | ✅ | ✅ unchanged |
| Invoices, tax records, GST registers | **Company** (+ `gst_registration_id`) | `company_id` | ✅ | + nullable `gst_registration_id` |
| Customers / suppliers / bank accounts | **Company** | `company_id` | ✅ | ✅ unchanged |
| Fixed assets, payroll | **Company** | `company_id` | ✅ | ✅ |
| Production orders, machines, shifts | **Factory** | `company_id` (+ `factory_id`) | company only | + nullable `factory_id` *(Phase 2, approval-gated backfill)* |
| Greige rolls, dyeing batches, baths, dispensing | **Factory** | `company_id` (+ `factory_id`) | company only | + nullable `factory_id` *(Phase 2)* |
| Quality records, finishing | **Factory** | `company_id` (+ `factory_id`) | company only | + nullable `factory_id` *(Phase 2)* |
| Warehouses | **Factory** | `company_id` + `branch_id` | ✅ (nullable branch) | branch_id populated *(Phase 2)* |
| Stock layers, receipts, issues, transfers, balances | **Warehouse** | `company_id` + `warehouse_id` | company + warehouse | ✅ (warehouse already present) |
| Users, roles, permissions | **Global / membership** | memberships | single `company_id` | + membership tables |
| Organization | **Group** | — | ✅ (new) | ✅ |
| UoM, HSN, tax codes (reference data) | **Shared** (company-agnostic) or per-company | varies | mixed | explicitly classify; no cross-company leakage |

**Factory ownership is added as a nullable column first.** It is only populated
and enforced after the business owner confirms the mapping (§6). Until then the
system behaves exactly as today (company-scoped), so nothing breaks.

---

## 4. Inter-company vs intra-company movement (policy, Phase 8)

- **Same-company factory→factory transfer:** stock movement + goods-in-transit
  only. **No sales/purchase invoice, no GST.** Internal transfer document.
- **Inter-company (Company A → Company B):** a real tax invoice; A books a
  receivable ("Due from related company"), B books a payable ("Due to related
  company"); GST applies per place-of-supply; group consolidation posts an
  **elimination** entry so inter-company revenue/expense is not double-counted.

These rules are **designed, not yet implemented**; they need business sign-off on
the exact ledger accounts and GST treatment before coding (§6).

---

## 5. Migration strategy (non-destructive, staged)

Per the data-safety rules, schema evolution is staged and reversible:

1. **Additive only (this session, migration `0002`):** create new tables
   (`organizations`, `gst_registrations`, `user_company_memberships`,
   `user_factory_memberships`) and add **nullable** `companies.organization_id`.
   No existing column changes, no data touched. Fully reversible.
2. **Backfill (Phase 2, approval-gated):** add nullable `factory_id` to
   operational tables; backfill ownership from an **owner-confirmed** mapping on a
   DB copy; reconcile counts and balances.
3. **Validate:** compare row counts, trial-balance totals, and inventory
   valuation before/after on the copy.
4. **Constrain:** add indexes/composite FKs; add `NOT NULL` only where backfill is
   proven complete.
5. **Clean up:** remove obsolete columns only after verification + approval.

Every step: **back up first, test on a disposable copy first.** `downgrade()` is
provided for each revision.

---

## 6. Business decisions required before Phase 2 (STOP-AND-ASK)

The following cannot be guessed and block enforcement/backfill:

1. **Legacy mapping.** The existing data is one company's. Confirm: which
   factory/branch do existing production records belong to? Is a single default
   factory acceptable, or must rows be split by machine/warehouse?
2. **Factory isolation policy.** Should users be restricted to specific factories,
   or does company membership imply all factories? (Wrong enforcement can lock
   users out of their own data.)
3. **Inter-company accounts & GST.** Confirm the exact "due from/to related
   company" ledger accounts and the GST treatment for inter-company sales.
4. **Consolidation policy.** Which companies consolidate into which organization,
   and the elimination rules.
5. **Multiple GSTINs.** Does any company actually hold more than one GST
   registration today? If not, the extracted table stays optional.

Until these are answered, we ship only the **additive foundation** and keep the
running system behaving exactly as it does today.

---

## 7. Implemented so far (additive, non-destructive)

- **Migration 0002** — `organizations`, `gst_registrations`,
  `user_company_memberships`, `user_factory_memberships`, nullable
  `companies.organization_id`. Upgrade/downgrade proven.
- **Migration 0003** — nullable `factory_id` (→ branches.id) on the factory-owned
  operational tables: `machines`, `knitting_production`, `fabric_rolls`,
  `dye_batches`, `finishing_transactions`, `quality_inspections`,
  `dispensing_transactions`. **Columns only — no backfill, no enforcement.**
- **Resolver** `app/core/context.py` — membership-aware, backward-compatible.
- **API** `app/api/routes/tenancy.py` — `GET /api/me/context` (authorized
  companies/factories + resolved active scope), and `admin.manage`-guarded CRUD
  for organizations, company/factory memberships, and GST registrations.
- **Acting-company route wiring** — `get_current_user` resolves and stamps the
  acting company per request (`set_committed_value`, never persisted); switching
  `X-Company-Id` changes the real dataset. See `docs/ROUTE_WIRING_REPORT.md`.
- **Factory access policy (rule 3)** — explicit `UserFactoryMembership` or the
  company-wide `factory.access_all` permission required; company membership alone
  does not grant factory access.
- **GST (confirmed rule: one active GSTIN per company)** — `GSTRegistration` is
  extensible to many, but the create endpoint enforces one *active* registration
  per company and validates GSTIN format. GST transactions remain company-scoped;
  cross-company GST register isolation is tested.
- **Tests** — foundation (8), migrations (4), tenancy API (9), company switching
  (4). Full suite green.

### Completed since
- **Acting-company route wiring** — switching `X-Company-Id` changes real data
  (`docs/ROUTE_WIRING_REPORT.md`).
- **Legacy/Unassigned factory backfill** tooling (`docs/LEGACY_FACTORY_BACKFILL.md`).
- **Group consolidation** + report-only eliminations (`docs/CONSOLIDATION.md`).
- **Inter-company accounting** — configurable approved mappings, dual-leg atomic
  posting with GST by place of supply, elimination suggestions
  (`docs/INTERCOMPANY_ACCOUNTING.md`).
- **Factory data-filtering (opt-in)** — operational list endpoints narrow to the
  acting factory when an authorized `X-Factory-Id` is in scope; with no factory
  selected the view is company-wide (nothing hidden, legacy rows stay visible).
  Helper `apply_factory_scope` in `app/core/context.py`; wired into machines,
  knitting production, dye batches, finishing, quality inspections, fabric rolls.

### Still blocked on §6 business decisions / later steps
- Running the legacy `factory_id` backfill on real data + reconciliation;
  `NOT NULL`/constraints once reconciled; auto-propagating inter-company documents
  across the full sales/purchase lifecycle.
