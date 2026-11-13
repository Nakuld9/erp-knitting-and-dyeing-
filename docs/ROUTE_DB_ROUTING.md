# Route-by-Route Database Routing (database-per-company cutover)

**Date:** 2026-11-12 · **Branch:** `feat/multi-company-production-ready`

The live cutover from a single database to **central platform DB + one operational
database per company**. This report classifies every API route module as **CENTRAL**
(platform/identity) or **COMPANY** (routes to the acting company's database), and
records the change made.

- **CENTRAL** routes use `Depends(get_db)` → the central platform database. These
  hold global identity/catalog: users, roles, permissions, memberships,
  organizations, the company catalog, the `company_databases` registry, and
  group-level config (GST registrations, inter-company mappings, consolidation
  eliminations).
- **COMPANY** routes use `Depends(get_company_db)` → a session bound to the acting
  company's database (falls back to the central DB when that company has no
  registered dedicated database, preserving single-DB installs and the test suite).
- **CROSS-DB** routes (consolidation, inter-company posting) read/write **several**
  company databases and are handled by dedicated services, not a single session
  (see §3–§4 of this doc and `DATABASE_PER_COMPANY.md`).

---

## 1. Classification & action

| Module | Class | Routed to | Notes |
|---|---|---|---|
| `auth.py` | CENTRAL | `get_db` | login / me — identity |
| `admin.py` (users, roles) | CENTRAL | `get_db` | global identity |
| `admin.py` (periods) | COMPANY | `get_company_db` | `AccountingPeriod` is per-company |
| `tenancy.py` (orgs, memberships, companies, gst-regs, intercompany-mappings) | CENTRAL | `get_db` | group/identity catalog |
| `tenancy.py` (consolidated, pnl, elimination-suggestions, intercompany-posting) | CROSS-DB | reporting/saga services | reads/writes multiple company DBs |
| `accounts.py` | COMPANY | `get_company_db` | chart of accounts |
| `vouchers.py` | COMPANY | `get_company_db` | ledger postings |
| `accounting_ops.py` | COMPANY | `get_company_db` | GST adj / year-end / overhead |
| `reports.py` | COMPANY | `get_company_db` | TB, ledger, statements, GST registers |
| `masters.py` | COMPANY | `get_company_db` | customers, suppliers, warehouses, machines, … |
| `operations.py` | COMPANY | `get_company_db` | GRN, sales invoice, dyeing, dispensing |
| `orders.py` | COMPANY | `get_company_db` | PO/SO lifecycle |
| `presales.py` | COMPANY | `get_company_db` | enquiries/quotations |
| `banking.py` | COMPANY | `get_company_db` | receipts/payments/recon |
| `inventory_ops.py` | COMPANY | `get_company_db` | stock on hand, transfer, count |
| `transfers.py` | COMPANY | `get_company_db` | factory transfers |
| `knitting.py` / `rolls.py` | COMPANY | `get_company_db` | production, greige rolls |
| `quality.py` | COMPANY | `get_company_db` | finishing, inspections |
| `hardware.py` | COMPANY | `get_company_db` | scale/machine/dispensing ingest |
| `jobwork.py` / `stores.py` | COMPANY | `get_company_db` | job work, stores |
| `costing.py` | COMPANY | `get_company_db` | costing & profitability |
| `assets.py` / `payroll.py` | COMPANY | `get_company_db` | fixed assets, payroll |
| `dashboard.py` | COMPANY | `get_company_db` | company dashboards |
| `approvals.py` / `inbox.py` | COMPANY | `get_company_db` | maker-checker, notifications |
| `einvoice.py` / `ewaybill.py` | COMPANY | `get_company_db` | e-invoice / e-way (per company) |
| `bom.py` | COMPANY | `get_company_db` | Fabric BOM + production orders + grey fabric actual cost (factory-authorized) |
| `dyeing.py` | COMPANY | `get_company_db` | Chemical-free dyeing jobs + actual dyed cost + traceability (factory-authorized) |

**Result:** 26 company-scoped route modules on `get_company_db` (25 at cutover +
`bom.py`); `admin.py`
split (periods → company, users/roles → central); `auth.py` and the identity/catalog
part of `tenancy.py` kept central. No global identity or membership table was moved
into a company database.

## 2. Mechanism & safety

`get_company_db` (in `app/core/deps.py`) depends on `get_current_user`, which runs
once per request (FastAPI dependency caching) against the **central** DB to
authenticate and resolve the acting company from memberships. It then yields a
session routed to that company's database via `app/core/db_router.py`, falling back
to the central engine when the company is unregistered. Authorization is unchanged
(still `require_permission` on the resolved user).

The test suite uses a single in-memory database with no registered company
databases, so `get_company_db` resolves to the same engine as `get_db`; the
`client` fixture overrides both to the one shared session. This keeps all existing
tests valid while the real routing diverges only once a company is provisioned onto
its own database (validated on real PostgreSQL — see `POSTGRES_VALIDATION.md`).

## 3. Cross-database consolidation (reporting service)

Consolidation must read **each** member company's database independently and
aggregate in a reporting service (it can no longer join across companies in one
query). See § `CROSS-DATABASE CONSOLIDATION` in `DATABASE_PER_COMPANY.md`.

## 4. Cross-database inter-company posting (saga)

Two company databases cannot be committed in one SQLAlchemy transaction. Inter-
company posting uses a recoverable, idempotent **saga/outbox** with reconciliation
and compensation; **live cross-database posting stays disabled until it passes real
PostgreSQL tests**. See `DATABASE_PER_COMPANY.md`.

## 5. Background jobs / exports / files

Any background job, scheduled task, export or file operation that touches
company-scoped data must carry an **explicit company context** and open a session
via `db_router.company_session(company_id)` — it must not rely on an ambient request
scope. (The app currently has no always-on background workers; scheduled/async work
added later must follow this rule.)
