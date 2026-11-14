# Hybrid Manufacturing, Job Work, Inventory, Costing, GST ITC-04 & Sales Documents — Design

**Status: DESIGN ONLY. No application code, schema, migration, business logic, test,
or live data was changed by this document.** Implementation has not started and is
awaiting explicit approval.

**Repository:** `Nakuld9/erp-knitting-and-dyeing-`
**Branch inspected:** `feat/multi-company-production-ready`
**HEAD at inspection:** `8022693f11aa5658528d68589b936b2d9b8dc47f`
**Working tree note:** at inspection the tree also held **uncommitted Stage-3 print
format-correction changes** (`backend/app/api/routes/printing.py`,
`backend/app/services/printing/{document,sales_documents}.py`,
`backend/app/services/printing/pdf_sales.py`, `backend/tests/test_sales_documents.py`)
that are a *separate, earlier* task awaiting the owner's visual approval. **This
design task did not create, modify, or commit those files**; they remain the
owner's pending work.

> **Evidence discipline.** Every "current state" claim below cites a repository path
> (and line where useful). Where a feature is **absent/incomplete**, it is marked a
> **GAP**. Proposed design is marked **PROPOSED**. Statutory items are marked with a
> **verification date** or **UNRESOLVED** where the exact official schema could not be
> confirmed from primary sources in this pass.

---

## 1. Executive summary

The ERP already implements a correct double-entry accounting core, FIFO perpetual
inventory with stock-to-GL reconciliation, grey→dyed manufacturing with *actual*
costing, dyed-roll → grey → yarn traceability, a reusable A4 print framework with
company-scoped, FY-aware, concurrency-safe document numbering, and Stage-3 Sales GST
Invoice + Delivery Challan documents. It does **not** yet implement: a job-worker
**custody** inventory dimension; outsourced knitting/dyeing/finishing as first-class
job-work flows; a **Semi-Finished** or **Trading** warehouse category; **trading
fabric** purchase/sale; **direct yarn sales**; a **packing list**; or **GST Form
ITC-04 / Section 143** job-work movement tracking.

This document designs those additions **on top of** the existing architecture,
reusing the roll-numbering sequence, FIFO engine, posting engine, job-work model and
print framework, without renumbering historical rolls, rewriting historical ledger
entries, enabling chemical-BOM automation, or weakening the database-per-company
isolation. It also designs the three distinct sales documents (manufactured-fabric
invoice with roll-wise detail, detailed delivery challan, separate packing list) and
the trading-fabric and direct-yarn-sales flows with their precedence rules.

---

## 2. Scope, non-goals, precedence rules

**In scope (design only):** hybrid internal/outsourced knitting, dyeing, finishing;
common job-work framework reusing the vendor master; job-worker custody as an
inventory custody dimension; five warehouse categories; roll genealogy incl.
split/merge; actual costing/valuation across job work; accounting & GST event
mappings (for CA review); ITC-04 / Section 143 design; three sales documents;
trading-fabric and direct-yarn-sales flows; reporting, permissions, audit, edge
cases, data-model proposal, test matrix, phased plan, open decisions.

**Non-goals:** implementing code; running migrations/backfills; deploying; merging to
`main`; certifying tax/legal positions; enabling chemical-BOM explosion/reservation;
changing existing dye recipes / chemical masters / history; renumbering historical
roll IDs; altering existing costing/GST calculations.

**Precedence (authoritative; overrides any conflicting assumption):**

| Rule | Decision |
|---|---|
| A. Trading fabric | **No** ERP Roll ID, lot/batch, roll genealogy, or individual roll weight. Item/spec + aggregate qty/UOM/weight only. Held in a separate **Trading Warehouse** category. |
| B. Direct yarn sales | **Retain & display the existing Yarn Store lot number.** Never generate a replacement lot. Reserve against existing lots; preserve lot history + FIFO. |
| C. Packing list | Separate Sales Packing List, **manufactured fabric only**. None for trading fabric or direct yarn sales. |
| D. Manufactured fabric Roll IDs | **Reuse the existing ERP roll-numbering** and format/sequence. No replacement generator, no renumber of historical rolls, no separate grey/dyed/finished ID systems. |
| E. Warehouse categories | Four mandatory manufacturing stages — **Yarn Store, Greige, Semi-Finished, Finished Goods** — plus a separate additional **Trading Warehouse** (does not replace/collapse any stage). |
| F. Scope | Design/documentation only; render real previews; wait for approval. |

**Conflict watch:** Rule D says "reuse the existing generator" but the repo has **no
single generator** (see §5). Safest resolution (PROPOSED, needs approval): treat the
existing knitting `next_document_number(doc_type="ROLL")` path as the canonical
generator and route *all* genuine new manufactured output (grey/dyed/finished)
through it, while preserving historical derived IDs unchanged. This is a
backward-compatible extension, not a replacement.

---

## 3. Branch / HEAD and inspection evidence

- `git rev-parse --abbrev-ref HEAD` → `feat/multi-company-production-ready`
- `git rev-parse HEAD` → `8022693f11aa5658528d68589b936b2d9b8dc47f`
- `git remote -v` → `origin https://github.com/Nakuld9/erp-knitting-and-dyeing-`
- Recent commits: `8022693` (UAT walkthrough docs) · `66279b7` (Stage-3 sales docs) ·
  `6164fee` (Stage-2 print framework) · `2689672` (Stage-1 branding) · `721f674`
  (branding/doc plan).
- Stack: FastAPI + SQLAlchemy 2 + Alembic; PostgreSQL (prod) / SQLite (tests);
  self-contained React SPA served at `/app/`. Migrations `backend/alembic/versions/`
  up to `0015_printed_documents`. Tests `backend/tests/` (conftest uses a seeded
  in-memory SQLite `demo` fixture).

---

## 4. Current architecture findings (verified)

