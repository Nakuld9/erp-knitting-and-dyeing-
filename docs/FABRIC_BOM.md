# Fabric Bill of Materials (BOM)

**Date:** 2026-11-13 · **Branch:** `feat/multi-company-production-ready`
**Status:** implemented and tested end to end (service + API + SPA), including a
realistic knit→dye→finish production run, historical-version preservation,
factory-level authorization, honest actual-vs-standard costing, production-order
lifecycle controls and input validation. **Migrations `0011`+`0012`**, additive
and reversible on SQLite and PostgreSQL.

A version-controlled Fabric BOM ties the three manufacturing stages of a knitted
fabric — **knitting**, **dyeing** and **finishing** — into one approvable,
cost-rolled definition, and drives production orders with material reservation,
issue, actual-consumption capture and planned-vs-actual variance analysis.

It **reuses** the existing recipe/production structures rather than duplicating
them (the inspection that preceded this work is recorded in §6).

> **Scope at this stage — YARN ONLY.** Dye and chemical *usage* is not yet
> available, so the BOM's **material** side covers **yarn only**: no dyeing-recipe
> linkage, no dye/chemical material cost in the roll-up, and no chemical
> reservations on production orders. The dyeing-recipe column
> (`dye_recipe_version_id`) is **kept but unused** (nullable), so the dyeing
> material can be switched back on later without a migration. The
> knitting / dyeing / finishing / labour / overhead **conversion standards**
> (per-kg rates you enter) are unchanged.

---

## 1. Data model (`app/models/fabric_bom.py`, migrations `0011`+`0012`)

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
* **Approval** (`approve_version`) validates the version (needs yarn components),
  **snapshots the standard cost roll-up**, sets `effective_from`, and
  **supersedes** the previously-effective
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
dye_chem   = 0            # YARN-ONLY at this stage (no dye/chemical material)
finishing  = Σ  std_cost_per_kg(step)
total/kg   = yarn + std_knitting + std_dyeing + finishing
                  + std_labour + std_overhead
