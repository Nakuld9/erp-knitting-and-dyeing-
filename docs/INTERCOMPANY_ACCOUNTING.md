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

## 4. Dual-leg posting (implemented)

`post_intercompany_charge()` posts **both legs atomically** (one transaction,
commit once):

- Seller A: Dr due-from(B) [control, from A's approved mapping]; Cr income;
  Cr output GST.
- Buyer B: Dr expense; Dr input GST; Cr due-to(A) [control, from B's approved
  mapping].

- **Fail-closed:** both mappings must be approved (`get_approved_mapping`), else it
  raises and nothing posts.
- **GST by place of supply:** intra-state (same state) → CGST+SGST; inter-state →
  IGST; derived from each company's state code via the existing `tax` service
  (fail-closed on a missing state code). Not assumed uniform across the group.
- **Accounts are configurable:** income/expense/GST accounts resolve through
  `posting_rules` (events `INTERCOMPANY_SALE` / `INTERCOMPANY_PURCHASE`,
  overridable per company) or explicit codes passed in; control accounts come
  from the mappings.

API: `POST /api/accounting/intercompany-posting` (`voucher.post` + authorization
for **both** companies).

**Flow assumption (documented):** the two legs are posted together by this call
(seller + buyer entered as one inter-company charge), sharing a reference — not
auto-propagated across separate user sessions. Wiring this onto the full
enquiry→SO→PO lifecycle (auto-creating B's PO from A's SO) remains a later,
separately-approved enhancement.

## 5. Automatic elimination suggestions (implemented, read-only)

`suggest_intercompany_eliminations()` reads the due-from/due-to control-account
balances for approved reciprocal pairs and proposes balanced eliminations
(`GET /api/reports/consolidated/elimination-suggestions`). It **persists
nothing**; finance reviews and creates them as approved `ConsolidationElimination`
entries. Assumes the mapped control accounts are **dedicated** inter-company
accounts.