| Capability | Status | Evidence |
|---|---|---|
| Double-entry posting (balanced-or-nothing, idempotent, reversal, period locks) | Present | `app/services/accounting/posting_engine.py`, `posting_rules.py` |
| FIFO perpetual inventory + value-based stock-to-GL reconciliation | Present | `app/services/inventory/service.py`, `app/services/accounting/stock_reconciliation.py` |
| Grey production (yarn issue → WIP → greige) at actual cost | Present | `app/services/bom/production.py` |
| Dyeing (grey → dyed) at actual cost, chemical-BOM OFF | Present | `app/services/dyeing/service.py`, `app/models/dyeing_cost.py` |
| Dyed roll = sellable FINISHED `FabricRoll` in dispatch/COGS flow | Present | `dyeing/service.py` complete_job; `order_lifecycle.create_delivery` |
| Dyed-roll → grey → yarn traceability | Present | `dyeing.trace`, `sales_documents._roll_trace` |
| Sales invoice posting + GST split (intra/inter) | Present | `app/services/sales.py`, `app/services/tax.py` |
| Delivery note (dispatch) + COGS | Present | `app/services/order_lifecycle.py` |
| Roll split / merge with genealogy (`parent_roll_id`) | Present | `app/services/manufacturing/roll_ops.py` |
| Finishing transaction (greige→FG transform) | Present (partial) | `app/models/quality.py`, `app/services/quality.py` |
| Job-work model (billing-oriented) | Present (partial) | `app/models/jobwork.py` |
| Print framework + numbering + reprint registry | Present | `app/services/printing/`, `app/models/printing.py` |
| Multi-company DB-per-company + factory isolation | Present | `app/core/db_router.py`, `app/core/context.py`, `app/models/tenancy.py` |
| Job-worker **custody** inventory dimension | **GAP** | not modeled |
| Outsourced knitting/dyeing/finishing as job-work flows | **GAP** | only internal production flows exist |
| Semi-Finished & Trading warehouse categories | **GAP** | `Warehouse.kind` = STORE/GREIGE/FG/DISPENSING_TANK only (`masters.py:141`) |
| Trading fabric purchase/sale | **GAP** | only a *trading account* P&L statement exists (`financial_statements.py`) |
| Direct yarn sales | **GAP** | yarn is only *issued to production* |
| Packing list | **GAP** | no model/renderer |
| GST ITC-04 / Section 143 tracking | **GAP** | none |

---

## 5. Existing Roll ID generator analysis (PROTECTED, §10)

**Finding: there is no single central roll-ID generator.** Roll numbers are produced
per creation path:

- **Knitting (greige):** `app/services/manufacturing/knitting.py:84-90` — if the caller
  supplies no `roll_no`, it calls
  `next_document_number(db, company_id=…, doc_type="ROLL", fiscal_year=fy, prefix=f"GR-{mcode}-")`
  → e.g. `GR-K01-2627-00001`. This uses the shared `DocumentSequence`
  (`app/models/audit.py:29`) via `app/services/sequences.py`, which is **company-
  and FY-scoped, gap-free, and concurrency-safe** (row lock + `INSERT … ON CONFLICT
  DO NOTHING` seed, hardened in Stage 2, commit `6164fee`). Uniqueness is enforced by
  `UniqueConstraint(company_id, doc_type, fiscal_year)` on the sequence.
- **Dyeing (dyed/finished roll):** `app/services/dyeing/service.py:306` —
  `roll_no=f"{job.number}-{o.lot_code}"` (e.g. `DJ-SJ-A`). **Does not** use the ROLL
  sequence.
- **Split:** `app/services/manufacturing/roll_ops.py:77` — `f"{parent.roll_no}-S{i}"`.
- **Merge:** `roll_ops.py:115` — `f"{first.roll_no}-MRG"`.
- **Caller-supplied:** knitting and split/merge accept an explicit `roll_no` when given.

`FabricRoll.roll_no` is `String(40), index=True` (not globally unique by DB
constraint today — **GAP/risk**: there is no `UniqueConstraint` on
`(company_id, roll_no)`). Barcode mirrors `roll_no`. Label printing exists
(`app/services/labels.py::roll_label_pdf`, QR on `roll_no`).

**Design position (PROPOSED):** Adopt the knitting `doc_type="ROLL"` sequence as the
**single canonical generator** for *all* genuine new manufactured output, with
stage-aware prefixes (e.g. `GR-…` greige, `DY-…` dyed, `FN-…` finished) while keeping
the **same sequence service and uniqueness semantics**. Migrate dyeing/finishing roll
creation to call it. Preserve every historical `roll_no` exactly (no renumber). Add a
`UniqueConstraint(company_id, roll_no)` only after a read-only audit proves no
existing duplicates (migration risk flagged in §30). Transfer / job-work issue /
return / reprint must **never** change a roll's ID.

---

## 6. FabricRoll and genealogy analysis

`app/models/manufacturing.py::FabricRoll` fields: `company_id`, `factory_id`,
`roll_no`, `barcode`, `kind` (`RollKind.GREIGE|FINISHED`), `knitting_production_id`,
`fabric_type_id`, `machine_id`, `yarn_id`, `yarn_lot_id`, `sales_order_id`,
`dye_batch_id`, `delivery_note_id` (prevents re-dispatch), `weight_kg`, `length_m`,
`gsm`, `width_cm`, `warehouse_id`, `quality_status`, `status`, `parent_roll_id`.

**Genealogy today** is a single `parent_roll_id` self-reference plus implicit links
(`knitting_production_id`, `yarn_lot_id`, `dye_batch_id`, and
`DyeingOutput.dyed_item_id → FabricRoll.id`). This represents simple one-parent
split/merge and dyed←grey via the dyeing job, but **cannot** represent true
many-to-many genealogy (e.g. one dyed roll from several grey rolls, or a finished roll
blending multiple dyed lots) with explicit per-edge quantity/UOM allocation
(**GAP**). `RollKind` has only GREIGE/FINISHED — **no SEMI-FINISHED/dyed kind**
(**GAP**; dyed rolls are currently created as `FINISHED`).

