# Grey fabric actual cost & dyeing (chemical-BOM-free)

**Date:** 2026-11-13 · **Branch:** `feat/multi-company-production-ready`
**Migration:** `0013_grey_cost_and_dyeing` (additive, reversible on SQLite + PostgreSQL).

Builds the **grey (greige) fabric** actual-cost chain and an operational
**dyeing** layer that tracks fabric through dyeing and computes a **genuine
actual dyed-fabric cost**, now with **full double-entry GL postings** and
**inventory-to-GL reconciliation** (§5–§6) — while the **chemical BOM stays
disabled** (no recipe explosion, no chemical reservation). The dye recipe /
chemical masters, columns and history are untouched and remain available for later.

---

## 1. Grey fabric BOM (yarn-only) — standard cost

The grey fabric BOM is the yarn-only Fabric BOM (`docs/FABRIC_BOM.md`): yarn
components / blend % / feeder allocation, standard consumption / wastage /
process loss, machine + knitting parameters, approved + version-controlled, and
a production order that **stamps** the exact approved version. The **standard
grey cost/kg = `std_yarn_cost` + `std_knitting_cost`** (knitting scope, from the
stamped version snapshot).

## 2. Grey fabric actual cost (`app/services/bom/production.py`)

* **Actual yarn** = Σ FIFO issue cost of the order's yarn consumptions
  (genuine, from the perpetual inventory ledger).
* **Actual knitting conversion** is recorded **independently** via
  `record_actual_knitting_conversion_cost` — only when entered. It is **never
  derived from the standard**; if not entered it is `None` and flagged missing.
* `value_grey_output` values the completed order's grey fabric into **FABRIC
  inventory** (keyed by the production-order id) at
  `actual grey cost/kg = (actual yarn + actual knitting conversion) / grey kg`.
  Idempotent. This is the inventory layer that dyeing later consumes by FIFO.
* `grey_actual_cost` reports standard vs actual grey cost/kg, the variance, the
  grey inventory value, and `missing_cost_components` / `cost_complete`.

## 3. Dyeing — operational + cost tracking (`app/services/dyeing/`)

Chemical-free: **no recipe is exploded and no chemical is reserved.** A
`DyeingJob` tracks:

* grey fabric in (FIFO-consumed from grey FABRIC inventory → genuine
  `grey_cost_consumed`) with **traceability** to the grey production order (and
  through it to yarn lots / inventory issues);
* production order / customer order / shade / **dye lot**; input & output weight,
  **process loss / weight change / rejection / reprocessing**;
* **internal dyeing charge** or **outside job-work charge** (by `mode`), plus
  optional **chemical / finishing / testing / other / reprocess** costs —
  *recorded manually when available*, never defaulted;
* dyed output lots, valued into FABRIC inventory; completion + status.

## 4. Actual dyed fabric cost (`dyeing.dyed_actual_cost`)

```
total_actual = grey_cost_consumed (FIFO)
             + dyeing_charge | jobwork_charge     (by mode, when recorded)
             + chemical_cost  (when recorded — chemical BOM disabled)
             + finishing_cost + testing_cost + other_cost + reprocess_cost
actual dyed cost/kg = total_actual / saleable_output_kg
```

* Accounts for **input vs output**, **process loss / rejection**,
  **reprocessing**, **additional material**, **outside job-work**, and
  **allocation across output lots** (by weight; the dyed fabric is valued into
  inventory per lot).
* **Incompleteness is explicit.** A required component that is *not recorded*
  (the mode's processing charge, or the chemical cost — always manual at this
  stage) puts it in `missing_cost_components`, sets `cost_complete = False`, and
  attaches a `note` labelling the per-kg figure a **lower bound**, never an
  artificially precise actual.

## 5. GL journal postings (double-entry, through the posting engine)

Every grey order and dyeing job now posts **balanced** production journals
(`voucher_type = PJ`) through the sanctioned `posting_engine.post_voucher`, using
only the **existing** chart of accounts — no invented codes, no engine bypass.
Dyed-fabric and grey-fabric are received into inventory at **exactly** the
accumulated cost (an exact-value override on `inventory.receive`, so the
sub-ledger equals the GL with no rounding dust).

**A. Knitting → grey** (`value_grey_output`, one voucher `PO:{n}:grey`):

| Dr | Cr | Amount |
|----|----|--------|
| 1080 Work in Progress | 1060 Yarn Inventory | actual yarn (FIFO) |
| 1080 Work in Progress | 2020 Expenses Payable | actual knitting conversion *(only if recorded)* |
| 1070 Greige Fabric Inventory | 1080 Work in Progress | grey total (yarn + conversion) |

WIP nets to **zero**; the greige debit equals the grey inventory receipt.

**B. Grey → dyeing WIP** (`issue_grey`, per issue `DYE:{n}:grey:{input}`):

| Dr | Cr | Amount |
|----|----|--------|
| 1080 Work in Progress | 1070 Greige Fabric Inventory | grey cost consumed (FIFO) |

**C. Dyed fabric** (`complete_job`, one voucher `DYE:{n}:dyed`):

| Dr | Cr | Amount |
|----|----|--------|
| 1080 Work in Progress | 2020 Expenses Payable | dyeing + chemical + finishing + testing + other + reprocess *(each only if recorded)* |
| 1080 Work in Progress | 2010 Accounts Payable *(party = sub-contractor)* | outside job-work charge *(if recorded)* |
| 1085 Finished Fabric Inventory | 1080 Work in Progress | accumulated total (grey + all charges) |

