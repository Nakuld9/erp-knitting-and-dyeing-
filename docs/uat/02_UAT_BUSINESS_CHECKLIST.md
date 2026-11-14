# 02 — UAT Business-Scenario Checklist

Run this **in company A** first, end to end, then repeat the key parts in company
B. Each step lists **what to do**, **where**, and **what to verify**. Tick the box
only when the verified result matches `04_EXPECTED_VALUES.md` (or your own
independently calculated figure). Record any mismatch with `05_DEFECT_TEMPLATE.md`.

- **Who runs it:** company accountant + production staff together.
- **Scope:** log in as `acct_a`; the UI company/factory switcher sends
  `X-Company-Id` / `X-Factory-Id`. API users: add those headers.
- **Golden rule:** after *every* stage that touches stock or money, open
  **Reports → Trial Balance** (must balance) and **Reports → Stock vs GL
  Reconciliation** (must reconcile). A stage is not "passed" until both hold.

The single reference cycle (quantities from the sample data) is:

> 200 kg yarn purchased @ ₹240 → 100 kg issued to knitting → 100 kg grey fabric
> (+₹600 conversion) → dyeing (+₹3,000 dyeing +₹1,500 chemical) → **2 dyed lots**
> (60 kg + 40 kg) → sales order → dispatch both lots → COGS → statements.

---

## A. Procurement & inventory

### A1. Yarn purchase & GRN
- **Do:** Receive a yarn lot — 200 kg of `Y-30S` @ ₹240/kg from the sample
  supplier, into the greige/yarn store.
- **Where:** UI **Purchase (GRN)**, or `POST /api/...` yarn receipt; service
  `yarn.receive_yarn_lot`.
- **Verify:**
  - Yarn on-hand rises by **200 kg**.
  - GL **1060 Yarn Inventory** rises by **₹48,000** (200 × 240).
  - A purchase/GRN voucher exists and is balanced.