**PROPOSED (§17):** a `RollGenealogyEdge` join entity (child_roll, parent_roll,
relation, qty, uom, allocation_basis) alongside the kept `parent_roll_id`, and a
`RollKind.DYED` (semi-finished) value, so the stage sequence Greige→Dyed→Finished is
explicit and cost/quantity flows are auditable.

---

## 7. Existing warehouse / inventory architecture

- `app/models/masters.py::Warehouse` — `company_id`, `branch_id` (factory),
  `code`, `name`, `kind` (`String(30)`, default `STORE`; observed values
  `STORE/GREIGE/FG/DISPENSING_TANK`). **No category entity**; "category" and
  "physical warehouse" are conflated into a free-string `kind`.
- FIFO inventory: `app/services/inventory/service.py` — `StockLayer` (company_id,
  item_type, item_id, warehouse_id, qty_remaining, unit_cost, value_remaining),
  `receive()` / `consume()` (FIFO/FEFO, `with_for_update` lock), `balance()`;
  value-based reconciliation in `accounting/stock_reconciliation.py` (grey→GL 1070,
  dyed/finished→GL 1085, yarn→1060, chemical→1100, dye→1090).
- Reservations: `app/models/fabric_bom.py::ProductionReservation` +
  `ReservationStatus` (RESERVED/ISSUED/RELEASED) prevent double-issue of yarn to
  production.
- Factory transfers: `app/services/factory_transfer.py` (GL-neutral warehouse move).

**GAP:** no `SEMI_FINISHED`/`TRADING` kind; no category-vs-physical separation; no
job-worker custody location; no in-transit location.

---

## 8. Four manufacturing stages + Trading Warehouse (PROPOSED)

Introduce a **WarehouseCategory** concept (enum or small table) separate from the
physical `Warehouse` row:

| Category | Holds | Roll tracking | GL inventory account (existing COA) |
|---|---|---|---|
| **YARN_STORE** | purchased yarn, existing lots, FIFO layers | lot-wise (existing yarn lots) | 1060 Yarn Inventory |
| **GREIGE** | grey fabric after knitting | manufactured Roll IDs | 1070 Greige Fabric Inventory |
| **SEMI_FINISHED** | dyed fabric accepted after dyeing, before finishing | manufactured Roll IDs | **PROPOSED** new sub-account under finished/greige, or reuse 1085 with a stage tag — CA/finance decision |
| **FINISHED_GOODS** | finished, inspected fabric | manufactured Roll IDs | 1085 Finished Fabric Inventory |
| **TRADING** (additional) | purchased trading fabric for resale | **no Roll ID / no lot / aggregate only** | **PROPOSED** new account e.g. 1086 Trading Fabric Inventory (separate from manufacturing) |

A company/factory may have many physical warehouses within one category; category
semantics stay separate from the physical `Warehouse` row. **Job-worker custody is a
separate custody/location dimension (§15), not a fifth stage.** Goods at a job worker
remain company-owned and are included in valuation and stock-to-GL reconciliation;
**never counted both in a warehouse and in custody.**

Stock states to model explicitly: physical-in-warehouse, in-transit, job-worker
custody, accepted-output-awaiting-put-away, rejected/held, consumed, process
loss/waste, unused returns.

---

## 9. Hybrid manufacturing configuration (PROPOSED)

A `ProcessMode` settings entity, **company-level** (factory override only if a real
need is shown), with independent toggles: internal/outsourced × knitting/dyeing/
finishing. All four combinations per process must work (hybrid / internal-only /
outsourced-only / unavailable-for-new-work).

Controls: warn before disabling a mode; **block new transactions** for a disabled
mode but **never** hide/disable historical transactions, reporting, printing,
costing, inventory, traceability, accounting, or audit; always allow already-started
work to be completed/received/reconciled/reversed under controlled rules. Record who
changed a setting, when, why, and scope (audit).

---

## 10. Common job-work framework (PROPOSED)

Reuse `app/models/masters.py::Supplier` as the vendor master. Add a **vendor
classification** (e.g. `is_job_worker` flag or a `vendor_type` set) rather than a
duplicate master — consistent with the existing `is_chemical_supplier` pattern.

The existing `app/models/jobwork.py::JobWork` is **billing-oriented** (INWARD/OUTWARD
direction, `process`, `supplier_id`, `qty_kg`, `returned_qty`, `rate`, `status`) and
explicitly treats inward material as *theirs* (not valued). It does **not** model
**custody of our own goods sent out**, roll-level issue/receipt, per-stage cost
accumulation, challan movements, or ITC-04 data (**GAP**). PROPOSED job-work entities
(§16): `JobWorkOrder`, `JobWorkChallan` (issue / receipt / return / worker-to-worker),
`JobWorkCustodyLedger`, `JobWorkCharge`, reusing `Supplier`, `FabricRoll`, yarn lots,
the FIFO engine and posting engine.

The order's lifecycle must cover: issue, custody, transfer, receipt, unused return;
contracted vs actual charges + freight; actual output / loss / rejection / shortage /
excess / reprocessing; vendor bills + accounting refs; approvals, statuses, audit;
outstanding material, ageing, vendor reconciliation; **partial** issues and receipts.
**A challan/custody movement is not a sale or purchase**; the company retains
ownership throughout.

---

## 11. Knitting workflows

**Internal (present):** `knitting.py` — yarn issue (FIFO by lot) → greige rolls (ROLL
sequence) → WIP/greige postings; grey valued at actual (yarn FIFO + actual conversion)
in `bom/production.py::value_grey_output`.