Dyeing WIP (grey already debited at issue) nets to **zero**; the finished-fabric
debit equals the dyed inventory receipt and the accumulated actual cost.

**Account mapping** is through two new posting *rules* (existing codes only):
`GREY_PRODUCTION` {wip 1080, yarn 1060, greige 1070, conversion_payable 2020} and
`DYEING_PRODUCTION` {wip 1080, greige 1070, fg 1085, expense_payable 2020,
jobwork_payable 2010}. `resolve_account` falls back to these defaults, so existing
company databases need no re-seeding; they are configurable per company like any
rule.

**WIP accounting:** WIP holds the grey cost from issue until the dyed fabric is
received, when it moves to finished inventory; a COMPLETED order/job leaves **no**
WIP balance.

**Cost allocation:** the accumulated job cost is allocated across saleable output
lots **by weight** (last lot absorbs allocation rounding); each lot is received
into inventory at exactly its allocated cost. `actual dyed cost/kg =
total / saleable output`.

**Loss & rejection:** normal process loss / weight change is **absorbed into the
good-output unit cost** (total ÷ saleable output) — standard job-costing. Rejected
quantity is recorded and excluded from saleable output (so it is likewise absorbed
into the cost of good output); an explicit abnormal-loss write-off to a P&L loss
account is a noted future extension, not a silent balancing entry.

**Corrections / reversals:** posted vouchers are immutable; corrections flow
through `posting_engine.reverse_voucher`, which posts a swapped REVERSAL voucher
(`REVERSAL:{ref}`) and marks the original REVERSED. Nothing is edited or deleted
in place.

**Controls:** every voucher is balanced or nothing commits; posting is idempotent
on `posting_reference`, which carries a **DB-level `UniqueConstraint(company_id,
posting_reference)`** (concurrency-safe, not only an application check); postings
land only in OPEN periods (`ClosedPeriodError` otherwise); the full audit trail
and source-document references (`source_module`/`source_id`) are preserved; and
every voucher posts in the acting legal company (company-scoped session + company
id on the voucher).

## 6. Inventory-to-GL reconciliation (`accounting/production_reconciliation.py`)

Proven directly from the immutable ledger, never via a balancing adjustment
(`/api/bom/orders/{id}/reconciliation`, `/api/dyeing/jobs/{id}/reconciliation`,
`/api/dyeing/reconciliation`; a **RECONCILED / DISCREPANCY** badge on the
Production Cost screen):

* yarn inventory reduction == yarn consumption GL (greige voucher yarn credit);
* grey fabric inventory receipt == grey production cost (greige debit);
* grey fabric issue == dyeing WIP (greige credit into WIP);
* dyeing WIP debits == accumulated job cost; credits move it all out;
* dyed fabric inventory receipt == accumulated actual dyed cost (finished debit);
* a COMPLETED order/job leaves **no** WIP balance;
* the FABRIC sub-ledger (grey + dyed layers) ties to the greige + finished GL
  contribution of this workflow.

Discrepancies are reported, never hidden.

## 7. API, UI, permissions, isolation

* **API**: grey-cost endpoints on `/api/bom/orders/{id}` (`conversion-cost`,
  `value-grey`, `grey-cost`); dyeing at `/api/dyeing/jobs` (create, issue-grey,
  costs, output, complete, cancel, cost, trace). Company-scoped
  (`get_company_db`) and **factory-authorized** (jobs owned by a factory are
  reachable only with access to it; `factory_id`/`warehouse_id` validated, never
  trusted). Guarded by `dyeing.manage` / `report.read` / `production.manage`.
* **UI**: the **Production Cost (Grey → Dyed)** screen shows grey std-vs-actual
  cost/kg + variance, dyeing operation, the actual dyed-fabric cost breakdown
  with *not-recorded* components and an **INCOMPLETE** badge, loss %, per-lot
  allocation, and **drill-down** from the dyed job → grey PO → yarn lots →
  inventory issues. Browser-verified.

## 8. Restrictions honoured

Chemical BOM explosion / reservation stays **disabled**; dye recipe & chemical
masters, columns and history are **untouched**; no historical transactions are
rewritten; the database-per-company architecture and factory/company isolation
are preserved; migration `0013` is additive; no live data is migrated.

## 9. Tests

`tests/test_dyeing_cost.py` (10) + `tests/test_dyeing_api.py` (6): FIFO yarn
actual costing, independent actual conversion, grey valuation + idempotency,
grey std-vs-actual, dyeing full flow, different saleable outputs + allocation,
job-work mode, reprocess/additional costs, incomplete-cost warnings,
grey→yarn-lot traceability, **factory** & **company** isolation, and
**accounting/inventory valuation consistency**.

`tests/test_production_accounting.py` (10) — GL integration: knitting→grey
journal (balanced, WIP zero), grey-without-conversion still balances, dyeing
internal/job-work journals, dyed receipt == inventory == GL across multiple
lots, idempotent + DB-unique-constraint duplicate guard, **period lock**,
**reversal**, and **inventory-to-GL reconciliation** (per order, per job,
company-wide), plus company isolation.

Grey BOM costing + version preservation: `tests/test_fabric_bom.py`. Migration
`0013` round-trip: `tests/test_migrations.py`. **Full suite 402 passed, 2
PG-gated skips** on SQLite; the grey/dyeing + accounting + migration suite
(44 tests) also passes on **real PostgreSQL**.
