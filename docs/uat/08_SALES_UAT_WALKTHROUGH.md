# 08 — Guided Sales UAT Walkthrough

**Sales Order → Select Dyed Rolls → Partial Dispatch → GST Invoice → Print Both →
Reprint → Verify Stock, COGS, Revenue, GST & Reconciliation.**

Sample data only. **Documentation only — no application code or historical record
was changed.** Every number below was produced by running this exact flow against
a freshly seeded sample company and reading the posted ledger back; they are
verified, not illustrative.

- **Company:** `DEMO` — Demo Textile Mills Private Limited, GSTIN `03ABCDE1234F1Z5`
  (state 03, Punjab).
- **Customer:** Fashion House, GSTIN `03YYYYY0000Y1Z5` (state 03) → **intra-state**
  supply (CGST + SGST).
- **Fabric:** Single Jersey 160 GSM, Navy Blue; tax code `GST18` (9% + 9%).
- **Prereq produced earlier in the cycle:** one dyeing job with **two dyed finished
  rolls** — lot **A = 60 kg** (cost ₹17,460) and lot **B = 40 kg** (cost ₹11,640);
  total finished-goods value **₹29,100** (GL 1085). Grey/dyeing already valued and
  reconciled.

GL accounts used: 1060 Yarn · 1070 Greige · 1085 Finished Fabric · 5150 COGS ·
4010 Fabric Sales · 1040 Accounts Receivable · 2041 Output CGST · 2042 Output SGST.

---

## Step 0 — Baseline

- **Action:** `GET /api/reports/stock-reconciliation`, `GET /api/reports/trial-balance`.
- **Expected:** `is_reconciled = true`, trial balance balanced. Finished-goods (1085)
  = **₹29,100** (lots A + B). **Verified: PASS.**

## Step 1 — Create the Sales Order

- **UI:** Sales Orders → New (customer Fashion House; line: Single Jersey 160, Navy,
  **100 kg @ ₹400**). **API:** `POST /api/sales/orders` → confirm.
- **Service:** `order_lifecycle.create_sales_order`.
- **Expected:** SO `SO-2627-0007` created for 100 kg. **No accounting/stock change**
  (an order is a commitment, not a transaction). **Verified: PASS.**

## Step 2 — Select dyed rolls for dispatch

- **Action:** choose the finished rolls to dispatch. For a **partial** dispatch pick
  **lot A only** (the 60 kg roll). The other roll (lot B, 40 kg) stays in stock.
- **Traceability:** each dyed roll traces roll → dyeing output (dye lot, shade) →
  dyeing job → grey production order → yarn lot.

## Step 3 — Partial dispatch (Delivery Challan + COGS)

- **UI:** Sales Orders → Dispatch (select roll A). **API:** `POST /api/sales/deliveries`.
  **Service:** `order_lifecycle.create_delivery`.