**Outsourced (PROPOSED):** `YARN STORE → job-worker custody → new greige rolls →
GREIGE`. Issue actual Yarn Store lots (FIFO cost) to custody (stock leaves Yarn Store
once, custody recorded once; **no** sale/purchase, no WIP double count); capture
vendor/order/challan/dates, yarn issued by lot, unused yarn returned **to the original
lot**, machine/specs where relevant; on accepted receipt create new greige Roll IDs
via the canonical generator; capture construction/GSM/width/weight/length,
wastage/shortage/rejection/excess/reprocessing, **actual** knitting conversion charges,
freight, partial receipts, outstanding yarn.
**Grey actual cost = actual FIFO yarn consumed + actual knitting job-work charges +
attributable costs**, allocated transparently to accepted output and loss/rejection
policy. **Never** substitute a standard conversion rate for actual charges.

## 12. Dyeing workflows

**Internal (present):** `dyeing/service.py` — grey issue → dyed output lots → dyed
cost = grey + dyeing charge + chemical (recorded, no BOM) + reprocess; 1085 posting.

**Outsourced (PROPOSED):** `GREIGE → job-worker custody → new dyed rolls →
SEMI_FINISHED`. Track original grey Roll IDs + genealogy, vendor/order/challan,
shade/dye lot/specs, issued/received weight+length, new dyed Roll IDs via the
canonical generator, loss/rejection/reprocessing/shortage/excess, **actual** dyeing/
testing charges, freight, partial receipts, outstanding custody.
**Dyed actual cost = allocated actual grey cost + actual dyeing charges + attributable
costs.** Do **not** enable chemical-BOM explosion/reservation; keep dye recipe/chemical
masters/history untouched; optional chemical cost only when explicitly recorded with
evidence.

## 13. Finishing workflows

**Current:** `app/models/quality.py::FinishingTransaction` + `services/quality.py::
record_finishing`; `_transfer_greige_to_fg` moves a roll's carried cost greige→FG.
**GAP:** finishing transforms the **same** roll (no new finished Roll ID; no explicit
Semi-Finished→Finished stage; no before/after GSM/width/weight/length deltas as a new
roll identity).

**PROPOSED (internal & outsourced):** `SEMI_FINISHED → (job-worker custody if
outsourced) → new finished rolls → FINISHED_GOODS`. Track dyed roll/dye-lot genealogy,
vendor/order/challan, before/after GSM/width/weight/length, new finished Roll IDs via
the canonical generator, shrinkage/loss/rejection/reprocessing/shortage/excess,
**actual** finishing charges/freight, partial receipts, outstanding custody.
**Finished actual cost = allocated actual dyed cost + actual finishing charges +
attributable costs.**

**Internal/outsourced mix:** all route permutations (e.g. internal knit → outsourced
dye → internal finish; outsourced knit → internal dye → outsourced finish; hybrid
allocation of different lots to internal vs external) must preserve ownership,
physical location, custody, roll identity, cost accumulation, and genealogy per lot.

---

## 14. Same-vendor / different-vendor scenarios (PROPOSED)

- **Same vendor, dyeing + finishing (combined order):** one order, but **separate**
  dyeing and finishing scope/qty/rate/charges/losses/outputs/costs/genealogy/partial
  receipts.
  - *Physical intermediate return:* record accepted dyed rolls **into SEMI_FINISHED**,
    then issue for finishing; preserve each stock-ledger event.
  - *No physical intermediate return:* record a **logical** intermediate dyeing stage
    + vendor custody; **never** record a false physical warehouse receipt; keep
    separate stage costs/qty/roll identity/genealogy; show true custody/location.
- **Different vendors:** Dye Vendor A → accepted dyed receipt into SEMI_FINISHED →
  issue to Finishing Vendor B → finished receipt into FINISHED_GOODS. Keep separate
  orders/challans/dates/rates/bills/custody balances/losses/cost accumulation/audit.
- **Worker-to-worker transfer:** controlled custody transfer with source/destination
  acknowledgement + quantity reconciliation. **Distinguish an internal ERP
  custody-transfer record from a statutory challan/endorsed document**; do **not**
  claim an internal record satisfies law without CA/legal verification (§13 GST).

---

## 15. Job-worker custody and transfer (PROPOSED)

A **JobWorkCustodyLedger** is a custody/location dimension parallel to warehouse stock:
issuing to a job worker removes source-warehouse stock **once** and records custody
**once**; receipt moves custody to the accepted-warehouse stock **once**. Goods remain
company-owned and valued. Reconciliation ties *physical warehouse + custody +
in-transit* to inventory valuation and GL. Distinguish consumption, normal loss,
abnormal loss, rejection, unused return, and still-outstanding custody. Never
duplicate inventory value, purchase, expense, or GL postings.

---

## 16. Proposed models and relationships (PROPOSED — additive)

> Reuse/extend where safe; not every item is a new table.

- `WarehouseCategory` (or `Warehouse.category` enum) — YARN_STORE / GREIGE /
  SEMI_FINISHED / FINISHED_GOODS / TRADING.
- `Supplier.is_job_worker` (or `vendor_type`) — vendor classification (reuse master).
- `JobWorkOrder` — company_id, factory_id, vendor_id, process, mode, references
  (production/sales), contracted rate, expected return, status, audit.
- `JobWorkChallan` — order_id, type (ISSUE / RECEIPT / UNUSED_RETURN /
  WORKER_TO_WORKER), date, qty/UOM, roll links, statutory-challan fields, audit.
- `JobWorkCustodyLedger` — company_id, vendor_id, item (yarn lot / roll), qty in/out,
  custody balance, valuation link.
- `JobWorkCharge` — order_id, charge type (processing/freight/testing), contracted vs
  actual, vendor bill ref, accounting voucher ref.
- `RollGenealogyEdge` — child_roll_id, parent_roll_id, relation, qty, uom,
  allocation_basis (adds true many-to-many over the kept `parent_roll_id`).
- `RollKind.DYED` added (semi-finished) — additive enum value.
- `TradingItem` + trading GRN/stock (aggregate; **no roll/lot**), separate FIFO layers.
- `DirectYarnSale` path (reuses yarn lots + FIFO; §25), no new lot generation.
- `PackingList` + `PackingListLine` (§23).
- `ITC04Return`, `ITC04Line`, `ITC04RollSchedule` (§20).

