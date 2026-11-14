# 04 — Sample Data & Expected Values

The numbers your UAT results must match. They are computed from the **named UAT
sample data** loaded in `01` step 8, and are confirmed by the automated end-to-end
acceptance test (`backend/tests/test_e2e_grey_to_dyed_sales.py`). All money is in
Indian rupees (₹), Decimal (no floating point).

> **Deltas, not absolutes.** The sample set seeds **opening stock** (yarn 2000 kg
> @ ₹240 = ₹4,80,000 in GL 1060, plus chemicals/dyes). The tables below show the
> **change produced by each step**; add them to your opening baseline when reading
> absolute GL balances. The reconciliation must hold at **every** stage.

---

## 1. Sample company & factory data

| Item | Company A (`DEMO`) | Company B (`UATB`) |
|---|---|---|
| Legal name | Demo Textile Mills Pvt Ltd | UAT Weaves Pvt Ltd |
| GSTIN / state | `03ABCDE1234F1Z5` / 03 Punjab | `27ABCDE9999F1Z2` / 27 Maharashtra |
| Currency / FY start | INR / April | INR / April |
| Factories | A-F1 (Knitting), A-F2 (Dyeing) | B-F1 (Knitting), B-F2 (Dyeing) |
| Warehouses | A-GREIGE, A-CHEM, A-FG | B-GREIGE, B-CHEM, B-FG |
| Group | UATG (consolidation) | UATG |

**Per-company opening stock (sample):**

| Item | Qty | Unit cost | Value | GL account |
|---|---|---|---|---|
| Yarn `Y-30S` 30s combed cotton | 2,000 kg | ₹240 | ₹4,80,000 | 1060 Yarn Inventory |
| Reactive Blue dye | 500 kg | ₹1,200 | ₹6,00,000 | 1090 Dye Inventory |
| Reactive Black dye | 500 kg | ₹1,500 | ₹7,50,000 | 1090 Dye Inventory |
| Common Salt | 500 kg | ₹10 | ₹5,000 | 1100 Chemical Inventory |
| Soda Ash | 500 kg | ₹25 | ₹12,500 | 1100 Chemical Inventory |

