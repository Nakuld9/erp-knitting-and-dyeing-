# 05 — Defect Recording Template

Copy one block per defect into a shared tracker (or a copy of this file named
`UAT_DEFECTS_<date>.md`). Keep raw evidence (screenshots, JSON responses, the
voucher number) with each entry. **Do not fix application code during UAT** —
record the defect; fixes are a separate, approved step.

## Severity scale

| Severity | Meaning | Example |
|---|---|---|
| **S1 Critical** | Wrong money/stock, imbalance, data leak across companies/factories, data loss | Trial balance doesn't balance; A's data visible to B; COGS double-posted |
| **S2 Major** | Core scenario blocked or wrong, no safe workaround | Cannot dispatch a lot; grey value wrong; cancellation doesn't reverse GL |
| **S3 Minor** | Works but wrong/awkward in a non-financial way | Mislabeled field; report column order; confusing message |
| **S4 Cosmetic / Note** | Typo, wording, deferred-by-design item | GST wording; abnormal-loss policy note (MEDIUM-5) |

---

## Defect block (copy per defect)

```
Defect ID:            UAT-___
Date / Tester:        ____-__-__  /  __________________
Company / Factory:    (A / B)  /  (A-F1 / A-F2 / B-F1 / B-F2 / company-level)
Checklist step:       (e.g. 02 §D2, or 03 §M4)
Severity:             S1 / S2 / S3 / S4

Summary (one line):   _______________________________________________

Steps to reproduce:
  1.
  2.
  3.

Expected result:      (quote the figure/behaviour from 04_EXPECTED_VALUES.md)
Actual result:        (what happened; include the number you saw)

Evidence:             (screenshot file / API response / voucher no. / report export)
Reconciliation state: trial balance balanced? (Y/N)   stock reconciled? (Y/N)
Scope leak?           (did any data cross company/factory boundaries? Y/N — if Y, mark S1)

Workaround (if any):  _______________________________________________

--- for the delivery team (do not fill during UAT) ---
Status:               Open / Triaged / Fixed / Won't-fix / Deferred
Root cause:
Resolution / commit:
Retest result:        Pass / Fail  (date / tester)
```

---

## Defect log (index)

| ID | Sev | Company/Factory | Step | Summary | Status | Retest |
|---|---|---|---|---|---|---|
| UAT-001 | | | | | | |
| UAT-002 | | | | | | |
| UAT-003 | | | | | | |

> Any **S1** defect blocks sign-off outright. Count open S2 defects; the business
> owner decides in `06` whether any S2 is acceptable for a controlled go/no-go.
