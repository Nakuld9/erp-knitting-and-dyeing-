# 06 — Final UAT Sign-Off Report

Fill this in at the **end** of UAT. It records what was tested, with which data,
by whom, the defect position, and the explicit human acceptances. UAT is **not
complete** until the business-owner row is signed. This report certifies
**business mechanics**, not statutory GST filing or production readiness (those
are separate rows and separate reviews).

---

## 1. Identification

| Field | Value |
|---|---|
| Product | Integrated Knitting & Dyeing Textile ERP |
| Branch / build | `claude/keen-heisenberg-o93aep` / commit ________ |
| Environment | Development / UAT (not production) |
| UAT window | ____-__-__ to ____-__-__ |
| Data set | Named UAT sample data (see `04_EXPECTED_VALUES.md`) |
| Companies | A = `DEMO`, B = `UATB`; Group = `UATG` |

## 2. Participants

| Role | Name | Organization |
|---|---|---|
| Business owner | | |
| Company accountant (A) | | |
| Company accountant (B) | | |
| Production / factory staff | | |
| Group administrator | | |
| CA / GST professional (statutory review) | | |

## 3. Scope executed

| Area | Checklist | Executed? | Result (Pass/Fail) |
|---|---|---|---|
| Full textile cycle (company A) | `02` A–F | | |
| Full textile cycle (company B) | `02` G | | |
| Production cancellation & returns | `02` E2 | | |
| Rejection / wastage / reprocessing | `02` E3 | | |
| Financial statements | `02` F1 | | |
| GST reports (mechanics) | `02` F2 | | |
| Multi-company isolation | `03` M1–M5 | | |
| Factory access control | `03` M4 | | |
| Factory transfer (GL-neutral) | `03` M6 | | |
| Consolidated reporting | `03` M7–M8 | | |

## 4. Reconciliation attestation

At the end of UAT, with all sample transactions posted:

| Company | Trial balance balanced? | Stock vs GL reconciled (all diffs ₹0)? | Reviewed by |
|---|---|---|---|
| A (`DEMO`) | ☐ Yes ☐ No | ☐ Yes ☐ No | |
| B (`UATB`) | ☐ Yes ☐ No | ☐ Yes ☐ No | |

Attach the trial-balance and stock-reconciliation exports for each company.

## 5. Defect position

| Severity | Open | Fixed & retested | Deferred (owner-accepted) |
|---|---|---|---|
| S1 Critical | | | |
| S2 Major | | | |
| S3 Minor | | | |
| S4 Cosmetic/Note | | | |

- **Any open S1 → UAT FAIL.** No exceptions.
- Open S2 items and their business justification: _______________________________

## 6. Known limitations carried forward (not resolved by UAT)

These are acknowledged as **out of UAT scope** and remain open before any
production decision:

- [ ] **GST statutory review (MEDIUM-4)** — GSTR-1/3B, HSN, place-of-supply,
      capitalise-vs-expense, job-work GST signed off by a practising Indian
      CA/GST professional. (UAT verified mechanics only.)
- [ ] **Abnormal-loss / rejection accounting policy (MEDIUM-5)** — formal policy
      pending; losses currently absorbed into saleable cost.
- [ ] **Provisioning / period-close hardening (MEDIUM-6)**.
- [ ] **Backup & restore drill** performed and verified (`scripts/backup.sh` /
      `restore.sh`; see `07`).
- [ ] **Security review** completed.
- [ ] **Load / performance test** completed.
- [ ] Chemical BOM automation **remains disabled** (by decision).

## 7. Acceptance

| Acceptance | Name | Signature | Date | Verdict |
|---|---|---|---|---|
| Business mechanics (cycle A+B reconcile to expected figures) | | | | ☐ Accept ☐ Reject |
| Multi-company & factory isolation correct | | | | ☐ Accept ☐ Reject |
| GST statutory review (CA/GST professional) | | | | ☐ Accept ☐ Reject ☐ Pending |
| **Business-owner acceptance of controlled UAT** | | | | ☐ Accept ☐ Reject |

**Overall UAT status:** ☐ PASS ☐ PASS WITH NOTED LIMITATIONS ☐ FAIL

> Until the business-owner row is signed **Accept**, status remains
> **UAT IN PROGRESS**. A PASS here authorizes continued controlled UAT/staging
> use only — **not** production go-live, which additionally requires the
> MEDIUM-4 CA review and the standing gates in §6 to be cleared.

## 8. Notes / conditions attached to this sign-off

_____________________________________________________________________________
_____________________________________________________________________________
