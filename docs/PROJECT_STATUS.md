# Project status & pending list

Audit of the implementation against the master specification (the 24-section
brief). Updated **2026-11-11**. Test suite: **263 passing** (+1 Postgres-gated)
(`cd backend && pytest`).

> **Multi-company / multi-factory programme (branch `feat/multi-company-production-ready`).**
> Phase 0 (baseline audit) and Phase 1 (architecture design + additive
> foundation) are done. See `docs/PRODUCTION_READINESS_BASELINE.md` and
> `docs/MULTI_COMPANY_ARCHITECTURE.md`. The foundation adds — non-destructively —
> an `Organization` group entity, a normalised `GSTRegistration`,
> `UserCompanyMembership` / `UserFactoryMembership` tables, a nullable
> `companies.organization_id`, a **backward-compatible** request-scoped tenant
> resolver (`app/core/context.py`, falls back to `User.company_id`), and the
> first **incremental** Alembic migration (`0002`, additive-only, upgrade+downgrade
> proven). Enforcement across routes and any legacy-data backfill are **gated on
> business decisions** (see the architecture doc §6) and not yet applied.

Legend: ✅ done & tested · 🟡 partial / simulated / readiness-only · ❌ not built.

---

## ✅ Built & tested

| Area | Coverage |
|---|---|
| Double-entry engine | balanced-only posting, atomic commit, immutable ledger guard, reversal-only corrections, idempotent posting refs, closed-period locks, configurable posting rules, 13 voucher types, opening balances |
| Chart of accounts | full textile COA seeded (assets/liabilities/equity/income/COGS/indirect), hierarchical, configurable |
| General ledger | GL + customer/supplier/bank sub-ledgers, drill-down to source |
| Trial balance | automatic, debit=credit validation, period/branch scoped |
| Financial statements | P&L, Balance Sheet, Cash Flow, Debtors & Creditors ageing |
| GST | CGST/SGST/IGST, configurable rates, purchase/sales registers, GSTR-1, GSTR-3B, book-vs-register reconciliation, **adjustment vouchers (ITC reversal / reclaim / RCM / round-off / manual)** |
| **Year-end close** | closing voucher zeroes income/expense into retained earnings + locks the year's periods (idempotent) |
| Banking | receipts/payments/contra, CSV statement import, reconciliation |
| Procurement | chemical/dye GRN → FIFO stock + GST purchase voucher |
| **Sales pipeline** | enquiries → quotations → convert to SO; credit-limit control on SO confirm; customer/supplier statements |
| **PO / SO lifecycle** | PO place/confirm → GRN roll-up (PARTIAL/COMPLETED); SO place/confirm → delivery challan → **COGS**; invoice |
| **Credit / debit notes** | sales credit note (receivable + output GST reversal); purchase debit note (goods return, FIFO relief + ITC reversal); both in GST registers |
| Knitting | machine/shift/operator/yarn-lot, greige rolls, WIP/greige postings |
| Greige inventory | per-roll ID, barcode/QR, auto-numbering, lineage, labels |
| **Finishing → FG transfer** | greige→finished-goods GL transfer on finishing (keeps sub-ledger reconciled) |
| Yarn lot tracking | priced lots, FIFO per lot, traceability |
| Batching + dyeing | dye batches/baths, roll traceability, approved-recipe scaling |
| Color Service | dose-order builder, **simulator** adapter, bath-wise consumption register, correction dosing, idempotent ingest |
| Chemical inventory | FIFO perpetual, value-based layers, GL reconciliation |
| **Valuation / stock ops** | FIFO + **FEFO** draw order, **weighted-average** valuation, **warehouse transfers**, **roll split / merge**, **physical stock count**, **sales-return restock** |
| Costing | per-kg knit/dye/finish, shade/machine/order, std-vs-actual, customer profitability |
| **Work centres** | machines are WORK cost centres under departments; production/dyeing postings tag the cost centre; cost-centre ledger report |
| **Overhead allocation** | configurable pools absorbed into work centres by output-kg / machine-hours / equal basis; posts Dr WIP / Cr source expense; fully-loaded cost per work centre |
| **Fixed assets** | register, SLM/WDV depreciation, disposal (gain/loss) |
| **Payroll** | salary structures, PF/ESI/PT/TDS, payslips, balanced monthly PRV run |
| **Approvals** | configurable maker-checker rules; supplier-payment, purchase-order **and journal/voucher** approval gates via a generic executor (self-approval blocked, N approvals, confirms/posts the document on approval). Held vouchers store their full posting payload and are validated balanced at submit time — nothing unbalanced or unauthorised reaches the ledger early |
| **Notifications & attachments** | in-app notification feed (broadcast on approval requests, unread badge) + document attachments (base64 upload, size-guarded, list & download) |
| Quality/finishing | inspections, complaints, finishing processes |
| Hardware | weighing-scale + knitting-machine integration (simulated), efficiency |
| Dashboards | Owner / Factory / Accounts / Chemical |
| Reports | Excel + PDF exports |
| Security | JWT, 15 RBAC roles, granular permissions, immutable posted entries; **company-ownership guard** on referenced ids (no cross-tenant supplier/customer/account/warehouse refs); **fail-closed GST place-of-supply**; **production config guard** (rejects debug / weak secret / default DB creds in prod); **per-IP rate limiting** (tight login bucket, health exempt); PostgreSQL FIFO consumption serialized by advisory lock + FOR UPDATE, **proven by a real-Postgres concurrency test** |
| Traceability | order → yarn → roll → batch → bath → dispensing → inventory → GL |
| **Observability / ops** | readiness probe (`/health/ready`, DB-checked), in-process metrics (`/health/metrics`), structured per-request logging with correlation id (`X-Request-ID`), unhandled-error capture → clean JSON 500; committed backup / restore / health-probe scripts + cron example; Docker/compose `HEALTHCHECK` |
| **E-invoice (IRN)** | canonical NIC payload (v1.1) from a posted sales invoice with intra/inter-state CGST-SGST/IGST split; pluggable adapter (offline **simulator** + configurable **GSP** REST adapter for Webtel / Pinnacle, with subscription-key header); IRN/Ack/signed-QR stored, idempotent generate, cancel, `integration_events` audit; generate/cancel from the Sales Invoice page |
| **E-way bill** | NIC EWB payload from the invoice + transport details (mode/vehicle/distance/transporter); same GSP connection as e-invoice, **links to the IRN** when present; EWB no + validity stored, idempotent generate, cancel, audit; transport form + generate/cancel on the Sales Invoice page |
| **Chart-of-accounts management** | create ledger accounts from the UI (code, name, type, parent group, heading) with validation; full COA list — add a specific loan / bank / expense head yourself |
| **Stock on hand** | live stock position (qty + value) by item and warehouse from open FIFO layers; warehouse and item-type filters; fabric grouped by type + greige/finished |
| **Job work** | inward (process others' fabric → bill processing-charge income) and outward subcontracting (send ours out → book the dyer's charge as cost + payable), GST-split, posted via the engine, on a Job Work page |
| **Stores & spares** | needles / machine spares as store items; receive into FIFO stock (Dr Stores Inventory) and issue-to-machine expensed at FIFO cost (Dr Stores Consumption); shows in stock-on-hand |
| **Party & GL ledgers on screen** | customer/supplier running ledgers on their pages; trial balance + general ledger (drill-down by account) on the Reports page |

