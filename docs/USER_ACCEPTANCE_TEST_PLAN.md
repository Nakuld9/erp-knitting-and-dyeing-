# User Acceptance Test Plan (Phase 15)

**Date:** 2026-11-12 · **Branch:** `feat/multi-company-production-ready`

UAT exercises the ERP against real business scenarios with real roles, reconciling
results against independently-calculated values, and ends with **business-owner
acceptance**. This plan covers the environment, roles, scenarios and expected
results, and records what is already covered by **automated** acceptance tests
versus what must be run **manually with your data and signed off**.

> Status of sign-off: **NOT OBTAINED.** Automated acceptance evidence exists
> (below); business-owner acceptance is a human step and is not asserted by any
> test.

---

## 1. Test environment (to prepare)

- **Two legal companies** (e.g. company A, company B), each with its own chart of
  accounts, financial year and GST registration.
- **Two factories per company**; **multiple warehouses per factory**.
- **Different GST registrations** where applicable (state codes differing to
  exercise intra/inter-state GST).
- **Role-specific users:** company accountant (per company), factory manager
  (restricted to a factory), group-level administrator (consolidation access).

Set-up aids in the repo:
- `scripts/provision_company_db.py` — per-company database (if using
  database-per-company).
- The **Group & Tenancy** admin page — create the group, assign companies, grant
  memberships, configure inter-company mappings.
- `tests/test_uat_multicompany.py::_uat_setup` shows the exact data shape
  (two companies in a group, factories, warehouses, approved inter-company
  mappings) and can seed a demo instance.

## 2. Roles & access to verify

| Role | Must be able to | Must NOT be able to |
|---|---|---|
| Company accountant (A) | Post/read company A's vouchers, GST, statements | Touch company B's ledger |
| Factory manager (A/F1) | See/operate factory A-F1 | See A-F2 data without authorization |
| Group administrator | Consolidated reports across A+B | — |

Negative access is enforced and tested (forged `X-Company-Id` → 403; factory
scoping; cross-company record guard). See `docs/ROUTE_WIRING_REPORT.md`,
`tests/test_company_switching.py`, `tests/test_tenancy_api.py`.

## 3. Scenarios & expected results

### 3a. Full textile transaction (single company) — automated
Enquiry → quotation → sales order → purchase order → GRN → yarn issue → knitting →
greige rolls → batching → dyeing → dispensing → consumption → finishing → quality
→ finished goods → dispatch → sales invoice → accounting.
**Expected:** correct stock/WIP/FG, roll genealogy, costing, GST, balanced ledger.
**Coverage:** existing end-to-end tests (`tests/test_operability.py`,
`tests/test_api_flow.py`, the `demo` fixture) — 18–19 of the 20 acceptance tests;
the Color-Service steps run against the **simulator** (see `docs/INTEGRATIONS.md`).

### 3b. Multi-company / multi-factory — automated (`tests/test_uat_multicompany.py`)
| # | Scenario | Expected (reconciled) |
|---|---|---|
| 1 | Company isolation | Company A's stock is invisible to company B |
| 2 | Same-company factory transfer (F1→F2, 300 of 1000 kg) | A-W1 = 700, A-W2 = 300; **no voucher posted** (GL-neutral) |
| 3 | Inter-company charge A→B (₹5,00,000) | A: Dr due-from 5,00,000 / Cr income; B: Dr expense / Cr due-to 5,00,000 |
| 4 | Independent balancing | A's and B's trial balances each balance (Dr=Cr) |
| 5 | Group consolidated P&L | income 5,00,000, expense 5,00,000, **net 0** (inter-company nets out) |
| 6 | Inter-company reconciliation | both legs approved → ready for elimination |

Run: `cd backend && pytest tests/test_uat_multicompany.py` (deterministic, 0/6
flakes verified).

### 3c. Manual UAT (to run with your data, then sign off)
- Enter a real order and run it through the full chain in **each** company.
- Verify stock, WIP, FG, costing, GST registers, company ledgers, factory reports,
  group consolidation and audit history against your **independently-calculated**
  expected values.
- Verify GST intra/inter-state treatment with your real GSTINs (and have a GST
  professional review the statutory output before any live filing).

## 4. Acceptance criteria

UAT is complete when 3a + 3b pass, 3c is reconciled against your figures with no
material discrepancy, and the business owner signs below.

## 5. Sign-off

| Item | Owner | Date | Result |
|---|---|---|---|
| Automated acceptance (3a, 3b) | — | — | PASS (337 tests + multi-company UAT) |
| Manual reconciliation (3c) | _business_ | ____ | ____ |
| GST statutory review | _GST professional_ | ____ | ____ |
| **Business-owner acceptance** | _owner_ | ____ | ____ |

Until the last row is signed, status remains **UAT IN PROGRESS / DEVELOPMENT
READY** — not UAT-complete.
