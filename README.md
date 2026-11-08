# Integrated Textile Manufacturing ERP

A production-grade ERP for an integrated knitting, dyeing and finishing textile
company, built around a **correct double-entry accounting engine** with Indian
GST support, full traceability from sales order to general ledger, and a
specified (pending-verification) Color Service dispensing integration.

> **Scope honesty.** The brief describes a product a team builds over many
> months. This repository delivers **Stages 1–4 plus the accounting core
> (Stage 6)** as working, tested, database-backed code — architecture, the full
> data-model foundation, auth/RBAC, the double-entry engine + trial balance, the
> procurement→knitting→greige→dyeing→dispensing→consumption chain, FIFO
> inventory, and a **Color Service simulator** — plus the four design documents.
> Still pending (specified in `docs/`): financial statements (P&L/Balance
> Sheet/Cash Flow), banking/reconciliation, GST return preparation, management
> dashboards, finishing/quality, and the Next.js UI. Nothing here claims GST
> filing, bank feeds or **live** Color Service integration are production-ready —
> the dispensing integration runs against a simulator pending vendor
> verification.

## What works today (verified by tests)

**Accounting core**
- **Double-entry posting engine** — balanced-only posting, one-sided lines,
  Decimal money (no floats), idempotent `posting_reference`, atomic commit.
- **Immutable ledger** — posted vouchers/lines cannot be edited or deleted
  (enforced at the ORM session level); corrections via reversal vouchers.
- **Chart of accounts** — full textile COA seeded, configurable & hierarchical;
  **configurable posting rules** map each business event to accounts.
- **Accounting periods** (Indian FY, closed-period locks), **16 voucher types**,
  gap-free numbering, **trial balance** + **account ledger** drill-down.
- **Financial statements** (Stage 7) — **Profit & Loss**, **Balance Sheet**
  (balances by construction, folding current-year profit into equity),
  **Cash Flow** (operating/investing/financing, ledger-derived), **debtor /
  creditor ageing** (FIFO allocation), and the **Manufacturing account**
  (cost of goods manufactured via the WIP account), **Trading account** (sales −
  COGS → gross profit, using fabric-stock movement) and **Funds Flow statement**
  (funds from operations + sources/applications) — all from the posted ledger.
- **Indian GST** (Stage 7) — **sales & purchase registers**, **GSTR-1** summary
  (B2B/B2C, rate-wise, HSN-wise), **GSTR-3B** summary (outward tax, eligible
  ITC, net payable), and a **books-vs-registers reconciliation**; rates come
  from configurable tax codes, never hardcoded.
- **Banking** (Stage 7) — customer receipt / supplier payment / contra / bank
  charge postings, **CSV bank-statement import** (idempotent), and **bank
  reconciliation** (auto-match by amount + date, outstanding/in-transit items,
  difference explained).
- **Fixed assets & depreciation** — asset register with capitalisation posting,
  **straight-line and written-down-value** depreciation (floored at salvage),
  a **monthly depreciation run** that posts one balanced DEP voucher for the
  batch (idempotent per period), and **disposal** with automatic gain/loss.
- **Payroll** — per-employee salary structures (basic / HRA / allowances +
  configurable **PF / ESI / professional tax / TDS** rates), payslip
  computation, and a **monthly run** that posts one balanced PRV voucher
  (salaries + employer PF/ESI debited; net pay and the statutory payables
  credited), idempotent per pay period.
- **Maker-checker approvals** — configurable rules (per document type and
  amount threshold) route a document for approval; the requester can never
  approve their own request, and only an approved request completes the action.
  Wired to **supplier payments**: a payment at/above the threshold is held until
  the required approvals arrive, then posts; below it, it posts immediately.
- **GST adjustment vouchers** — **ITC reversal** (Dr reversal expense / Cr input
  tax), **ITC reclaim** (the mirror — re-take credit earlier reversed),
  **reverse-charge (RCM)** self-assessment (books output liability + input
  credit), **round-off** (the rounding difference against a Rounding Off
  account), plus a manual two-account adjustment; all balanced, idempotent.
