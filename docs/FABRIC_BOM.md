# Fabric Bill of Materials (BOM)

**Date:** 2026-11-12 · **Branch:** `feat/multi-company-production-ready`
**Status:** implemented and tested end to end (service + API + SPA), including a
realistic knit→dye→finish production run and historical-version preservation.

A version-controlled Fabric BOM ties the three manufacturing stages of a knitted
fabric — **knitting**, **dyeing** and **finishing** — into one approvable,
cost-rolled definition, and drives production orders with material reservation,
issue, actual-consumption capture and planned-vs-actual variance analysis.

It **reuses** the existing recipe/production structures rather than duplicating
them (the inspection that preceded this work is recorded in §6).

---

## 1. Data model (`app/models/fabric_bom.py`, migration `0011`)

```
FabricBom (parent: a finished fabric)
 └── FabricBomVersion (DRAFT → APPROVED → SUPERSEDED / OBSOLETE)
      │   effective_from / effective_to · approved_by/at · supersedes_id
      │   finished-fabric spec: construction, composition, yarn_count, gsm,
      │        width, gauge, diameter, stitch_length, finished_gsm/width
      │   dye_recipe_version_id  ──►  RecipeVersion (EXISTING, reused)
      │   std conversion costs: knitting, dyeing, finishing, labour, overhead
      │   cost snapshot: std_yarn_cost, std_dye_chem_cost, std_total_cost_per_kg
      ├── FabricBomYarn[]     yarn blend: yarn, blend %, feeder allocation,
      │                       std consumption/kg, wastage %, process-loss %, ₹/kg
      ├── FabricBomKnitting   machine type, gauge, diameter, feeders,
      │                       stitch length, rpm, efficiency, expected output
      └── FabricBomFinishing[] compacting / stentering / … steps, target gsm/width, ₹/kg

ProductionOrder  (stamps bom_version_id — never changes on later revisions)
 ├── ProductionReservation[]  exploded material requirement per stage
 └── ProductionConsumption[]  actual issue/consumption, wastage, rejection, reprocess
```

A nullable `production_order_id` links the existing actuals
(`knitting_production`, `dye_batches`, `finishing_transactions`) back to an order
for traceability — additive, no existing data touched.

## 2. Version control

* A BOM always has at least one version; new versions start **DRAFT** and are the
  only editable state. `add_version(clone_from_version_id=…)` clones a prior
  version as the base for a revision.
* **Approval** (`approve_version`) validates the version (needs yarn components;
  a linked dyeing recipe must itself be APPROVED), **snapshots the standard cost
  roll-up**, sets `effective_from`, and **supersedes** the previously-effective
  approved version (closing its `effective_to` the day before). The superseded
  version is retained.
* `effective_version(bom_id, on_date)` resolves the version whose effective
  window covers a date — including historical (superseded) windows — so a past
  order keeps the version it was created against. See the preservation test in
  `tests/test_fabric_bom.py::test_historical_version_preserved_on_revision`.
* Approved versions are **frozen**; editing one is rejected.

## 3. Standard cost roll-up (per kg finished fabric)

```
yarn       = Σ  std_consumption_per_kg · (1+wastage%) · (1+process_loss%) · ₹/kg
dye_chem   = Σ  (percent_owf/100 or grams_per_kg/1000) · chemical.standard_cost
             over the linked dyeing RecipeVersion's materials
finishing  = Σ  std_cost_per_kg(step)
total/kg   = yarn + dye_chem + std_knitting + std_dyeing + finishing
                  + std_labour + std_overhead
```

Computed by `bom.compute_cost_rollup` (preview) and snapshotted onto the version
at approval, so historical versions keep the cost they were approved with.

## 4. Production integration

1. **Create order** against an approved version (by `bom_version_id`, or by
   `bom_id` + `order_date` → the effective version). The version id is **stamped**.
2. **Explode** the BOM into reservations: yarn (knitting stage, grossed for
   wastage + process loss) and dyes/chemicals (dyeing stage, from the recipe).
3. **Requirements** (`material_requirements`) show planned vs on-hand, flagging
   shortages.
4. **Issue** (`issue_material`) consumes from FIFO stock via the inventory
   service (`source_module=PRODUCTION_ORDER`), recording actual cost, wastage,
   rejection and reprocess flags, and advancing the reservation status.
5. **Complete** records produced qty; **variance_report** compares, stage by
   stage, the standard (planned) cost from the stamped version against the actual
   cost drawn from inventory, with wastage/rejection quantities and totals.

## 5. API, permissions, UI

* **API** (`/api/bom`, `app/api/routes/bom.py`): BOM CRUD, versions, spec/yarns/
  knitting/finishing editors, cost preview, approve/obsolete; production orders,
  requirements, issue, complete, variance. All routed through `get_company_db`
  (the acting company's database under database-per-company) and guarded by the
  `bom.read` / `bom.write` / `bom.approve` / `production.manage` permissions
  (`app/seed/rbac.py`). Orders carry `factory_id` for factory awareness.
* **UI**: the **Fabric BOM** screen (`frontend/app.src.jsx` → `app.js`) builds a
  BOM, edits the draft (spec, yarn blend, knitting, finishing, dyeing recipe
  link), previews/rolls up cost, approves, creates a production order (exploding
  the BOM), issues materials and shows the variance report. Browser-verified.

## 6. What already existed (not duplicated)

Inspected before building. **Reused:** `UnitOfMeasure` (UoM + conversions),
`YarnMaster`/`YarnLot`, `FabricType`, `Machine`/`MachineType`, `Chemical`
(dyes+chemicals+auxiliaries, `standard_cost`), `ShadeCode`, and the **versioned
dyeing recipe** `Recipe`/`RecipeVersion`/`RecipeMaterial` (percent-OWF /
grams-per-kg, DRAFT/APPROVED) — the fabric BOM **links** a RecipeVersion for the
dyeing stage instead of re-modelling it. The actuals
`KnittingProduction`, `DyeBatch`/`DyeBath`, `FinishingTransaction`, `FabricRoll`
and the inventory FIFO engine (`inventory.consume`) are reused for issue and
traceability. **New** (there was no such thing before): the parent finished-fabric
BOM, its versioning/approval/cost-rollup, the yarn/knitting/finishing BOM
children, and the production-order / reservation / consumption / variance layer.

## 7. Tests

* `tests/test_fabric_bom.py` — version control + cost roll-up; order explosion +
  FIFO issue + stock drawdown; **historical version preserved on revision**;
  cannot approve without yarn.
* `tests/test_fabric_bom_api.py` — the full build→approve→order→issue→variance
  flow over HTTP, plus auth guard.
* `tests/test_migrations.py` — `0011` up/down/up reversibility.

## 8. Data-safety

Migration `0011` is additive and reversible; no historical data is migrated.
Operational BOM/production data is company-scoped and lives in the acting
company's database under database-per-company. The data-safety rules
(verified backup + approval before any historical migration; no merge into
`main`) continue to apply.