Each carries `company_id` (+ `factory_id` where scoped), audit fields
(`created_at/by`, `updated_at/by`), constraints/indexes, and lives in the
**company operational database** (never the central DB).

---

## 17. Roll genealogy and split/merge

Genealogy is separate from Roll ID. The model must represent one-to-many, many-to-one,
many-to-many, partial processing, split/merge, reprocessing, rejected output, and
process loss, preserving original identities and quantities, with **allocation basis +
quantity (weight/length/UOM) recorded on every edge**. Current `parent_roll_id`
(`roll_ops.py`) handles only single-parent cases; PROPOSED `RollGenealogyEdge` adds
the rest. Full trace chain to support: finished → dyed(s) → grey(s) → yarn lot(s) →
production order/knitting job → job worker(s) → actual costs → sales order/dispatch/
COGS. Prevent circular genealogy and unexplained quantity creation/loss (validation).

---

## 18. Actual costing and valuation (PROPOSED, aligned to existing engine)

Per-roll / per-kg (and per-metre where meaningful) **actual** accumulation: FIFO yarn
purchase cost → actual yarn consumption → grey cost → dyeing conversion → finishing
conversion → attributable freight/testing → split/merge allocation → partial receipts
→ process-loss/rejection policy → reprocessing → vendor-invoice reconciliation →
incomplete/unbilled cost (clearly labelled estimate) → audited adjustments. **Never
label standard cost as actual.** Estimated cost is labelled and reconciled when actual
evidence (vendor bill/freight) arrives (late-bill handling).

**Custody accounting rules:** issue-to-worker = custody/location move (not sale);
goods remain owned; remove source stock once, record custody once; on return move
custody to accepted stock once; no duplicate inventory/purchase/expense/GL; distinguish
consumption / normal loss / abnormal loss / rejection / unused return / outstanding;
reconcile physical + custody + in-transit to valuation and GL.

The existing posting engine, posting rules (`posting_rules.py`), period locks and
reconciliation are reused. **Do not invent account names/posting rules** — §19 maps to
existing COA and flags new accounts for approval.

---

## 19. Accounting and GST considerations (for CA review — no certification)

Existing COA codes referenced: 1060 Yarn, 1070 Greige, 1080 WIP, 1085 Finished, 5150
COGS, 4010 Sales, 1040 Receivable, 2010 AP, 2020 Expenses/Conversion Payable,
2041/2042/2043 Output CGST/SGST/IGST. Existing posting rules:
`GREY_PRODUCTION`, `DYEING_PRODUCTION`, `FABRIC_DELIVERY`, `SALES_INVOICE`
(`app/services/accounting/posting_rules.py`).

**Proposed event→posting mappings (illustrative; to be confirmed by Indian CA):**

| Event | Dr | Cr | Note |
|---|---|---|---|
| Issue yarn/roll to job worker (custody) | *(stock relieved; custody sub-ledger)* | inventory account | GL-neutral or a "Goods with job worker" control account — **CA decision** |
| Receipt of processed output | new-stage inventory (1070/1085/new) | custody control | at accumulated actual cost |
| Job-work charge (vendor bill) | WIP / inventory (capitalise) **or** expense | 2020/2010 payable | **capitalise-vs-expense = CA decision** |
| Abnormal loss at vendor | abnormal-loss expense | inventory/custody | policy decision (§12 MEDIUM-5 analogue) |
| GST on job-work service (vendor) | Input CGST/SGST/IGST (ITC) | payable | ITC eligibility — CA |

**Open CA items (do not auto-post):** capitalise vs expense of job-work charges;
abnormal-loss/rejection treatment; GST/ITC on job-work service; **TDS** (e.g. 194C)
applicability; Semi-Finished/Trading GL account choice; stock valuation inclusions.
Preserve double-entry, posting/reversal controls, period locks, audit, reconciliation.

---

## 20. GST FORM ITC-04 — Job Work Movement Tracking and Return Preparation

> **Design guidance, not legal/tax certification.** Verified against secondary sources
> on **2026-11-14**; exact current column headers / JSON schema **UNRESOLVED** here and
> must be confirmed against the live GSTN offline utility before implementation.

**What ITC-04 is.** A Rule 45(3) **job-work movement statement/return** — **not** an
ITC claim and **not** a substitute for GSTR-1 or GSTR-3B.

**Filing frequency & due dates (verified 2026-11-14; basis Rule 45(3), amended by
Notification 35/2021-Central Tax eff. 01-Oct-2021; Section 143 CGST Act):**
- Preceding-FY **aggregate turnover > ₹5 crore** → **half-yearly**: Apr–Sep due
  **25 Oct**; Oct–Mar due **25 Apr**.
- **≤ ₹5 crore** → **annual**, due **25 Apr** after the FY.
- **Do not hard-code quarterly.** (One secondary source claimed quarterly; it conflicts
  with Rule 45(3) and is treated as erroneous.) Store **legal basis, notification
  reference, period, and an audited due-date override** per return; determine frequency
  from preceding-FY aggregate turnover + current law.

**Table mapping (business-level; confirm exact fields against the official utility):**
- **Table 4 (utility tab "Table 4A"):** goods **sent** for job work, incl.
  direct-to-worker movement — worker identity/state, original challan no/date,
  description, UQC, quantity, taxable value, input vs capital-goods classification,
  applicable tax fields.
- **Table 5A:** goods **received back from the original** job worker — return
  challan/date, original-challan link, description, UQC/qty, nature of job work,
  loss/waste.
- **Table 5B:** goods received from a **different/subsequent** worker — preserve the
  principal → original worker → subsequent worker → principal chain, with
  challan/date/identity/description/UQC/qty/nature/loss.
- **Table 5C:** goods **supplied from the job worker's premises** — original challan,
  worker, supply invoice/date, description, UQC/qty, prescribed fields.