- **Year-end closing** — posts a closing voucher that zeroes income & expense
  accounts into **Retained Earnings** and locks every period in the year
  (idempotent); the P&L then nets to zero and the profit carries forward.

**Operations (Stage 2–4)**
- **Procurement** — chemical/dye GRN → FIFO stock layer + GST purchase voucher.
- **FIFO perpetual inventory** — costed receipts/consumption; stock sub-ledger
  reconciles with the GL control accounts.
- **Valuation methods** — **FIFO** and **FEFO** (earliest-expiry-first) draw
  orders, plus a **weighted-average** valuation basis; lot expiry flows from the
  GRN onto the cost layer.
- **Stock transfers** — move an item between warehouses at the drawn cost
  (GL-neutral); recorded as a stock-transfer document.
- **Roll split / merge** — carve a roll into children (each inheriting lineage
  and its share of the stock at cost) or merge several active rolls into one;
  both conserve stock value and reorganise only the sub-ledger (no voucher).
- **Physical stock count** — record the counted quantity against book stock and
  post the costed variance (shortage → Dr Inventory Adjustment / Cr inventory;
  surplus → the reverse at average cost), keeping the sub-ledger tied to the GL.
- **Sales-return restock** — a credit note that also takes returned rolls back
  into finished-goods stock at their dispatch cost and reverses the COGS booked
  on delivery.
- **Lot tracking** — chemicals/dyes **and yarn** are received as priced lots
  (own lot master + FIFO stock per lot + accounting); knitting can draw from a
  specific yarn lot, so greige rolls are traceable to the exact yarn lot.
- **Knitting** — yarn issue (FIFO, by lot) → traceable greige rolls + WIP/greige
  postings.
- **Recipe scaling** — %OWF / grams-per-kg (250 kg × 1.4% = 3.5 kg), approved
  versions only.
- **Purchase orders** — place and confirm a PO; GRNs matched to its lines roll
  the order up to PARTIAL / COMPLETED on received quantity (received qty is
  summed from the matched receipts, so status never drifts).
- **Sales pipeline** — **enquiries** → **quotations** → convert an accepted quote
  into a sales order; **credit-limit control** blocks confirming an SO that would
  push a customer's exposure (outstanding receivable + order value incl. GST)
  past their limit (0 = unlimited).
- **Sales orders & dispatch** — place and confirm an SO; a **delivery challan**
  dispatches finished rolls, taking fabric out of stock (FIFO, at actual
  sub-ledger cost) and booking a balanced **COGS** voucher; the SO rolls to
  PARTIAL / COMPLETED on delivered quantity, and a roll can't be dispatched
  twice. The revenue invoice follows the dispatch.
- **Party statements** — customer / supplier statement of account from the
  posted ledger (opening balance, each transaction with a running balance,
  closing balance).
- **Credit / debit notes** — a **sales credit note** reverses the receivable and
  output GST (price adjustment / short shipment); a **purchase debit note**
  returns chemicals (relieves FIFO stock), reducing the payable and input GST.
  Both post balanced vouchers and flow into the GST registers as negatives.
- **Greige → finished-goods transfer** — finishing a roll moves its carried cost
  from the greige inventory account to finished-goods, so dispatch COGS credits
  the right control account and the sub-ledger stays reconciled.
- **Batching** — assign greige rolls to a dye batch (the dyeing lot); the batch
  fabric weight is summed from the selected rolls, a roll can't be double-batched,
  and each roll is stamped with its batch for traceability.
- **Dyeing** — batches/baths with roll traceability and the approved recipe.
- **Shop-floor hardware** — **weighing scale** and **knitting-machine**
  integration (vendor-neutral adapter + simulator, idempotent ingest), a machine
  **efficiency** summary, and printable **fabric-roll labels (PDF + QR)**.
- **Color Service** — dose-order builder, **simulator** adapter, idempotent
  ingest → immutable dispensing transactions → **consume stock exactly once** →
  Dr WIP / Cr inventory; **correction dosing** as separate transactions;
  **reconnect/replay never duplicates**; per-bath **consumption register**
  (recipe vs actual, variance, cost, per-kg).
- **Sales** — GST sales invoice posting (receivable + output tax).