Opening is posted as a balanced **OB** voucher (`Dr` each inventory account /
`Cr 3010` owner's capital), so the sub-ledger ties to the GL from the start.

Key GL accounts used in the cycle: **1060** Yarn · **1070** Greige Fabric ·
**1080** Work in Progress · **1085** Finished Fabric · **5150** COGS ·
**2010** Accounts Payable · **2020** Expenses Payable · **4010** Sales.

---

## 2. Reference cycle — expected accounting entries (deltas)

Quantities: purchase 200 kg @ ₹240; issue 100 kg; conversion ₹600; dyeing ₹3,000
+ chemical ₹1,500; lots A 60 kg / B 40 kg; sale 100 kg @ ₹400.

| Step | Posting event | Voucher lines (Dr / Cr) | Amount |
|---|---|---|---|
| A1 | Yarn GRN (type **PI**; supplier → Cr 2010, else Cr 3010) | Dr 1060 Yarn / Cr 2010 Payable | ₹48,000 |
| B2 | Yarn issue to WIP (posted atomically with the FIFO issue) | Dr 1080 WIP / Cr 1060 Yarn | ₹24,000 |
| B3 | **Grey valuation voucher** (`value_grey_output`) — conversion line | Dr 1080 WIP / Cr 2020 Expenses Payable | ₹600 |
| B3 | **Grey valuation voucher** — greige line | Dr 1070 Greige / Cr 1080 WIP | ₹24,600 |
| C1 | Grey issued to dyeing (`issue_grey`) | Dr 1080 WIP / Cr 1070 Greige | ₹24,600 |
| C3 | **Dyeing completion voucher** (`complete_job`) — dyeing+chemical lines | Dr 1080 WIP / Cr 2020 Expenses Payable | ₹4,500 |
| C3 | **Dyeing completion voucher** — finished line | Dr 1085 Finished / Cr 1080 WIP | ₹29,100 |
| D2 | Dispatch lot A — COGS | Dr 5150 COGS / Cr 1085 Finished | ₹17,460 |
| D3 | Dispatch lot B — COGS | Dr 5150 COGS / Cr 1085 Finished | ₹11,640 |
| D4 | Sales invoice (intra-state) | Dr 1030 Receivable / Cr 4010 Sales + Cr output GST | see §5 |

> **Timing note (verified against the code).** `record_actual_knitting_conversion_cost`
> and `record_costs` only *capture* the amounts — they post **no** voucher. The
> conversion line is posted **inside the grey-valuation voucher** at B3; the
> dyeing-charge (₹3,000) and chemical (₹1,500) lines are posted **inside the
> dyeing-completion voucher** at C3 (shown combined as ₹4,500 → Cr 2020). For an
> INTERNAL dyeing job the expense credit is **2020**; a JOBWORK job credits
> **2010** (job-work payable) instead. So there are, for the reference cycle, GL
> postings at A1, B2, B3 (one voucher, two line-pairs), C1, C3 (one voucher,
> two line-pairs), D2, D3 and D4 — not a separate voucher per cost entry.

Every voucher is balanced (Dr = Cr) or nothing commits. Account **names/codes**
follow the seeded chart of accounts; if your COA differs, map by meaning.

---

## 3. Expected inventory balances (on-hand & value) by stage

| After step | Yarn on-hand | GL 1060 Δ | Grey | GL 1070 | WIP GL 1080 | Finished | GL 1085 |
|---|---|---|---|---|---|---|---|
| A1 purchase | +200 kg | +₹48,000 | — | 0 | 0 | — | 0 |
| B2 issue | −100 kg | −₹24,000 | — | 0 | ₹24,000 | — | 0 |
| B3 grey valued | — | — | 100 kg | ₹24,600 | **0** | — | 0 |
| C1 grey issued | — | — | 0 kg | **0** | 24,600 (dyeing WIP) | — | 0 |
| C3 dyed complete | — | — | 0 | 0 | **0** | 100 kg (A+B) | **₹29,100** |
| D2 lot A out | — | — | 0 | 0 | 0 | 40 kg (B) | ₹11,640 |
| D3 lot B out | — | — | 0 | 0 | 0 | 0 kg | **₹0** |

**Lot split:** A = 29,100 × 60/100 = **₹17,460**; B = 29,100 × 40/100 = **₹11,640**.

---

## 4. Expected cost calculations

- **Grey fabric unit cost** = (yarn 24,000 + conversion 600) ÷ 100 kg = **₹246.00 / kg**.
- **Dyed fabric unit cost** = (grey 24,600 + dyeing 3,000 + chemical 1,500) ÷ 100 kg
  = 29,100 ÷ 100 = **₹291.00 / kg** (both lots share the same per-kg actual cost).
- **Total COGS on full sale** = **₹29,100**; **revenue** = 100 × 400 = **₹40,000**;
  **gross profit** = **₹10,900** (before the sale's own GST, which is not revenue).

### Partial-production / rejection variant (checklist E3)
Inputs: 100 kg yarn issued → **92 kg** grey produced; conversion ₹500; dyeing
₹2,800 + chemical ₹1,200 + reprocess ₹400; saleable 85 kg, rejection 3 kg.

| Quantity | Value |
|---|---|
| Grey value (92 kg) | 24,000 + 500 = **₹24,500** |
| Dyed finished (GL 1085) | 24,500 + 2,800 + 1,200 + 400 = **₹28,900** |
| Saleable output | **85 kg** |
| Rejection | **3 kg** |
| Process loss (92 − 85 − 3) | **4 kg** |

(Abnormal-loss accounting policy is **MEDIUM-5**, deferred: the loss is currently
absorbed into saleable cost — expected, not a defect.)

---

## 5. Expected GST (reference sale) — mechanics only, not statutory sign-off

Sale 100 kg @ ₹400 = taxable **₹40,000**, tax code `GST18`, **intra-state** (both
customer and company in state 03):

| Component | Rate | Amount |
|---|---|---|
| Taxable value | — | ₹40,000 |
| CGST | 9% | ₹3,600 |
| SGST | 9% | ₹3,600 |
| Invoice total | — | **₹47,200** |

An **inter-state** sale (e.g. A in 03 selling to a 27-state customer) would instead
show **IGST 18% = ₹7,200**, no CGST/SGST. Verify the register picks the right split
from the customer/company state codes.

> **This is a mechanics check only.** The rate comes from the configurable tax
> code, not hardcoded. Statutory correctness of GSTR-1/3B, HSN, place-of-supply,
> reverse charge and job-work GST for your real GSTINs **must be signed off by a
> practising Indian CA / GST professional** (MEDIUM-4) before any live filing.

---

## 6. Expected reconciliation balances

At **every** stage of the cycle:

- **Trial balance:** total debits = total credits → `is_balanced = true`.
- **Stock vs GL reconciliation** (`GET /api/reports/stock-reconciliation`): for
  each control account the FIFO sub-ledger value **equals** the GL balance, every
  per-account difference = **₹0.00**, `is_reconciled = true`. The split is
  dyeing-aware: grey → **1070**, dyed/finished → **1085**.
- **Production-ledger reconciliation:** `is_reconciled = true` after dyeing
  completes.
- After full dispatch: **1070 = 0, 1080 = 0, 1085 = 0**, and COGS 5150 carries the
  full **₹29,100**.

If any difference is non-zero, record a defect (`05`) and stop — do not sign off.