**Section 143 time limits (verified 2026-11-14):** inputs **1 year**, capital goods
**3 years** to return/supply from the worker's premises (reckoned from date sent, or
from the worker's receipt date for direct-to-worker); Commissioner may extend inputs by
**up to 1 further year** and capital goods by **up to 2 further years**. Non-return in
the window = **deemed supply** on the original send date (tax + interest exposure).
**Moulds, dies, jigs, fixtures, tools excluded.** Provide deadline **alerts** and
deemed-supply exposure **for CA review** — **never** auto-create a liability, invoice,
voucher, or GSTR-1 entry.

**Internal roll-wise supporting schedule (NOT invented statutory fields):** Roll ID,
lot, dye lot, GSM, width, length, weight, UQC qty, challan refs, vendor, stage,
source/destination, job-work order. Reconcile sent / transferred / received /
supplied-from-premises / unused-returned / consumed / loss-waste / rejected /
reprocessed / outstanding. **Do not** classify every variance as loss; support partial
receipts across periods; prevent duplicate challan/line reporting; make UQC conversions
transparent (never silently aggregate incompatible units).

**Workspace:** period dashboard + company/GSTIN context; Tables 4/5A/5B/5C as
applicable; internal roll-wise schedules; validation errors/warnings + review
workflow; export to **GSTN offline utility JSON only after verifying the official
schema**; manual filing checklist + ARN/reference capture; optional official API only
if supported/authorised; **no** unofficial endpoints, no browser-filing automation, no
storage of portal passwords/OTPs. **Lifecycle:** Draft → Validation Failed / Ready for
Review → Approved → Exported → Submitted/Filed, capturing ARN/reference, filing date,
user, and an **immutable filed snapshot**; post-filing changes raise traceable alerts
and follow permitted correction processes. Company/GSTIN permissions, factory
source-data controls, CA/reviewer approvals, immutable audit.

**UNRESOLVED (must verify against primary sources before build):** exact current table
column headers and JSON keys of the GSTN offline utility; current utility version/tab
names; precise interest provision on deemed supply; any post-2021 amendments to
frequency/periods. Official references to verify against: GSTN offline utility
(`tutorial.gst.gov.in/downloads/formgstitc04.pdf`) and the CBIC CGST Rules (Rule 45) /
Section 143 — see §34.

---

## 21. Manufactured-fabric GST Tax Invoice (design)

Reuse Stage-3 engine (`app/services/printing/pdf_sales.py`, builders in
`sales_documents.py`, numbering/reprint via `printing/numbering.py` +
`models/printing.py`). **Precedence:** manufactured invoice must show **roll-wise
detail** — lot number, individual Roll ID, fabric/quality, GSM, width, shade/dye lot
(where available), individual roll **weight**, length, rate/quantity, taxable amount,
GST rate, CGST/SGST/IGST, line total. Several rolls under one commercial item → show a
readable roll breakdown. **Partial dispatch shows only actually-dispatched
rolls/quantity/weight.** Preserve existing GST calculations (`services/tax.py`). Keep
GST summary, amount-in-words, bank, terms/declaration (declaration only if configured —
none invented), acknowledgement/signature. Visual hierarchy per the supplied reference
image (logo/legal top-left; TAX INVOICE top-right; Bill-To/Ship-To panels; navy item
header; compact GST summary; balanced A4). Preview rendered in §26.

## 22. Manufactured-fabric Delivery Challan (design)

Header: logo, legal name/address/GSTIN/CIN/contact, **DELIVERY CHALLAN** top-right,
subtitle exactly **"Sales dispatch — finished fabric"**, challan no/date. Three
bordered panels **CONSIGNEE | DELIVERY ADDRESS | DISPATCH FROM**; SO + invoice
reference if issued. **Roll-wise column order (exact):** 1 Serial · 2 Roll No ·
3 Fabric · 4 GSM · 5 Width · 6 Shade · 7 Dye Lot · 8 Warehouse · 9 Length (m) ·
10 Weight (kg). Omit a column **only** if the source genuinely lacks the data; never
fabricate. Each dispatched roll once, actual dispatched weight, readable wrapping,
subtle alternating shading, navy header, aligned numeric columns. Transport:
transporter, vehicle, E-way no/date where applicable. Totals: roll count, actual
dispatched weight, total length when available. **Four** signatures: Prepared By,
Checked By, Authorised Signatory, Received By. Print **exactly**: *"Quantities shown
are the actual dispatched weights for this challan. Not a tax invoice — the GST tax
invoice is a separate document."* Multi-page: repeat headings every page; continuation
repeats challan no + "(contd.)"; dynamic row heights; never split a row; totals/
transport/signatures/note on the final page; page number each page; no fixed 20-row
limit; CANCELLED watermark **only** if the source transaction is actually cancelled;
reprints preserve identity/status. The Stage-3 renderer already implements most of
this; previews in §26 (the Stage-3 renderer is uncommitted and only used here to
produce design previews).

## 23. Manufactured-fabric Packing List (design — new)

A **separate** document (not a renamed invoice/challan), **manufactured fabric only**.
Header: company identity/branding; customer/consignee; delivery address;
SO/invoice/challan/dispatch refs where applicable; transporter/vehicle/E-way where
available; title **PACKING LIST**. Each physical roll is its **own** line (never
combine): serial, package no (only if recorded), Roll ID, lot, fabric/quality, GSM,
width, shade, dye lot, length, individual weight, remarks. Summary: total packages
(only if package data exists), total rolls, total length, net fabric weight,
gross/package weight **only if actually recorded** (never infer gross). Declaration +
Prepared/Checked/Authorised/Receiver. No GST calculation; linked to **actual**
dispatch; source rolls each once (unless real package data justifies multiple package
entries); accurate partial dispatch; exclude cancelled/reversed dispatches; reconcile
to challan and invoice if issued; **no transaction side effects** on preview/print/
reprint. Preview in §26.

---

## 24. Trading fabric purchase and sales (design)