- **Stock vs GL reconciliation** (TEST 17) — per control account, perpetual
  **value-based** FIFO inventory valuation vs the general-ledger balance, with
  per-account differences and an `is_reconciled` flag; ties to the paisa.
- **Report exports** (Stage 9) — one-call **Excel (.xlsx)** and **PDF** export
  for trial balance, P&L, Balance Sheet, cash flow, debtor/creditor ageing, GST
  sales/purchase registers, GSTR-3B, and the dye-bath consumption register
  (one report-document model renders to both formats).

- **Manufacturing costing** (Stage 8) — batch cost (dye + chemical), **standard
  vs actual** (recipe standard cost vs dispensed cost, with variance),
  **order- and customer-wise profitability** (revenue − actual cost → gross
  profit + margin), machine- and shade-wise cost, and cost-per-kg (knitted /
  dyed) — all from actual posted consumption and revenue.
- **Work centres** — each machine is a work centre (a WORK cost centre under its
  department); issuing yarn and producing fabric (and dyeing) tags the machine's
  cost centre on the WIP/greige lines, so cost accumulates **per machine in the
  ledger** and rolls up to the department. A **cost-centre report** reads these
  balances back from the posted ledger.
- **Overhead allocation** — configurable overhead **pools** (electricity, steam,
  maintenance, depreciation …) are absorbed into the work centres by a chosen
  **basis** (output kg / machine hours / equal split): an allocation run posts
  Dr WIP (per work centre) / Cr the source expense account, so each machine's
  cost centre shows its **fully-loaded** cost (direct + allocated). Idempotent
  per pool and period; the amount is explicit or pulled from the account balance.
- **Finishing & quality** (Stage 8) — finishing transactions (hydro/slitting/
  drying/stenter/compacting/raising/heat-setting) with shrinkage and roll
  finishing; **quality inspection** auto-graded from Delta E + shade match →
  accept/rework/reject (updates roll status); **customer complaints** with
  resolution; and a quality summary (rejection rate, avg Delta E, open
  complaints).
- **Management dashboards** (Stage 8) — Owner (revenue, gross/net profit, cash &
  bank, receivables, payables, inventory value, sales trend), Factory (knitting/
  dyeing output, active batches, chemical cost), Accounts (today's receipts/
  payments, bank balances, debtors/creditors, GST payable, TB status), and
  Chemical (dye/chemical cost, top consumption, stock alerts, dispensing errors).
- **Web UI** — a self-contained React SPA (dark-navy sidebar, KPI cards, Recharts
  charts) served at **`/app/`**: four dashboards; **transaction entry** —
  Purchase (GRN), Knitting production, Sales invoice, and a guided Dyeing flow
  (batch → bath → simulate dispensing → live consumption register) that drives
  the real posting engine and Color Service simulator; a **Vouchers** page for
  manual journal/payment/receipt/contra entry (with a live balance check);
  master management (a generic **Master Data** manager for tax codes, chemicals,
  machines, shades, fabric types and yarns, plus customers, suppliers,
  warehouses); an **Administration** page (open/close accounting periods, create
  users and assign roles); **Quality & Finishing** and **Costing & Profitability**
  pages; and authenticated Excel/PDF report downloads. No build step and no
  runtime CDN — libraries are vendored and the JSX is precompiled.

**Platform** — JWT auth + RBAC (15 roles), REST API, Alembic migrations,
idempotent seed + demo data, Docker Compose.

`207 passing tests` cover the accounting invariants, the nine worked posting
examples, correction dosing, RBAC, full-cycle trial balance, procurement,
PO / SO lifecycle (receipt + dispatch roll-up, COGS on delivery), credit /
debit notes, the greige→finished-goods transfer on finishing, knitting,
roll auto-numbering + lineage, fixed assets (SLM/WDV depreciation + disposal),
payroll (PF/ESI/PT/TDS + balanced run), maker-checker approvals, GST
adjustments (ITC reversal / reclaim / RCM / round-off), year-end closing, recipe scaling, the full
dispensing cycle, end-to-end traceability, the financial statements (P&L /
Balance Sheet / Cash Flow / ageing), GST (registers / GSTR-1 / GSTR-3B /
reconciliation), and the REST flow. See the acceptance-test map below.

