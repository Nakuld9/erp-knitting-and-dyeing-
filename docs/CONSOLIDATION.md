# Group Consolidation (read-only)

**Date:** 2026-11-11 · **Branch:** `feat/multi-company-production-ready`
**Status:** read-only consolidated reporting + configurable, approval-gated,
report-only eliminations. Posts nothing to any ledger.

Honours business decision section 5.

---

## 1. What it does

For an `Organization` (group), it aggregates the **independent** trial balances
of the member companies (those with `organization_id = <group>`) into a combined
group trial balance, by account code, then applies **approved** elimination
adjustments to produce the consolidated figures.

Each consolidated row shows three column pairs:
`combined_debit/credit` (sum of member companies) → `elimination_debit/credit`
(approved eliminations) → `consolidated_debit/credit` (the group result).

## 2. Safeguards (section 5)

- **Independent statutory books preserved.** This module only **reads** each
  company's trial balance (`trial_balance` service). It never writes.
- **No consolidation adjustment is posted into any company ledger.** Eliminations
  live in `consolidation_eliminations` and apply only to the consolidated report.
- **Finance approval required.** Only `is_approved` eliminations are applied.
  Unapproved ones are returned under `eliminations_pending`, and the report's
  `approved` flag is `false` while any remain. Approval needs the
  `consolidation.approve` permission (seeded to `FINANCE_CONTROLLER`, and
  `OWNER`/`SUPER_ADMIN` via `*`).
- **Group isolation.** Only member companies are included; companies outside the
  organization are never aggregated.
- **Access control.** The consolidated report requires `report.financial` **and**
  authorization for every member company (or superuser) — a user who cannot see
  one of the companies cannot see the group.
- **Configurable, not hardcoded.** Eliminations are data (account code + amounts +
  optional counterparty reference), entered by authorized admins; no account
  codes are assumed or hardcoded.

## 3. API

| Method | Path | Permission |
|---|---|---|
| GET | `/api/reports/consolidated?organization_id=&date_from=&date_to=` | `report.financial` (+ all-company access) |
| GET | `/api/admin/consolidation-eliminations?organization_id=` | `report.financial` |
| POST | `/api/admin/consolidation-eliminations` | `admin.manage` |
| POST | `/api/admin/consolidation-eliminations/{id}/approve` | `consolidation.approve` |

## 4. Scope / limitations

- The combined view aggregates by **account code**, which assumes member companies
  share the chart-of-accounts coding (as the seeded textile COA does). Cross-coding
  mapping is future work.
- Eliminations are entered manually (configurable). Automatic matching of
  inter-company receivables/payables is **not** implemented — it depends on the
  inter-company accounting configuration, which is a separate, still-gated
  decision (due-from/due-to control accounts per counterparty + accountant
  approval of mappings). Unrealized-profit-in-inventory elimination is likewise
  future work.
- `date_from`/`date_to` are passed through to each company's trial balance.

## 5. What it does NOT do

- Does not post, modify, or reverse any ledger entry.
- Does not create inter-company transactions (that configuration is separate).
- Does not change GST, inventory, or any company's statutory statements.