### Completed in the latest work cycle
- PO / SO lifecycle (receipt & dispatch roll-up, COGS on delivery)
- Fixed assets + depreciation (SLM/WDV) + disposal
- Credit notes (sales) & debit notes (purchase returns)
- Greige → Finished-Goods transfer on finishing (Acceptance Test 12)
- Payroll (structures, statutory deductions, balanced run)
- Maker-checker approval workflows (supplier-payment gate)
- GST adjustment vouchers (ITC reversal / RCM / manual) + year-end closing
- Roll split/merge, warehouse stock transfers, FEFO + weighted-average valuation
- Machines as work centres: cost-centre-tagged production postings + cost-centre report
- Overhead allocation: pools absorbed into work centres (fully-loaded cost)
- Sales pipeline: enquiries/quotations + convert-to-SO, credit limits, party statements
- Manufacturing / Trading / Funds-Flow statements (completes the 10-statement set)
- Physical stock count (inventory adjustment) + sales-return restock
- Approvals extended to purchase orders via a generic executor (dispatch by doc type); in-app notifications + document attachments (upload/list/download)
- Approvals extended to journals / vouchers: a held voucher parks its full posting payload (validated balanced at submit), posts unchanged on approval, stays unposted on reject
- Operability scaffolding: readiness + metrics endpoints, structured request logging with correlation ids, unhandled-error capture; backup/restore/health scripts + cron example + container HEALTHCHECK; OPERATIONS.md runbook
- E-invoice (IRN): canonical NIC payload, simulator + configurable GSP adapter, IRN/QR storage, idempotent generate + cancel, UI on the Sales Invoice page (see EINVOICE_INTEGRATION.md)
- E-way bill: NIC EWB payload + transport details, same GSP connection, links to the IRN, EWB no/validity storage, generate + cancel, UI on the Sales Invoice page
- Chart-of-accounts management (create accounts from the UI); on-screen trial balance + general ledger with drill-down; customer/supplier ledgers on their pages
- Live stock-on-hand by warehouse; job work (inward + outward subcontracting); stores & spares (needle/spare issue-to-machine, costed on issue)

