# 03 — Multi-Company & Factory Isolation UAT

This proves the **database-per-legal-company** architecture and **factory-level
authorization** behave correctly: two legal companies, multiple factories each,
correct isolation, correct access, no unauthorized cross-company or cross-factory
visibility, and consolidated reporting where supported.

> Prerequisite: `01_ENVIRONMENT_SETUP.md` completed — companies A (`DEMO`) and B
> (`UATB`) exist, each with factories A-F1/A-F2 and B-F1/B-F2, users `acct_a`,
> `fm_a1` (A-F1 only), `acct_b`, `group_admin`, and sample data in both.

There is also **automated** evidence for most of this
(`backend/tests/test_uat_multicompany.py`, and the true two-database test
`test_uat_two_databases.py`). The steps below are the **manual** confirmation with
your own eyes and logins.

---

## M1. Two separate legal companies
- **Do:** Log in as `acct_a` (acts in A) and, separately, `acct_b` (acts in B).
  Open each company's Trial Balance and chart of accounts.
- **Verify:** Each company has its **own** chart of accounts, its own periods, its
  own GSTIN/state (A = `03` Punjab, B = `27` Maharashtra), and its own opening
  stock. Figures differ and never bleed across.
- [ ] Pass  [ ] Fail (defect #: ____)

## M2. Multiple factories in each company
- **Do:** As `acct_a` (or admin with `X-Company-Id` = A), list factories;
  repeat for B.
- **Verify:** Company A shows **A-F1, A-F2** with their warehouses; company B shows
  **B-F1, B-F2**. A's factories never appear under B and vice-versa.
- [ ] Pass  [ ] Fail (defect #: ____)

## M3. Correct database isolation
- **Do:** In **Option A** (dedicated DB for B), create a customer in company A,
  then look in company B.
  ```bash
  # As admin: create a customer in A, then confirm it is NOT visible in B
  curl -s -X POST http://localhost:8000/api/masters/customers -H "Authorization: Bearer $TOKEN" \
    -H "X-Company-Id: <A_ID>" -H 'Content-Type: application/json' \
    -d '{"code":"ISOCHK","name":"Isolation Check","state_code":"03"}'
  curl -s "http://localhost:8000/api/masters/customers" -H "Authorization: Bearer $TOKEN" -H "X-Company-Id: <A_ID>" | grep ISOCHK   # present
  curl -s "http://localhost:8000/api/masters/customers" -H "Authorization: Bearer $TOKEN" -H "X-Company-Id: <B_ID>" | grep ISOCHK   # ABSENT
  ```
- **Verify:** `ISOCHK` exists in A, is **absent** in B. (Automated equivalent:
  `test_uat_two_databases.py` asserts `CUST-A` is in A's DB and not in B's, on two
  physically separate PostgreSQL databases.)
- [ ] Pass  [ ] Fail (defect #: ____)

## M4. Correct factory access
- **Do:** Log in as `fm_a1` (granted **A-F1 only**). Select factory A-F1
  (`X-Factory-Id = A-F1`); then try to select A-F2 (`X-Factory-Id = A-F2`).
- **Verify:**
  - A-F1 is accepted; `GET /api/me/context` shows factory = A-F1.
  - A-F2 is **refused with 403** ("explicit factory membership or
    `factory.access_all` required").
  - A nonexistent / wrong-company factory id returns **404** (existence not leaked).
- [ ] Pass  [ ] Fail (defect #: ____)

## M5. No unauthorized cross-company visibility
- **Do:** As `acct_a`, attempt to act in company B by sending `X-Company-Id = B_ID`.
- **Verify:** **403** ("Not a member of the requested company"). `acct_a` can never
  read or post into B's ledger. Symmetrically, `acct_b` cannot touch A.
- [ ] Pass  [ ] Fail (defect #: ____)

## M6. Same-company factory transfer is GL-neutral
- **Do:** In company A, transfer stock from A-F1's warehouse to A-F2's warehouse
  (e.g. 300 of 1000 kg).
- **Where:** UI **Transfers → Factory**; `POST /api/transfers/factory` → authorize
  → dispatch → receive.
- **Verify:** Source warehouse 1000 → **700**; destination 0 → **300**; **no
  accounting voucher** is posted (stock moves at drawn cost, GL-neutral). Trial
  balance unchanged.
- [ ] Pass  [ ] Fail (defect #: ____)

## M7. Consolidated reporting (group level)
- **Do:** Log in as `group_admin`. Open the consolidated report for the group
  `UATG`.
- **Where:** `GET /api/reports/consolidated`, `GET /api/reports/consolidated/pnl`.
- **Verify:**
  - The consolidated P&L includes **both** A and B.
  - Inter-company items (if you post an inter-company charge A→B) **net out** at
    group level once both legs are approved and the matching eliminations are
    approved — each company's **own statutory books stay independent** (an
    elimination is never posted to any company's ledger).
- [ ] Pass  [ ] Fail (defect #: ____)

## M8. (Optional) Inter-company charge, both legs balanced
- **Do:** With approved reciprocal inter-company mappings in place (set in
  `01` / admin), post an inter-company charge A→B (e.g. ₹5,00,000).
- **Where:** `POST /api/accounting/intercompany-posting` (single DB) or the saga
  path across two databases.
- **Verify:**
  - A books income + a **due-from** asset; B books expense + a **due-to**
    liability; each company's trial balance **balances independently**.
  - The inter-company reconciliation lists both legs as **ready for elimination**.
  - Consolidated P&L nets the charge to **0** at group level after eliminations.
- [ ] Pass  [ ] Fail (defect #: ____)

---

## Automated evidence (for reference)

| Check | Automated test |
|---|---|
| Company isolation (A stock invisible to B) | `test_uat_multicompany.py` |
| Factory transfer GL-neutral | `test_uat_multicompany.py` |
| Inter-company both legs balanced | `test_uat_multicompany.py` |
| Independent trial balances | `test_uat_multicompany.py` |
| Consolidated P&L nets inter-company out | `test_uat_multicompany.py` |
| **True two physical databases** (isolation, switching, saga, consolidation, concurrency) | `test_uat_two_databases.py` (gated on `PG_CTL_URL`/`PG_A_URL`/`PG_B_URL`) |
| Forged `X-Company-Id` → 403; factory scoping; cross-company guard | `test_company_switching.py`, `test_tenancy_api.py` |

Run the first set with:
```bash
cd backend && . .venv/bin/activate && pytest tests/test_uat_multicompany.py
```
