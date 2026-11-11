# Legacy / Unassigned Factory Backfill — mapping & consequences

**Date:** 2026-11-11 · **Branch:** `feat/multi-company-production-ready`
**Status:** tooling delivered; **not yet executed against any real database.**

This documents the historical-record mapping approved under business decision
section 2, per the requirement to "document the mapping and its consequences"
before any assignment.

---

## 1. The mapping

Historical operational rows (`machines`, `knitting_production`, `fabric_rolls`,
`dye_batches`, `finishing_transactions`, `quality_inspections`,
`dispensing_transactions`) created before factory ownership existed have
`factory_id IS NULL`. Their **true** factory often cannot be established from the
data.

The backfill assigns each such row to an explicit **Legacy / Unassigned factory**
of the **same company**:

- One `branches` row per company, `code = "LEGACY"`,
  `name = "Legacy / Unassigned (reconcile)"`, `is_legacy_unassigned = True`.
- This is **not an operational factory**. It is a holding bucket.
- Assignment never crosses companies: a row keeps its `company_id`; it only gets
  its own company's legacy factory.

## 2. Consequences (explicit)

- **No invented operational ownership.** Rows are not assigned to any real
  factory. `is_legacy_unassigned = True` marks the bucket so these rows are, by
  definition, **pending reconciliation**.
- **History preserved.** Only the previously-NULL `factory_id` is filled. Company
  ownership, dates, amounts, genealogy and all transaction data are untouched.
- **Not mandatory.** `factory_id` stays nullable; no `NOT NULL`/enforcement is
  added. New rows created after the backfill are **not** auto-assigned (factory
  assignment on create is a separate, later step).
- **Reversible.** `revert_legacy_backfill` clears `factory_id` on legacy-bucket
  rows and removes the empty legacy factories.
- **Visible for reconciliation.** `GET /api/admin/factory-reconciliation` and the
  CLI `--report` list how many rows still sit on a legacy factory, per table, so
  operations staff can reassign them to real factories over time.

## 3. How to run it (deliberate, never silent)

Prerequisite: migration `0004` applied (`branches.is_legacy_unassigned`).

```bash
cd backend
# 0) BACK UP, then test on a disposable COPY first.
# 1) Preview (writes nothing):
.venv/bin/python -m scripts.backfill_legacy_factory
# 2) Apply (explicit):
.venv/bin/python -m scripts.backfill_legacy_factory --commit
# 3) Reconciliation status at any time:
.venv/bin/python -m scripts.backfill_legacy_factory --report
# 4) Undo if needed:
.venv/bin/python -m scripts.backfill_legacy_factory --revert --commit
```

The script is a **preview by default**; it only writes with `--commit`. It is
idempotent (a second run assigns nothing new).

## 4. Reconciliation workflow (post-backfill)

1. Run `--report` (or the admin endpoint) to see rows pending reconciliation.
2. As the real factory for a batch of rows is established, reassign them
   (`UPDATE ... SET factory_id = <real factory>`), reducing the legacy count.
3. Factory ownership is made mandatory (`NOT NULL`) **only after** the legacy
   count reaches zero and is verified — a separate, approval-gated migration.

## 5. What this does NOT do

- Does not enforce factory isolation on queries (data filtering is a later step).
- Does not change GST, accounting, or inter-company behaviour.
- Does not run automatically in any migration or request path.
