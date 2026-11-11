# Inter-Company Accounting (configuration + approval)

**Date:** 2026-11-11 · **Branch:** `feat/multi-company-production-ready`
**Status:** configurable, accountant-approved control-account mappings + a
fail-closed resolver and reconciliation view. **No posting is wired yet** —
by design, until the approach is confirmed and the sales/purchase integration is
separately approved.

Honours business decision section 4.

---

## 1. Model

`intercompany_account_mappings` (migration 0006, additive): from a company's
books, toward a counterparty company, the **configurable** control accounts:

- `due_from_account_id` — an **ASSET** account (amounts the counterparty owes us)
- `due_to_account_id` — a **LIABILITY** account (amounts we owe the counterparty)

plus approval fields (`is_approved`, `approved_by`, `approved_at`). Unique per
`(company, counterparty)`.

## 2. Safeguards (section 4)

- **Separate configurable due-from/due-to accounts per counterparty.** One mapping
  row per `(company, counterparty)`; each company configures its own control
  accounts toward each related company.
- **No hardcoded account codes; no assumed chart of accounts.** The accounts are
  chosen by an authorized admin from that company's **own** COA and validated:
  they must exist in the company, be active posting accounts, and be of the right
  nature (asset for due-from, liability for due-to).
- **Accountant approval before live posting.** A mapping is created unapproved.
  `get_approved_mapping()` — the fail-closed guard that every future inter-company
  posting path must call — returns a mapping **only** when it is active **and**
  approved; otherwise it raises. Approval requires the `intercompany.approve`
  permission (seeded to `ACCOUNTANT`, `FINANCE_CONTROLLER`, and `*`).
- **Reconciliation / elimination references.** `reconciliation()` reports, for
  each mapping, whether the reciprocal mapping (counterparty → company) also
  exists and is approved — i.e. `ready_for_elimination`, the signal a future
  automatic consolidation elimination will key off.
- **GST from the actual transaction.** When posting is wired, GST treatment is
  derived from the real transaction (seller/buyer GST registration + place of
  supply) via the existing `tax` service (`compute_gst` / fail-closed
  `resolve_intra_state`) — never assumed uniform across group companies.

## 3. API

| Method | Path | Permission |
|---|---|---|
| GET | `/api/admin/intercompany-mappings` | `report.financial` |
| POST | `/api/admin/intercompany-mappings` (by account **code**) | `admin.manage` |
| POST | `/api/admin/intercompany-mappings/{id}/approve` | `intercompany.approve` |
| GET | `/api/admin/intercompany-reconciliation` | `report.financial` |

## 4. What is intentionally NOT done (next, separately-approved step)

- The actual **dual-company posting** of an inter-company sale/purchase (company A
  books a receivable to "due from B"; company B books a payable to "due to A";
  GST per place of supply; linked counterparty reference). The configuration and
  the fail-closed guard are in place; wiring the posting onto the sales/purchase
  lifecycle is the next step and needs sign-off on the transaction flow.
- **Automatic** consolidation elimination from matched inter-company balances
  (the `ready_for_elimination` signal exists; generating the elimination entries
  from it is the follow-on).

Until posting is wired, this module changes no ledger and creates no transaction.