## Design documents (start here)

| Doc | Contents |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Modular-monolith design, module map, posting engine, integrity rules |
| [`docs/ER_DIAGRAM.md`](docs/ER_DIAGRAM.md) | Mermaid ER diagrams + the master traceability chain |
| [`docs/ACCOUNTING_POSTING_MATRIX.md`](docs/ACCOUNTING_POSTING_MATRIX.md) | Dr/Cr rules for every business event |
| [`docs/COLOR_SERVICE_INTEGRATION.md`](docs/COLOR_SERVICE_INTEGRATION.md) | Dispensing integration spec, adapters, simulator |
| [`docs/EINVOICE_INTEGRATION.md`](docs/EINVOICE_INTEGRATION.md) | E-invoice (IRN) engine, simulator + GSP adapter, going live |
| [`docs/HARDWARE_INTEGRATION.md`](docs/HARDWARE_INTEGRATION.md) | Weighing-scale & knitting-machine integration, roll labels |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Install, configure, back up / recover, upgrade, monitor |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | Day-2 runbook: scheduled jobs, health/metrics, logs, incident playbook |
| [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) | Administrator guide + user guide by workflow |

## Quick start

### Option A — Docker (Postgres + API)
```bash
cd erp
cp backend/.env.example backend/.env      # then edit SECRET_KEY / admin password
docker compose up --build
# API:  http://localhost:8000      Docs: http://localhost:8000/docs
```
The API container waits for Postgres, runs migrations, seeds the demo company
and admin user, then serves.

### Option B — Local (SQLite, no server)
```bash
cd erp/backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL="sqlite:///./erp.db" SECRET_KEY="dev-secret-$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
alembic upgrade head
python -m app.seed.seed
uvicorn app.main:app --reload
```

Then open the **web UI** at **http://localhost:8000/app/** and sign in with the
seeded admin (below). The dashboards show data once the demo/manufacturing data
is present (tests seed it; for a live DB, create documents via the API/UI).

### Run the tests
```bash
cd erp/backend && . .venv/bin/activate && pytest
```

### Rebuild the UI assets (only if you change `app.src.jsx`)
```bash
cd erp/frontend && ./build-ui.sh   # needs npm; re-vendors libs + recompiles app.js
```

### First login
Seeded superuser: **`admin` / `admin123`** (override via `ERP_ADMIN_*` env vars;
change it immediately outside dev).
```bash
curl -s -X POST http://localhost:8000/api/auth/login \
  -d 'username=admin&password=admin123' | python -m json.tool
```

## Project layout
```
erp/
├── docs/                      # the four design documents
├── docker-compose.yml
├── frontend/                 # self-contained React SPA served at /app/
│   ├── index.html            #   dark-navy sidebar, dashboards, warehouse master
│   ├── app.src.jsx           #   source JSX (precompiled to app.js by build-ui.sh)
│   ├── app.js                #   generated — no runtime Babel
│   └── vendor/               #   vendored React / ReactDOM / Recharts (no CDN)
└── backend/
    ├── app/
    │   ├── core/             # config, db, security, Decimal types, ledger guard, deps
    │   ├── models/           # core, masters, accounting, inventory, commercial,
    │   │                     #   manufacturing, dispensing, banking, audit (ORM)
    │   ├── services/         # posting engine, trial balance, financial statements, gst,
    │   │                     #   stock reconciliation, dashboard, exports, inventory (FIFO),
    │   │                     #   banking, procurement, sales, knitting, recipes, colorservice
    │   ├── api/routes/       # auth, masters, accounts, vouchers, reports, operations,
    │   │                     #   banking, dashboard
    │   └── seed/             # chart of accounts, RBAC, seeder, demo data
    ├── alembic/              # migrations (baseline from metadata)
    └── tests/                # 207 tests: invariants, posting matrix, TB, procurement,
                              #   knitting, dyeing+dispensing, traceability, statements,
                              #   gst, banking, exports, dashboards, quality, API
```