- [ ] Pass  [ ] Fail (defect #: ____)

### A2. FIFO yarn inventory
- **Do:** Inspect the yarn stock layers (opening 2000 kg @ ₹240 + new 200 kg @ ₹240).
- **Where:** UI stock view / `GET /api/inventory/...` balance.
- **Verify:** Layers are ordered oldest-first; valuation = Σ(layer qty × layer
  cost). On the next issue, the **oldest** layer is drawn first (FIFO).
- [ ] Pass  [ ] Fail (defect #: ____)

---

## B. Grey fabric production

### B1. Grey fabric BOM approval
- **Do:** Create a fabric BOM for the grey fabric; set its yarn line (100% `Y-30S`,
  1.0 kg yarn per kg fabric, std cost ₹240); **approve** the version.
- **Where:** UI **BOM** screen; `POST /api/bom/...`; service `bom.create_bom` /
  `set_yarns` / `approve_version`.
- **Verify:** The version moves to **APPROVED**; an unapproved version cannot be
  used to create a production order; yarn %/consumption are stored as entered.
- [ ] Pass  [ ] Fail (defect #: ____)

### B2. Knitting production order + actual costing
- **Do:** Create a knitting production order for **100 kg** against the approved
  BOM. Issue **100 kg** yarn (FIFO). Complete production at **100 kg** produced.
  Record actual knitting conversion cost **₹600**.
- **Where:** UI **Knitting production**; `POST /api/knitting/...`,
  `/api/bom/orders/...`; services `production.create_order` / `issue_material` /
  `complete_order` / `record_actual_knitting_conversion_cost`.
- **Verify:**
  - Yarn on-hand falls by **100 kg**; GL **1060** falls by **₹24,000**.
  - At issue, GL **1080 WIP** rises by **₹24,000** (`Dr WIP / Cr Yarn`, posted
    atomically with the FIFO issue — not deferred).
  - Trial balance balances; stock reconciles.
- [ ] Pass  [ ] Fail (defect #: ____)

### B3. Grey fabric stock valuation
- **Do:** Value the grey output (move WIP → greige inventory).
- **Where:** `production.value_grey_output`.
- **Verify:**
  - Grey fabric on-hand = **100 kg**, value = **₹24,600** (24,000 yarn + 600 conversion).
  - GL **1070 Greige Inventory** = **₹24,600**; GL **1080 WIP** back to **₹0**.
  - Grey voucher is balanced (`Dr Greige / Cr WIP`); it does **not** re-post yarn.
  - Stock reconciliation shows **1070** tying to the grey sub-ledger.
- [ ] Pass  [ ] Fail (defect #: ____)

---

## C. Dyeing

### C1. Dyeing job + grey issue
- **Do:** Create an INTERNAL dyeing job against the knitting order; issue **100 kg**
  grey into it.
- **Where:** UI **Dyeing** flow; `POST /api/dyeing/...`; services
  `dyeing.create_job` / `issue_grey`.
- **Verify:** Grey on-hand falls to 0; GL **1070** falls to **₹0** (greige consumed
  into dyeing WIP).
- [ ] Pass  [ ] Fail (defect #: ____)

### C2. Dyeing costs (actual, chemical BOM OFF)
- **Do:** Record dyeing charge **₹3,000** and chemical cost **₹1,500**.
- **Where:** `dyeing.record_costs`.
- **Verify:** Costs are captured as **actual** amounts (no chemical BOM explosion,
  no chemical reservation). Running dyed cost = 24,600 + 3,000 + 1,500 = **₹29,100**.
- [ ] Pass  [ ] Fail (defect #: ____)

### C3. Multiple dyed output lots
- **Do:** Record **two** output lots: **A = 60 kg**, **B = 40 kg**. Complete the job.
- **Where:** `dyeing.record_output` (lots), `dyeing.complete_job`.
- **Verify:**
  - Each lot becomes a **finished fabric roll** (sellable identity), not a bare
    inventory number.
  - GL **1085 Finished Fabric Inventory** = **₹29,100**; dyeing WIP back to **₹0**.
  - Lot A value = **₹17,460** (29,100 × 60/100); lot B value = **₹11,640**.
  - Production-ledger reconciliation reports `is_reconciled = true`.
- [ ] Pass  [ ] Fail (defect #: ____)

### C4. Finished fabric stock valuation
- **Do:** Inspect finished-goods stock for the two lots.
- **Verify:** Finished-goods on-hand = **100 kg** across lots A+B; total value
  **₹29,100**; each lot traceable back to the dyeing job → grey order → yarn lot.
- [ ] Pass  [ ] Fail (defect #: ____)

---

## D. Sales, dispatch & COGS

### D1. Sales order
- **Do:** Create a sales order for the customer (100 kg @ ₹400/kg).
- **Where:** UI **Sales**; `order_lifecycle.create_sales_order`.
- **Verify:** SO is created/confirmed; credit-limit control behaves as configured.
- [ ] Pass  [ ] Fail (defect #: ____)

### D2. Partial dispatch — lot A — with COGS
- **Do:** Dispatch **lot A** (60 kg) against the SO via a delivery challan.
- **Where:** UI dispatch; `order_lifecycle.create_delivery`.
- **Verify:**
  - Finished-goods lot A on-hand → **0**; GL **1085** falls by **₹17,460**
    (remaining = ₹11,640 = lot B).
  - COGS voucher posts `Dr 5150 COGS 17,460 / Cr 1085 17,460`.
  - SO rolls to **PARTIAL**. Lot A **cannot be dispatched again** (duplicate
    dispatch rejected).
- [ ] Pass  [ ] Fail (defect #: ____)

### D3. Partial dispatch — lot B — COGS completes
- **Do:** Dispatch **lot B** (40 kg).
- **Verify:**
  - GL **1085** → **₹0** (all finished stock sold); total COGS = **₹29,100**.
  - SO rolls to **COMPLETED**.
  - Trial balance balances; stock reconciles.
- [ ] Pass  [ ] Fail (defect #: ____)

### D4. Sales invoice & GST
- **Do:** Raise the GST sales invoice for the dispatch (intra-state → CGST+SGST).
- **Where:** UI **Sales invoice**; sales posting.
- **Verify:** Receivable and output GST posted; invoice appears in the GST sales
  register. (Statutory GST correctness is a **separate CA review** — see `00`.)
- [ ] Pass  [ ] Fail (defect #: ____)

---

## E. Reconciliation & controls

### E1. Stock-to-GL reconciliation (whole company)
- **Do:** Open **Reports → Stock vs GL Reconciliation**.
- **Verify:** Each control account (1060 yarn, 1070 greige, 1085 finished, 1090
  dye, 1100 chemical) — the FIFO sub-ledger value equals the GL balance;
  `is_reconciled = true`, differences all **₹0**.
- [ ] Pass  [ ] Fail (defect #: ____)

### E2. Production cancellation & material returns
Use a **fresh** production order for this (do not cancel the one already dyed).
- **Do (cancel before issue):** Create order, cancel immediately.
  - **Verify:** reservations RELEASED; no issue/return vouchers; stock unchanged.
- **Do (cancel after full issue):** Create order, issue 100 kg yarn, then cancel.
  - **Verify:** yarn stock & GL **fully restored**; a **PRODUCTION_RETURN**
    inventory movement exists; the issue voucher is **REVERSED** with a balanced
    reversal voucher (audit trail preserved — nothing deleted); reservations
    RELEASED with issued qty 0; trial balance balances; stock reconciles.
- **Do (guard):** Try to cancel an order whose grey is already valued → **rejected**.
- **Do (duplicate):** Cancel an already-cancelled order → **rejected** (no double
  reversal).
- [ ] Pass  [ ] Fail (defect #: ____)

### E3. Rejection, wastage & reprocessing
Use a dyeing job on a fresh batch.
- **Do:** Produce 92 kg grey; dye it; record saleable **85 kg**, **rejection 3 kg**,
  reprocess 5 kg; record reprocess cost ₹400.
- **Verify:**
  - `process_loss = 4 kg` (92 − 85 − 3); rejection = 3 kg.
  - Finished value includes grey + dyeing + chemical + **reprocess** cost.
  - Stock reconciles; the loss is absorbed into saleable cost (policy note:
    formal abnormal-loss accounting is **MEDIUM-5**, deferred — flag, don't fail).
- [ ] Pass  [ ] Fail (defect #: ____)

---

## F. Financial statements & GST reports

### F1. Financial statements
- **Do:** Generate and read each statement for company A.
- **Where / endpoints:**
  - Trial Balance — `GET /api/reports/trial-balance`
  - Profit & Loss — `GET /api/reports/profit-and-loss`
  - Balance Sheet — `GET /api/reports/balance-sheet`
  - Cash Flow — `GET /api/reports/cash-flow`
  - Manufacturing / Trading account — `GET /api/reports/manufacturing-account`, `/trading-account`
  - Debtors / Creditors ageing — `GET /api/reports/debtors-ageing`, `/creditors-ageing`
- **Verify:** Trial balance Dr = Cr. Balance Sheet balances. P&L reflects the sale
  (revenue) and COGS (₹29,100 for the reference cycle). Figures reconcile to your
  manual calculation in `04`.
- [ ] Pass  [ ] Fail (defect #: ____)

### F2. GST reports
- **Do:** Generate the GST outputs for the period.
- **Where / endpoints:** `GET /api/reports/gst/sales-register`,
  `/gst/purchase-register`, `/gst/gstr1`, `/gst/gstr3b`, `/gst/reconciliation`.
- **Verify:** The reference sale appears with the right taxable value and tax
  split; books-vs-registers reconciliation shows no unexplained difference.
  **Do not treat this as statutory sign-off** — a practising CA/GST professional
  must review GSTR-1/3B against your real GSTINs before any filing (MEDIUM-4).
- [ ] Pass  [ ] Fail (defect #: ____)

### F3. Report exports
- **Do:** Download Excel (.xlsx) and PDF for trial balance, P&L, balance sheet and
  the GST registers.
- **Where:** `GET /api/reports/export/{report}` (authenticated) or the UI download
  buttons.
- **Verify:** Files open; figures match the on-screen reports.
- [ ] Pass  [ ] Fail (defect #: ____)

---

## G. Repeat in company B

- [ ] Run **A1 → F2** again logged in as `acct_b` (company B), confirming company B
      produces its own correct, independent books. Then proceed to
      `03_MULTICOMPANY_FACTORY_UAT.md`.

---

### Scenario coverage map (requested → section)

| Requested scenario | Section |
|---|---|
| Yarn purchase and GRN | A1 |
| FIFO yarn inventory | A2 |
| Grey fabric BOM approval | B1 |
| Knitting production and actual costing | B2 |
| Grey fabric stock valuation | B3 |
| Dyeing job and multiple dyed output lots | C1–C3 |
| Finished fabric stock valuation | C4 |
| Sales order, partial dispatch and COGS | D1–D3 |
| Stock-to-GL reconciliation | B3, C3, E1 (and after every stage) |
| Production cancellation and material returns | E2 |
| Rejection, wastage and reprocessing | E3 |
| Financial statements and GST reports | F1–F2 |
