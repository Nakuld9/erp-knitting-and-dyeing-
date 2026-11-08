# Project status & pending list

Audit of the implementation against the master specification (the 24-section
brief). Updated **2026-11-08**. Test suite: **184 passing**
(`cd backend && pytest`).

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
| **Approvals** | configurable maker-checker rules; supplier-payment **and purchase-order** approval gates via a generic executor (self-approval blocked, N approvals, confirms/posts the document on approval) |
| **Notifications & attachments** | in-app notification feed (broadcast on approval requests, unread badge) + document attachments (base64 upload, size-guarded, list & download) |
| Quality/finishing | inspections, complaints, finishing processes |
| Hardware | weighing-scale + knitting-machine integration (simulated), efficiency |
| Dashboards | Owner / Factory / Accounts / Chemical |
| Reports | Excel + PDF exports |
| Security | JWT, 15 RBAC roles, granular permissions, immutable posted entries |
| Traceability | order → yarn → roll → batch → bath → dispensing → inventory → GL |

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

---

## ❌ Pending

### Workflow / controls
1. **Approval coverage for journals** — the maker-checker engine now gates supplier
   payments **and purchase orders** through a generic executor; the remaining
   extension is routing arbitrary vouchers / journals through the same rules.

### Integration (needs vendor access)
2. **Live Color Service adapter** (SOAP / OPC-UA / Modbus / DB) — only the simulator exists
3. **E-invoice / e-way bill** — marked "readiness", not implemented
4. **Bank-feed** integration (CSV import done; live feeds not)

### Deliverables / ops
5. Admin & user manuals beyond `docs/USER_GUIDE.md`; installation / backup guides (API docs = FastAPI `/docs`)
6. Automated backups + error-logging / monitoring infrastructure
7. Frontend is the self-contained SPA, **not** the Next.js/TS/Tailwind/shadcn stack in the spec (functionally complete, different stack)

---

## Acceptance tests (brief §23)

18–19 of the 20 end-to-end tests are covered. Remaining weak spots:
- **Tests 5 / 6 / 18** run against the Color Service **simulator**, not verified vendor hardware.
- All ledger, inventory, GST, costing and traceability tests (1–4, 7–17, 19–20) pass, including **Test 12** (WIP→FG) now that finishing posts the greige→FG transfer.

## Suggested priority order for remaining work
1. Extend approvals to vouchers/POs/journals + notifications/attachments
2. Live external integrations (when vendor credentials/hardware are available)
3. Operational docs, backups, monitoring