## Stage status (brief §22)

> A full done-vs-pending audit against the master spec is kept in
> [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md).


| Stage | Scope | Status |
|---|---|---|
| 1 | Architecture, schema foundation, auth, master data | **Done** |
| 2 | Sales, purchase, knitting, fabric inventory | **Done** (PO/SO lifecycle, GRN→PO roll-up, knitting, greige rolls, dispatch+COGS, sales invoice) |
| 3 | Batching, recipes, lab dips, dyeing | **Done** (batches/baths, recipe scaling; lab-dip model) |
| 4 | Color Service integration + bath-wise consumption | **Done via simulator** (live adapter pending vendor) |
| 5 | Chemical inventory + inventory accounting | **Done** (FIFO valuation + GL reconciliation) |
| 6 | Double-entry engine, chart of accounts, vouchers | **Done** (core + TB) |
| 7 | GST, banking, financial statements, reconciliation | **P&L + BS + Cash Flow + ageing + GST (registers/GSTR-1/3B) + banking + reconciliation done**; e-invoice/e-way pending |
| 8 | Costing, finishing, quality, dashboards | **Costing (profitability/std-vs-actual) + finishing + quality + 4 dashboards + web UI** done |
| 9 | Reports, security hardening, deployment | RBAC + Docker + API + **Excel/PDF exports** done; more report coverage + UI pending |

## Acceptance-test coverage (brief §23)

| # | Acceptance test | Status |
|---|---|---|
| 1 | Chemical purchase + GST → purchase voucher | ✅ `test_stage2_procurement`, `test_example1` |
| 2 | Knitting order + yarn issue with auto inventory/accounting | ✅ `test_stage2_knitting` |
| 3 | Greige batch with all rolls traceable | ✅ `test_stage2_knitting`, `test_stage4_traceability` |
| 4 | 250 kg dye batch + scaled approved recipe | ✅ `test_recipe_scales_to_fabric_weight` |
| 5 | Send recipe to Color Service (verified interface) | ✅ via **simulator** `test_send_dose_order_to_simulator` |
| 6 | Receive actual dispensing transactions | ✅ `test_ingest_dispensing…` |
| 7 | Record every chemical against its dye-bath ID | ✅ `test_ingest_dispensing…`, register |
| 8 | Deduct actual consumption exactly once | ✅ `test_reconnect_does_not_duplicate` |
| 9 | Dr WIP / Cr inventory on consumption | ✅ `test_ingest_dispensing…`, `test_example2` |
| 10 | 200 g correction dosing = separate transaction | ✅ `test_correction_dosing…` |
| 11 | Final actual dye-bath cost | ✅ `test_bath_consumption_register` |
| 12 | WIP → Finished Goods transfer | ✅ `test_example7`, `test_finishing_transfer` (greige→FG on finishing) |
| 13 | Sales invoice with GST postings | ✅ `test_stage2_sales`, `test_example4` |
| 15 | Trial balance debit == credit | ✅ `test_trial_balance…` |
| 18 | Reconnect integration → no duplicates | ✅ `test_reconnect_does_not_duplicate` |
| 19 | No unauthorized change to posted/dispensing data | ✅ `test_posted_*`, `test_rbac_*` |
| 14 | Record customer payment and reconcile the bank | ✅ `test_banking`, `test_reconciliation_flags_unbooked_charge_then_reconciles` |
| 16 | Generate Profit & Loss and Balance Sheet | ✅ `test_profit_and_loss`, `test_balance_sheet_balances` |
| 17 | Reconcile stock valuation with inventory control accounts | ✅ `test_stock_reconciliation` |
| 20 | Trace finished roll → order/yarn/machine/batch/bath/lot/GL | ✅ `test_full_chain_traceability` |

## Non-negotiables honoured

No floating point in money/quantities; posted entries immutable; every voucher
balances or nothing commits; unique posting identity + idempotency; closed-period
locks; multi-company isolation; complete audit trail. See
`docs/ACCOUNTING_POSTING_MATRIX.md` §Invariants.

## License / originality
Original implementation. No proprietary ERP code, branding or copyrighted
designs are used.
