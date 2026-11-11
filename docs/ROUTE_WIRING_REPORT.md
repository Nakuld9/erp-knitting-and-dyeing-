# Acting-Company Route Wiring — Report

**Date:** 2026-11-11 · **Branch:** `feat/multi-company-production-ready`
**Scope:** company-dimension switching only. No accounting or historical-data
migration was started (per instruction). Factory **data** filtering is not wired
(gated on backfill); the factory **access policy** is enforced in the resolver.

---

## 1. Mechanism

The acting company is resolved **once per request** in `get_current_user`
(`app/core/deps.py`):

1. Authenticate the token → load the `User`.
2. Force-load the user's roles/permissions (used by the factory-access check).
3. `resolve_tenant_context(db, user, X-Company-Id, X-Factory-Id)`
   (`app/core/context.py`) validates any client-supplied scope **against the
   user's memberships, server-side**, and returns the authorized acting company
   (and factory). Falls back to the user's default membership, then to
   `User.company_id`.
4. The acting company is stamped onto the request's user with SQLAlchemy
   `set_committed_value(user, "company_id", acting)` — a **clean, non-dirty**
   in-memory value that is **never persisted**. The user's stored default company
   is untouched (no historical-ownership change).

Because every route and service already reads `user.company_id`, this single
boundary makes switching real everywhere at once, with **zero changes to 201
call sites** and no risk of missing one.

**Why not mutate-and-commit or expunge:** a plain assignment would mark the row
dirty and a later `db.commit()` would overwrite the user's stored default;
`expunge` risks `DetachedInstanceError` on lazy attributes. `set_committed_value`
avoids both.

---

## 2. Impact list

- **Services:** `grep` for `user.company_id` in `app/services/` → **0**. Services
  already take `company_id` as a parameter, so no service needed changing.
- **Routes:** **201** `user.company_id` reads across **23** files. All now
  resolve to the **acting company**:

| file | reads | file | reads |
|---|---|---|---|
| masters.py | 32 | operations.py | 24 | reports.py | 20 |
| orders.py | 14 | accounting_ops.py | 10 | inventory_ops.py | 9 |
| costing.py | 8 | hardware.py | 8 | quality.py | 8 |
| approvals.py | 7 | payroll.py | 7 | banking.py | 6 |
| inbox.py | 6 | presales.py | 6 | assets.py | 5 |
| dashboard.py | 5 | jobwork.py | 5 | vouchers.py | 5 |
| accounts.py | 4 | admin.py | 4 | stores.py | 4 |
| knitting.py | 2 | rolls.py | 2 | | |

---

## 3. "Routes still using User.company_id directly" (as required)

**All 23 files above still read `user.company_id` directly — this is now
intended and correct:** `user.company_id` is the request-resolved, membership-
validated acting company, not the stored default. No route was found that:

- decodes a token or authenticates outside `get_current_user` (grep for
  `decode_access_token`/`oauth2_scheme` in routes → none); or
- scopes data by a `company_id` taken from client input.

The only endpoints that accept a client `company_id` are the **admin tenancy
endpoints** (`app/api/routes/tenancy.py`: set-organization, grant-membership,
GST-registration CRUD). These are cross-company **administration** guarded by
`admin.manage`, not data-scoping surfaces.

**Known limitation (not a regression):** those admin endpoints currently operate
across all companies for any `admin.manage` holder (single-admin model). Scoping
company administration to the admin's own companies is future work and is called
out in the architecture doc.

---

## 4. Factory access policy (business rule 3)

Enforced in `resolve_tenant_context`: an `X-Factory-Id` is accepted only if the
factory belongs to the acting company **and** the user has an explicit
`UserFactoryMembership` for it **or** holds the company-wide permission
`factory.access_all` (seeded; granted to `GENERAL_MANAGER`, and to `OWNER`/
`SUPER_ADMIN` via `*`). Company membership alone does **not** grant factory
access. Factory-level **data filtering** on operational records is deferred until
`factory_id` is backfilled (approval-gated).

---

## 5. Backward compatibility

A user with no memberships and no header resolves to their stored
`User.company_id` — identical to prior behaviour. The seeded single-company admin
and all existing flows are unaffected.

---

## 6. Tests

- `test_company_switching.py` (4): switching `X-Company-Id` changes the dataset
  returned by the real `/api/masters/customers` routes (A sees only A, B only B);
  a non-member company → 403; no header → default company; legacy admin still
  works.
- `test_multicompany_foundation.py`: factory policy updated — no factory rows →
  denied; `factory.access_all` → allowed.
- `test_tenancy_api.py`: forged `X-Company-Id` → 403 at the API boundary.
- Full suite re-run for regression (count below).

## 7. PostgreSQL

The wiring is dialect-agnostic (pure ORM/session behaviour); it adds 1–2
membership lookups per request. The existing PostgreSQL concurrency test
(`test_pg_concurrency.py`) remains the gated PG check. No raw SQL was added.

## 8. Not done (as instructed / gated)

- Inter-company accounting, consolidation, factory data-filtering, legacy
  `factory_id` backfill — not started; await business sign-off (architecture doc
  §6). GST remains one-registration-per-company (confirmed business rule).
