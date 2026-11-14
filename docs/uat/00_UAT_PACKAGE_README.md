# Controlled Business UAT Package

**Project:** Integrated Knitting & Dyeing Textile ERP
**Branch:** `claude/keen-heisenberg-o93aep` (development/UAT)
**Phase:** Controlled Business User Acceptance Testing (UAT) preparation
**Status:** Documentation only — no application code changed, not merged, not deployed.

---

## Purpose

This package lets you validate the ERP with **real textile business scenarios and
real people** (your accountant and production staff) before any production
decision. It is deliberately conservative: the goal is to prove that **one
complete business cycle** —

> Yarn purchase → Knitting → Grey fabric → Dyeing → Finished fabric → Sales →
> COGS → Financial statements

— produces the **correct physical quantities, inventory values and accounting
entries**, and that two legal companies with multiple factories stay correctly
isolated.

## What is in this package

| File | What it gives you |
|---|---|
| `01_ENVIRONMENT_SETUP.md` | Exact steps: PostgreSQL, central platform DB, two legal-company DBs, factories & warehouses, users/memberships/permissions/factory access, backend + frontend start, sample-data loading |
| `02_UAT_BUSINESS_CHECKLIST.md` | Step-by-step business-scenario checklist (full textile cycle) with pass/fail boxes |
| `03_MULTICOMPANY_FACTORY_UAT.md` | Two-company / multi-factory isolation and consolidation checklist |
| `04_EXPECTED_VALUES.md` | Sample company & factory data + expected accounting entries, inventory balances, cost calculations and reconciliation balances |
| `05_DEFECT_TEMPLATE.md` | Defect recording template (copy one block per defect) |
| `06_UAT_SIGNOFF_REPORT.md` | Final UAT sign-off report (business owner, accountant, GST professional) |
| `07_WINDOWS_INSTALL_GUIDE.md` | Beginner-friendly Windows PC install guide for development/UAT |

Read them in order. `01` builds the environment; `02`+`03` are the test scripts you
run; `04` holds the numbers you check results against; `05`/`06` record outcomes;
`07` is the standalone Windows setup for a non-technical operator.

## Hard restrictions this package respects

- **No application code changed.** This is documentation only.
- **Not merged into `main`. Not deployed to production. No live data migrated.**
- **Chemical BOM automation stays disabled** — dyeing is costed at *actual*
  (dyeing charge + chemical cost + reprocess), exactly as the accepted baseline.
- **No historical records rewritten.**
- Any implementation change needs **separate approval**.

## A note on scope honesty (read before sign-off)

Two things are explicitly **out of UAT sign-off** and must be cleared separately
before production, as recorded in the release audit:

1. **GST statutory correctness (MEDIUM-4)** — intra/inter-state treatment, HSN,
   capitalise-vs-expense, job-work GST. The ERP *produces* GST registers and
   GSTR-1/3B summaries, but their statutory correctness for your GSTINs must be
   signed off by a **practising Indian CA / GST professional**. UAT verifies the
   mechanics; it does **not** certify the filing.
2. **Standing production gates** — backup/restore drill, security review, load
   test. These remain open regardless of UAT.

UAT can pass on mechanics and still leave these open. The sign-off report
(`06`) keeps them as explicit separate rows so nothing is quietly assumed done.