```

Computed by `bom.compute_cost_rollup` (preview) and snapshotted onto the version
at approval, so historical versions keep the cost they were approved with.

## 4. Production integration

1. **Create order** against an approved version (by `bom_version_id`, or by
   `bom_id` + `order_date` → the effective version). The version id is **stamped**.
2. **Explode** the BOM into reservations: **yarn only** (knitting stage, grossed
   for wastage + process loss). No chemical/dyeing reservations at this stage.
3. **Requirements** (`material_requirements`) show planned vs on-hand, flagging
   shortages.
4. **Issue** (`issue_material`) consumes from FIFO stock via the inventory
   service (`source_module=PRODUCTION_ORDER`), recording actual cost, wastage,
   rejection and reprocess flags, and advancing the reservation status. Issuing
   beyond the reservation's planned quantity is rejected unless the authorized
   `allow_over_issue` path is used; materials can only be issued while the order
   is PLANNED / IN_PROGRESS.
5. **Complete** records produced qty and is refused while reservations are still
   outstanding (override: `allow_outstanding`), requires a positive quantity, and
   never silently re-completes a terminal order. **Cancel** releases open
   reservations. **variance_report** — see §4a.

### 4a. Costing: genuine material actuals, standard-only conversion

`variance_report` keeps **standard** and **actual** strictly separate and never
relabels one as the other:

* **Material** cost is a genuine standard-vs-actual comparison — planned = Σ
  reservation standard cost; actual = Σ consumption cost **drawn from FIFO
  stock**. `material_variance` / `material_variance_pct` are reported per stage
  and in total.
* **Conversion** cost (machine / labour / overhead) is reported as a *standard*
  figure (`std_conversion_cost`). Its **actual** is only populated from a genuine
  recorded source. No per-order actual conversion cost is posted today (the
  actuals record quantities and output, not attributable money; GL cost-centre
  postings are not attributed to a production order), so `actual_conversion_cost`
  is `null` and `conversion_actual_available = False`. The standard value is
  **never** substituted for the actual.
* `actual_output_kg` and `actual_material_cost_per_kg` come from the genuine
  linked actuals (`KnittingProduction.produced_kg`, `DyeBatch.fabric_kg`,
  `FinishingTransaction.output_weight_kg`) and are `null` when nothing is linked.
* `total_actual_cost` is reported **only** when every stage's conversion actual
  is genuinely available; otherwise it is `null` with an explanatory `note` —
  never a fabricated full-cost figure. `_actual_conversion_cost` is the single
  extension point for a future genuine source.

## 5. API, permissions, UI

* **API** (`/api/bom`, `app/api/routes/bom.py`): BOM CRUD, versions, spec/yarns/
  knitting/finishing editors, cost preview, approve/obsolete; production orders,
  requirements, issue, complete, cancel, variance. All routed through
  `get_company_db` (the acting company's database under database-per-company) and
  guarded by `bom.read` / `bom.write` / `bom.approve` / `production.manage`
  (`app/seed/rbac.py`).
* **Factory authorization** (`app/core/context.py`): `factory_id` and
  `warehouse_id` from the client are never trusted. On order creation they are
  validated to belong to the acting company and to a factory the user may act in
  (explicit `UserFactoryMembership` or the company-wide `factory.access_all`);
  a client-supplied issue warehouse must match the order's factory. Reading,
  issuing, completing and the variance report all re-check factory access, and
  the order list hides orders in factories the user cannot reach. Forged ids →
  404; exists-but-no-access → 403. BOMs themselves are company-level master data
  (no factory), guarded at the company level.
* **Validation** (`app/services/bom/service.py`): blend %s must sum to 100 with
  no duplicate yarn; positive consumption; wastage / process-loss in `[0,100)`;
  valid finishing `process_type`; a new
  approved version must take effect strictly after the latest approved one (no
  overlapping effective windows). DB unique constraints (migration `0012`) make
  duplicate BOM codes per company and duplicate version numbers per BOM
  concurrency-safe.
* **UI**: the **Fabric BOM** screen (`frontend/app.src.jsx` → `app.js`) builds a
  BOM, edits the draft (spec, yarn blend, knitting, finishing — yarn-only, no
  dyeing recipe), previews/rolls up cost, approves, creates a production order (exploding
  the BOM), issues materials and shows the variance report (material actual-vs-
  standard, with conversion actual marked *n/a*). Browser-verified.

## 6. What already existed (not duplicated)

Inspected before building. **Reused:** `UnitOfMeasure` (UoM + conversions),
`YarnMaster`/`YarnLot`, `FabricType`, `Machine`/`MachineType`, `ShadeCode`. The
**versioned dyeing recipe** `Recipe`/`RecipeVersion`/`RecipeMaterial` and the
`Chemical` master exist and were inspected; the BOM has a (currently unused)
`dye_recipe_version_id` column ready to link a `RecipeVersion` for the dyeing
stage **when dye/chemical usage is added** — at this yarn-only stage it is not
linked. The actuals
`KnittingProduction`, `DyeBatch`/`DyeBath`, `FinishingTransaction`, `FabricRoll`
and the inventory FIFO engine (`inventory.consume`) are reused for issue and
traceability. **New** (there was no such thing before): the parent finished-fabric
BOM, its versioning/approval/cost-rollup, the yarn/knitting/finishing BOM
children, and the production-order / reservation / consumption / variance layer.

## 7. Tests

* `tests/test_fabric_bom.py` — version control + cost roll-up; order explosion +
  FIFO issue + stock drawdown; genuine actual output read from a linked
  `KnittingProduction` with conversion actual left unavailable; **historical
  version preserved on revision**; cannot approve without yarn.
* `tests/test_fabric_bom_api.py` — the full build→approve→order→issue→variance
  flow over HTTP (issues every reservation; completion refused while outstanding),
  plus auth guard.
* `tests/test_fabric_bom_factory_authz.py` — negative factory authorization:
  cross-factory create/read/issue/complete → 403, forged factory/order → 404,
  warehouse/factory mismatch → 403, list filtered to accessible factories.
* `tests/test_fabric_bom_lifecycle.py` — over-issue rejected (and the authorized
  path), completion guards (outstanding reservations, positive qty, no silent
  re-complete), cancel releases reservations and blocks further ops.
* `tests/test_fabric_bom_validation.py` — blend sum / consumption / wastage range
  / duplicate yarn / invalid finishing / duplicate code / effective-date overlap,
  and the DB unique constraint on version numbers.
* `tests/test_migrations.py` — `0011` and `0012` up/down/up reversibility
  (verified on SQLite and PostgreSQL).

All of the above also pass on real PostgreSQL (39 BOM + migration tests).

## 8. Data-safety

Migrations `0011`+`0012` are additive and reversible; no historical data is migrated.
Operational BOM/production data is company-scoped and lives in the acting
company's database under database-per-company. The data-safety rules
(verified backup + approval before any historical migration; no merge into
`main`) continue to apply.