- **Expected quantities:** delivery note `DN-2627-0009`, **60 kg** dispatched (lot A
  only — **not** the SO's 100 kg).
- **Expected accounting (COGS voucher, type SJ):**

  | Dr / Cr | Account | Amount |
  |---|---|---|
  | Dr | 5150 Cost of Goods Sold | ₹17,460 |
  | Cr | 1085 Finished Fabric Inventory | ₹17,460 |

- **Expected inventory:** 1085 falls ₹29,100 → **₹11,640** (lot B remains). Lot A roll
  marked dispatched (cannot be dispatched again).
- **Verified:** COGS Δ = **+₹17,460**, FG Δ = **−₹17,460**, DN qty = **60.000 kg**,
  FG remaining **₹11,640**. **PASS.**

## Step 4 — GST Sales Invoice

- **UI:** Sales Invoice → New (customer, line: 60 kg @ ₹400, tax `GST18`), post.
  **Service:** `sales.post_sales_invoice`.
- **Expected amounts:** taxable **₹24,000** (60 × 400); intra-state → **CGST ₹2,160 +
  SGST ₹2,160**; **grand total ₹28,320**. Amount in words: *Twenty-eight thousand
  three hundred twenty rupees only*.
- **Expected accounting (invoice voucher):**

  | Dr / Cr | Account | Amount |
  |---|---|---|
  | Dr | 1040 Accounts Receivable | ₹28,320 |
  | Cr | 4010 Fabric Sales (revenue) | ₹24,000 |
  | Cr | 2041 Output CGST | ₹2,160 |
  | Cr | 2042 Output SGST | ₹2,160 |

- **Note:** the invoice is a **separate document** from the delivery challan. Revenue
  is recognised here; COGS was recognised at dispatch (Step 3). No stock moves on
  invoicing.
- **Verified:** taxable ₹24,000, tax ₹4,320, total ₹28,320; revenue 4010 = ₹24,000 Cr;
  receivable 1040 = ₹28,320 Dr; CGST/SGST ₹2,160 each. **PASS.**

## Step 5 — Print both documents

- **API:** `GET /api/print/sales-invoice/{invoice_id}` (inline = browser preview/print,
  `?download=true` = PDF file) and `GET /api/print/delivery-challan/{delivery_id}`.
  Each also has a `/preview` route (no number/print-history touched).
- **Expected:** two professional A4 PDFs (see screenshots). The invoice shows the
  branding letterhead, customer, SO ref, HSN/UOM, CGST+SGST split, grand total,
  amount-in-words, bank + signatures. The challan shows roll-wise detail (roll no,
  fabric, GSM, shade, dye lot, warehouse) and the **actual 60 kg** dispatched.
- **Control:** printing writes **no** voucher / invoice / stock movement.

## Step 6 — Reprint both

- **API:** call the same print endpoints again.
- **Expected:** the **same document numbers** are reused; the footer shows
  **"REPRINT #1"**; the print registry records the reprint. **No posted record
  changes.**
- **Verified:** invoice reprint_count = 1 (number `INV-2627-0021` unchanged); challan
  reprint_count = 1 (number `DN-2627-0009` unchanged). **Across print + 3 reprints:
  vouchers 7 → 7, inventory transactions 11 → 11, invoices 1 → 1 — all unchanged.
  PASS.**

## Step 7 — Verify stock, COGS, revenue, GST & reconciliation

Final ledger (after the full flow):

| GL | Account | Balance | Meaning |
|---|---|---|---|
| 1060 | Yarn Inventory | ₹4,56,000 | opening 4,80,000 − 24,000 issued |
| 1070 | Greige Inventory | ₹0 | consumed into dyeing |
| 1085 | Finished Fabric | **₹11,640** | lot B still in stock (lot A dispatched) |
| 5150 | COGS | **₹17,460** | lot A cost expensed on dispatch |
| 4010 | Fabric Sales | **₹24,000** (Cr) | revenue on the 60 kg invoiced |
| 1040 | Accounts Receivable | **₹28,320** (Dr) | invoice incl. GST |
| 2041 / 2042 | Output CGST / SGST | ₹2,160 / ₹2,160 (Cr) | GST payable |

**Stock vs GL reconciliation** (`GET /api/reports/stock-reconciliation`):

| GL | Ledger | Sub-ledger | Difference |
|---|---|---|---|
| 1060 Yarn | ₹4,56,000 | ₹4,56,000 | ₹0.00 |
| 1070 Greige | ₹0 | ₹0 | ₹0.00 |
| 1085 Finished | ₹11,640 | ₹11,640 | ₹0.00 |

`is_reconciled = true`; **trial balance balanced**. **Verified: PASS.**

### Consistency checks
- **No duplicate COGS:** COGS = ₹17,460 (lot A once), not ₹29,100. **PASS.**
- **No duplicate revenue/GST:** one invoice voucher; reprints added nothing. **PASS.**
- **Partial dispatch honoured:** challan + invoice both reflect 60 kg, not the SO's
  100 kg; 40 kg (lot B, ₹11,640) remains sellable. **PASS.**
- **Separation of documents:** challan (COGS/stock at dispatch) and invoice
  (revenue/GST) are distinct, as in the posted workflow. **PASS.**

---

## Final Pass/Fail Report

| # | Checkpoint | Expected | Result |
|---|---|---|---|
| 0 | Baseline reconciled + balanced | yes / FG ₹29,100 | **PASS** |
| 1 | Sales order, no posting | SO created, GL unchanged | **PASS** |
| 2 | Dyed-roll selection + traceability | roll→dye lot→job→grey→yarn | **PASS** |
| 3 | Partial dispatch qty | 60 kg (not 100) | **PASS** |
| 3 | Dispatch COGS entry | Dr 5150 / Cr 1085 ₹17,460 | **PASS** |
| 3 | Finished stock after dispatch | ₹11,640 (lot B) | **PASS** |
| 4 | Invoice amounts | taxable 24,000 / CGST+SGST 2,160+2,160 / total 28,320 | **PASS** |
| 4 | Invoice accounting | Dr 1040 / Cr 4010 + 2041 + 2042 | **PASS** |
| 4 | Intra-state GST split | CGST+SGST (not IGST) | **PASS** |
| 5 | Print both (PDF/preview/print) | two A4 PDFs, branding + detail | **PASS** |
| 6 | Reprint reuses number, posts nothing | same no., REPRINT #1, 0 new records | **PASS** |
| 7 | Revenue / COGS / GST balances | as table above | **PASS** |
| 7 | Stock-vs-GL reconciliation | all diffs ₹0, is_reconciled true | **PASS** |
| 7 | Trial balance | balanced | **PASS** |
| — | No duplicate revenue/GST/COGS/stock | confirmed | **PASS** |

**Overall: PASS.** The ERP completes the full customer-facing sales cycle on sample
data — dyed rolls dispatched (partial), COGS and revenue recognised correctly, GST
split correctly, both documents printed and reprinted without altering any posted
record, and stock ties to the general ledger to the paisa.

> Reminder (unchanged from the release audit): **GST statutory correctness
> (MEDIUM-4)** — GSTR-1/3B, HSN and place-of-supply for the real GSTINs — still needs
> a practising Indian CA / GST professional sign-off before any live filing. This
> walkthrough verifies the mechanics, not the statutory filing.