---

## ❌ Pending

### Workflow / controls
_All in-scope maker-checker coverage is done:_ the generic executor now gates
supplier payments, purchase orders **and journals / vouchers**. (Approval
routing could be surfaced on more document types — GRNs, sales invoices — as a
future enhancement, but every control the brief calls for is in place.)

### Integration (needs vendor access)
_The engines are built with an offline simulator and a configurable real
adapter; what remains is the live wiring, which needs the vendor's endpoint and
credentials (and network access) — not code._
1. **E-invoice & e-way bill** — both engines, the simulator and the GSP adapter
   are built (EWB reuses the e-invoice GSP connection and links to the IRN).
   Going live needs your GSTIN's GSP credentials (Webtel / Pinnacle) in the
   environment and, if the GSP's auth differs from the common token flow, a tweak
   to the one adapter seam (`GspAdapter._auth_token`). See
   `docs/EINVOICE_INTEGRATION.md`.
2. **Live Color Service adapter** (SOAP / OPC-UA / Modbus / DB) — only the simulator exists
3. **Bank-feed** integration (CSV import done; live feeds not)

### Deliverables / ops
4. Frontend is the self-contained SPA, **not** the Next.js/TS/Tailwind/shadcn stack in the spec (functionally complete, different stack — a deliberate choice, not a gap)

Backups, error-logging / monitoring infrastructure and the admin/ops runbook are
now **built** (see the Observability / ops row above and `docs/OPERATIONS.md`).

---

## Acceptance tests (brief §23)

18–19 of the 20 end-to-end tests are covered. Remaining weak spots:
- **Tests 5 / 6 / 18** run against the Color Service **simulator**, not verified vendor hardware.
- All ledger, inventory, GST, costing and traceability tests (1–4, 7–17, 19–20) pass, including **Test 12** (WIP→FG) now that finishing posts the greige→FG transfer.

## Suggested priority order for remaining work
1. Extend approvals to vouchers/POs/journals + notifications/attachments
2. Live external integrations (when vendor credentials/hardware are available)
3. Operational docs, backups, monitoring
