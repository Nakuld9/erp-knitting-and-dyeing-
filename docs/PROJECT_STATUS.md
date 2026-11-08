# Project status & pending list

Audit of the implementation against the master specification (the 24-section
brief). Updated **2026-11-08**. Test suite: **138 passing**
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
| GST | CGST/SGST/IGST, configurable rates, purchase/sales registers, GSTR-1, GSTR-3B, book-vs-register reconciliation |
| Banking | receipts/payments/contra, CSV statement import, reconciliation |
| Procurement | chemical/dye GRN → FIFO stock + GST purchase voucher |
| **PO / SO lifecycle** | PO place/confirm → GRN roll-up (PARTIAL/COMPLETED); SO place/confirm → delivery challan → **COGS**; invoice |
| **Credit / debit notes** | sales credit note (receivable + output GST reversal); purchase debit note (goods return, FIFO relief + ITC reversal); both in GST registers |
| Knitting | machine/shift/operator/yarn-lot, greige rolls, WIP/greige postings |
| Greige inventory | per-roll ID, barcode/QR, auto-numbering, lineage, labels |
| **Finishing → FG transfer** | greige→finished-goods GL transfer on finishing (keeps sub-ledger reconciled) |
| Yarn lot tracking | priced lots, FIFO per lot, traceability |
| Batching + dyeing | dye batches/baths, roll traceability, approved-recipe scaling |
| Color Service | dose-order builder, **simulator** adapter, bath-wise consumption register, correction dosing, idempotent ingest |
| Chemical inventory | FIFO perpetual, value-based layers, GL reconciliation |
| Costing | per-kg knit/dye/finish, shade/machine/order, std-vs-actual, customer profitability |
| **Fixed assets** | register, SLM/WDV depreciation, disposal (gain/loss) |
| **Payroll** | salary structures, PF/ESI/PT/TDS, payslips, balanced monthly PRV run |
| **Approvals** | configurable maker-checker rules; supplier-payment approval gate (self-approval blocked, N approvals, posts on approval) |
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

---

## ❌ Pending

### Accounting
1. **GST Adjustment Voucher** workflow (ITC reversals, RCM entries)
2. **Funds Flow**, **Trading Account**, **Manufacturing Account** (3 of the 10 statements)
3. **Year-end closing** → retained-earnings roll-forward routine

### Workflow / controls
4. **Approval coverage beyond payments** — the maker-checker engine is built and
   gates supplier payments; extend the same rules to vouchers, POs and journals.
5. **Notifications** & **attachments** (document uploads)

### Sales / commercial
6. **Enquiries & Quotations** (pre-sales pipeline)
7. **Credit-limit enforcement** on sales orders
8. **Customer / supplier statements** (formatted; ageing already exists)

### Manufacturing / inventory
9. **Roll split / merge** operations (`parent_roll_id` field exists; operations don't)
10. **FEFO & weighted-average** valuation + **chemical expiry** tracking (only FIFO today)
11. **Stock transfers** between warehouses + **physical-verification** adjustment workflow
12. **Sales-return restock** (sales credit note is financial-only today)

### Integration (needs vendor access)
13. **Live Color Service adapter** (SOAP / OPC-UA / Modbus / DB) — only the simulator exists
14. **E-invoice / e-way bill** — marked "readiness", not implemented
15. **Bank-feed** integration (CSV import done; live feeds not)

### Deliverables / ops
16. Admin & user manuals beyond `docs/USER_GUIDE.md`; installation / backup guides (API docs = FastAPI `/docs`)
17. Automated backups + error-logging / monitoring infrastructure
18. Frontend is the self-contained SPA, **not** the Next.js/TS/Tailwind/shadcn stack in the spec (functionally complete, different stack)

---

## Acceptance tests (brief §23)

18–19 of the 20 end-to-end tests are covered. Remaining weak spots:
- **Tests 5 / 6 / 18** run against the Color Service **simulator**, not verified vendor hardware.
- All ledger, inventory, GST, costing and traceability tests (1–4, 7–17, 19–20) pass, including **Test 12** (WIP→FG) now that finishing posts the greige→FG transfer.

## Suggested priority order for remaining work
1. GST adjustment voucher + year-end close
2. Roll split/merge, stock transfers, FEFO/weighted-average
3. Enquiries/quotations + credit limits + party statements
4. Extend approvals to vouchers/POs/journals + notifications/attachments
5. Live external integrations (when vendor credentials/hardware are available)
6. Operational docs, backups, monitoring