`Supplier PO/invoice → GRN/partial receipt → TRADING WAREHOUSE → valuation → sales
order → picking → delivery challan → GST invoice → dispatch → COGS`. Track item/
description, quality/construction, GSM, width, shade/colour where relevant, **aggregate
qty/UOM, aggregate total weight** where applicable, purchase rate, GST, freight/landed
cost, supplier invoice/GRN, partial receipts, FIFO purchase-cost layers, warehouse +
supplier balance. **No Roll ID / individual roll tracking / lot / batch / roll
genealogy / individual roll weight.** Trading stock/COGS kept **separate** from
manufacturing stock/costing and production-consumed yarn (separate GL account, §8).
Trading invoice/challan are **item-level aggregate**; **no packing list** (Rule C).

## 25. Direct yarn sales from Yarn Store (design)

`Yarn purchase → GRN → YARN STORE → sales order → lot-wise reservation → picking →
delivery challan → GST invoice → dispatch → FIFO COGS`. **Retain and display the
existing Yarn Store lot number** on SO/picking/invoice/challan (Rule B); multiple lots
= separate traceable lines; capture count/composition/colour/lot/qty/UOM/weight/rate/
taxable/GST. **Never** generate replacement lots; never sell beyond lot balance;
preserve lot history; reprint never changes lot identity. Direct-sale COGS = actual
FIFO yarn purchase cost. **Direct yarn sales create no knitting WIP** and stay separate
from yarn issued/consumed for production. Production and direct-sale reservations use
the **same Yarn Store availability pool** (existing `ProductionReservation` pattern
extended) to prevent double allocation. **No Roll ID, no packing list.**

## 26. Numbering / rendering / print behaviour

Reuse existing company/FY-scoped, concurrency-safe numbering (`sequences.py`,
`models/audit.py::DocumentSequence`) and the Stage-2 `PrintedDocument` reprint
registry. **General print rules:** preview/print creates **no** business transaction;
a number is allocated **only at actual issue/confirmation, not preview**; reprint
reuses the same number and original status; cancellation retains identity + audit;
numbering is company/FY-scoped and concurrency-safe; use existing logo/legal details;
document renderer limitations honestly. Previews are rendered as **design artefacts**
labelled *"DESIGN PREVIEW — SAMPLE DATA — NOT A TAX DOCUMENT"* under
`docs/design-preview/` (see §34 asset list).

---

## 27. Reporting and reconciliation (design)

Reports: stock by company/factory/warehouse-category/physical-warehouse; yarn lot
balances/reservations; grey/dyed/semi-finished/finished roll balances; aggregate
trading-fabric stock; **job-worker custody + ageing**; material issued/received/
unused-returned/rejected/lost/outstanding; internal vs outsourced by process; actual
cost by roll/kg/metre and process; incomplete/unbilled job-work cost; roll genealogy/
traceability; vendor performance/reconciliation; dispatch/invoice/challan/packing-list
reconciliation; **inventory valuation → GL** (extend existing
`stock_reconciliation.py` to include custody + new categories); ITC-04 period
reconciliation + deadline alerts.

## 28. Permissions and audit (design)

Permissions by company/factory, warehouse/process, job-work issue/receipt/approval,
configuration, cost visibility, GST/ITC-04 preparation/approval, document
cancellation/reprint — layered on the existing RBAC (`app/seed/rbac.py`) and tenant
guards (`app/core/context.py`, `deps.py`). Audit (extend `app/models/audit.py::
AuditLog`): user, timestamp, company/factory, source/destination, before/after for
controlled edits, approvals/reasons, posting/reversal refs, original document identity
on reprint/cancellation, immutable filed ITC-04 snapshots.

## 29. Edge cases and failure handling (design)

For each, define expected stock, cost, accounting, traceability, status, audit, and
recovery: partial issue/receipt; excess/shortage; unused yarn return **to original
lot**; one-to-many split; many-to-one merge; partial processing; reprocessing;
rejection; normal/abnormal loss; roll damaged/lost at vendor; vendor failure/change;
worker-to-worker transfer; same/different vendor; physical vs logical intermediate;
late vendor bill/freight (estimate→actual reconciliation); duplicate challan/receipt/
import (idempotency keys); cancelled/reversed documents; **closed accounting period**
(reuse `ClosedPeriodError`); warehouse/factory transfer; concurrent stock reservation;
concurrent Roll ID generation (reuse Stage-2 sequence concurrency proof); partial
sales dispatch; aggregate-only trading stock; multi-lot direct yarn sale; competing
production/sales reservations; missing optional specs; missing length where genuinely
unavailable (omit column); GSTIN/state/UQC errors; multi-company/factory permission
violations (404/403 as today); ITC-04 period boundaries / deadline exposure.

## 30. Historical data / migration risks

All proposed schema is **additive**; no migration is executed in this assignment.
**Do not:** renumber historical Roll IDs; rewrite historical stock/accounting entries;
change dye recipes/chemical masters/history; enable chemical-BOM explosion/reservation;
silently change existing costing/GST; create duplicate inventory/GL entries; mix
trading and manufacturing stock. **Backfill needs/risks (identify only, do not
perform):** adding `UniqueConstraint(company_id, roll_no)` needs a prior read-only
duplicate audit (**high-risk/irreversible → explicit approval**); categorising existing
warehouses (`kind` → category) needs a reviewed mapping; back-populating `RollKind.DYED`
for historical dyed rolls created as `FINISHED` is optional and must not alter
valuation. Mark irreversible/high-risk changes for explicit approval.

## 31. Test and acceptance matrix (design; separates existing vs proposed)

