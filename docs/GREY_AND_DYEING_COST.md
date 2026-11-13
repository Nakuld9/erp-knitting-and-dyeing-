# Grey fabric actual cost & dyeing (chemical-BOM-free)

**Date:** 2026-11-13 · **Branch:** `feat/multi-company-production-ready`
**Migration:** `0013_grey_cost_and_dyeing` (additive, reversible on SQLite + PostgreSQL).

Builds the **grey (greige) fabric** actual-cost chain and an operational
**dyeing** layer that tracks fabric through dyeing and computes a **genuine
actual dyed-fabric cost** — while the **chemical BOM stays disabled** (no recipe
explosion, no chemical reservation). The dye recipe / chemical masters, columns
and history are untouched and remain available for later.

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

## 5. Inventory valuation & accounting consistency

The perpetual **inventory sub-ledger** is the backbone and is internally
consistent: grey fabric received value = grey actual cost; its FIFO draw into
dyeing = that value; dyed fabric received value = the accumulated actual cost
(within per-kg rounding dust, which `dyed_inventory_value` records exactly from
the layers). The authoritative actual cost is the accumulated total; per-unit
inventory valuation carries the usual 2-dp rounding. GL journal posting for the
BOM-driven grey/dyed production path is a documented follow-up — the legacy
`record_production` path still posts GL for non-BOM knitting.

## 6. API, UI, permissions, isolation

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

## 7. Restrictions honoured

Chemical BOM explosion / reservation stays **disabled**; dye recipe & chemical
masters, columns and history are **untouched**; no historical transactions are
rewritten; the database-per-company architecture and factory/company isolation
are preserved; migration `0013` is additive; no live data is migrated.

## 8. Tests

`tests/test_dyeing_cost.py` (10) + `tests/test_dyeing_api.py` (5): FIFO yarn
actual costing, independent actual conversion, grey valuation + idempotency,
grey std-vs-actual, dyeing full flow, different saleable outputs + allocation,
job-work mode, reprocess/additional costs, incomplete-cost warnings,
grey→yarn-lot traceability, **factory** & **company** isolation, and
**accounting/inventory valuation consistency**. Grey BOM costing + version
preservation: `tests/test_fabric_bom.py`. Migration `0013` round-trip:
`tests/test_migrations.py`. All green on SQLite and real PostgreSQL.