**Existing tests (present now):** Stage-2 numbering concurrency (SQLite + real-PG);
Stage-3 sales-document suite (10); grey/dyed costing + reconciliation; dyed-dispatch;
production cancel/return; full suite 453 passed / 4 PG-gated skips (as of `8022693`).
**Proposed (future, not yet written — not claimed passing):** Roll-ID
compatibility/concurrency + identity through issue/transfer/return/reprint; new-output
IDs per process; split/merge genealogy edges; five warehouse categories + custody
valuation (no double count); internal/outsourced/hybrid toggles incl. disabling a mode
with in-progress jobs; same/different vendor + physical/logical intermediate; partial
issues/receipts reconciliation; unused yarn returns to original lot; FIFO yarn +
trading valuation; direct yarn sales with existing lots; competing production/sales
reservations; aggregate-only trading; actual-cost allocation + late vendor invoices;
posting/reversal + stock-to-GL incl. custody; invoice/challan/packing-list no-side-
effect printing; multi-page PDF, cancellation, reprint; GST/ITC-04 schema/frequency/
extensions/Section 143 alerts; multi-company/factory isolation/permissions/audit.
Each gets measurable acceptance criteria before implementation.

## 32. Future implementation phases (design)

| Phase | Deliverable | Key dependencies / risk / gates |
|---|---|---|
| P1 | Warehouse categories (incl. SEMI_FINISHED, TRADING) + `RollKind.DYED` | additive migration; warehouse-mapping review; CA GL-account choice |
| P2 | Canonical roll generator unification (grey/dyed/finished via ROLL sequence) + roll uniqueness audit | **no historical renumber**; duplicate audit before any unique constraint |
| P3 | Job-work framework: vendor classification, orders, challans, **custody ledger**, charges | custody-no-double-count; posting rules for CA review |
| P4 | Outsourced knitting/dyeing/finishing flows + hybrid config | depends P1–P3; actual-cost allocation; mode-disable safety |
| P5 | Roll genealogy edges (many-to-many) + costing across job work | depends P2–P4 |
| P6 | Trading fabric + direct yarn sales | separate GL; lot reuse (no new lots) |
| P7 | Sales documents: roll-wise invoice finalisation + **packing list** | reuse Stage-2/3 engine |
| P8 | **ITC-04 / Section 143** workspace + export | **CA + official schema verification gates**; immutable snapshots |
| P9 | Reporting, reconciliation (custody + categories), permissions, audit | extends `stock_reconciliation.py` |

Each phase: dependencies, data/model changes, integration points, migration risk,
accounting/GST review, tests, acceptance gates, rollback considerations — to be
detailed at approval time.

## 33. Open decisions and approvals

Repository gaps: no central roll generator (§5); `Warehouse.kind` lacks categories
(§7); genealogy single-parent only (§6); job-work model billing-only (§10); no
trading/yarn-sales/packing-list/ITC-04 (§4). **Process/policy decisions:** same-vendor
physical vs logical intermediate; factory-level overrides for process mode;
cost-allocation & loss policy (normal vs abnormal); capitalise-vs-expense of job-work
charges; packing-list applicability per customer; document numbering issue-point.
**Statutory (Indian CA / GST practitioner / legal):** current ITC-04 schema & legal
interpretation; Section 143 interest provision; TDS (194C) on job-work; GST/ITC on
job-work service; worker-to-worker statutory documentation; deemed-supply exposure;
Semi-Finished/Trading GL treatment. **Finance/management:** valuation inclusions,
unbilled-cost policy. None of these are decided here; each is routed to the named
approver.

## 34. Source references and verification dates

**Repository evidence (HEAD `8022693`):** `backend/app/models/manufacturing.py`,
`…/masters.py`, `…/dyeing_cost.py`, `…/jobwork.py`, `…/quality.py`, `…/fabric_bom.py`,
`…/audit.py`, `…/printing.py`, `…/tenancy.py`; `backend/app/services/{inventory,
accounting,bom,dyeing,sales,order_lifecycle,tax,sequences,factory_transfer}.py`,
`…/manufacturing/{knitting,roll_ops}.py`, `…/printing/*`; `backend/app/core/{db_router,
context,deps}.py`; `backend/app/seed/{chart_of_accounts,rbac}.py`.

**GST / ITC-04 (secondary sources; verified 2026-11-14 — confirm against primary CBIC/
GSTN before implementation):**
- Rule 45 / ITC-04 filing frequency & due dates (turnover > ₹5 cr half-yearly; ≤ ₹5 cr
  annual; basis Rule 45(3), Notification 35/2021-CT eff. 01-Oct-2021):
  https://taxguru.in/goods-and-service-tax/complexities-sending-goods-job-work-filing-form-gst-itc-04.html ·
  https://tallysolutions.com/gst/job-work-transactions-under-gst/ ·
  https://cleartax.in/s/gst-itc-04-offline-utility
- Section 143 time limits (inputs 1 yr / capital goods 3 yr; Commissioner extensions;
  deemed supply; moulds/dies/jigs/fixtures/tools excluded):
  https://www.taxtmi.com/article/detailed?id=15915 · https://taxguru.in/goods-and-service-tax/complexities-sending-goods-job-work-filing-form-gst-itc-04.html
- ITC-04 Tables 4/5A/5B/5C + offline utility (JSON upload; online filing only):
  https://taxguru.in/goods-and-service-tax/faqs-gst-itc-04-offline-utility.html ·
  official utility PDF: https://tutorial.gst.gov.in/downloads/formgstitc04.pdf
- **UNRESOLVED:** exact current table column headers / JSON keys / utility version;
  precise deemed-supply interest provision; any post-2021 frequency amendments —
  verify against the live GSTN offline utility and CBIC CGST Rules before build.

---

### Appendix A — Design preview assets

Rendered, clearly-labelled **SAMPLE** previews are under `docs/design-preview/` (page
images) with the mark *"DESIGN PREVIEW — SAMPLE DATA — NOT A TAX DOCUMENT"*. They
illustrate the manufactured-fabric invoice (roll-wise), delivery challan (multiple roll
counts incl. a multi-page case, a no-length case, and a cancelled case), and the
packing list. They are **design artefacts only**, produced for visual review; they are
**not** application code and carry **no** transaction effect. See
`docs/design-preview/README.md`.
