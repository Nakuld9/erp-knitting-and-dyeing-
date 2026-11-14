# 01 — UAT Environment Setup (exact steps)

This builds a clean UAT instance with **two legal companies**, each with
**multiple factories and warehouses**, real users with scoped access, and clearly
identified sample data. Linux/macOS commands are shown here; the equivalent
beginner Windows walk-through is in `07_WINDOWS_INSTALL_GUIDE.md`.

> Conventions
> - `$` = shell prompt. Run backend commands from `backend/` with the virtualenv active.
> - All example passwords are **placeholders** — replace them.
> - The ERP uses a **database-per-legal-company** design: a **central/control
>   database** holds global tables (users, roles, memberships, organizations,
>   companies, and the `company_databases` registry); each **company database**
>   holds that company's own data (ledger, inventory, production). This isolation
>   is a core control and must not be changed for UAT.

---

## 0. Prerequisites

- PostgreSQL **16** (or 14+).
- Python **3.11+** (the repo is tested on 3.13).
- Node + npm — only if you need to rebuild the UI (`frontend/build-ui.sh`); the
  compiled `app.js` is already vendored, so you normally do **not** need npm.

---

## 1. Set up PostgreSQL

Install and start PostgreSQL, then create a login role and the central database.

```bash
# as the postgres superuser
createuser --pwprompt erp            # choose a password, e.g. erp_dev_pw
createdb --owner=erp erp             # the CENTRAL / control database
```

Verify:

```bash
psql "postgresql://erp:erp_dev_pw@localhost:5432/erp" -c '\conninfo'
```

> For a quick disposable PostgreSQL you can instead run `docker compose up db`
> (see `docker-compose.yml`), which starts `postgres:16` bound to `127.0.0.1:5432`
> with user/password/db all `erp`.

---

## 2. Backend environment file

```bash
cd backend
cp .env.example .env
```

Edit `backend/.env` and set at least:

```ini
ENVIRONMENT=development
DATABASE_URL=postgresql+psycopg2://erp:erp_dev_pw@localhost:5432/erp
SECRET_KEY=<paste output of: python -c "import secrets; print(secrets.token_urlsafe(48))">
ERP_ADMIN_USERNAME=admin
ERP_ADMIN_PASSWORD=<a strong admin password>
ERP_ADMIN_EMAIL=admin@yourmill.local
DEFAULT_CURRENCY=INR
FISCAL_YEAR_START_MONTH=4
# Secret used when a company gets its own dedicated database (step 4):
ERP_COMPANY_DB_PW=erp_dev_pw
```

Create and populate the Python environment:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

---

## 3. Create the central platform database (schema + baseline seed)

```bash
cd backend
. .venv/bin/activate
alembic upgrade head              # creates the full schema in the central DB
python -m app.seed.seed           # permissions, roles, voucher types, demo company,
                                  #   chart of accounts, FY2026 periods, UoM, admin user
```

`app.seed.seed` prints the demo company id and admin username. This seeds the
first legal company (code **`DEMO`**) with its chart of accounts, 12 open monthly
periods for the Indian FY, cost centres, units of measure, posting rules, and the
superuser admin.

---

## 4. Provision two separate legal-company databases

You will run UAT with **two legal companies**. Company A is the seeded `DEMO`
company. Company B is a second legal entity. You have two supported options; use
**Option A** for the most faithful isolation test.

### Option A — dedicated database per company (true isolation)

1. **Register the two companies in the central DB.** Company A (`DEMO`) already
   exists. Create company B and (optionally) a group for consolidation. Run this
   one-off bootstrap from `backend/` with the venv active:

   ```bash
   python - <<'PY'
   from app.core.database import SessionLocal
   from app.models.core import Company
   from app.models.tenancy import Organization
   from sqlalchemy import select, update
   db = SessionLocal()
   a = db.execute(select(Company).where(Company.code == "DEMO")).scalar_one()
   b = db.execute(select(Company).where(Company.code == "UATB")).scalar_one_or_none()
   if b is None:
       b = Company(code="UATB", name="UAT Weaves Pvt Ltd",
                   legal_name="UAT Weaves Private Limited",
                   gstin="27ABCDE9999F1Z2", state_code="27",   # Maharashtra
                   currency="INR", fiscal_year_start_month=4)
       db.add(b); db.flush()
   org = db.execute(select(Organization).where(Organization.code == "UATG")).scalar_one_or_none()
   if org is None:
       org = Organization(code="UATG", name="UAT Group"); db.add(org); db.flush()
   db.execute(update(Company).where(Company.id.in_([a.id, b.id])).values(organization_id=org.id))
   db.commit()
   print("A (DEMO)      =", a.id)
   print("B (UATB)      =", b.id)
   print("Group (UATG)  =", org.id)
   PY
   ```

   Record the printed **company ids** — you need B's id next.

2. **Create an empty PostgreSQL database for company B and provision it** (full
   migrations + company-scoped seed + routing registration):

   ```bash
   createdb --owner=erp erp_company_uatb
   python -m scripts.provision_company_db \
       --company-id <COMPANY_B_UUID> \
       --dsn "postgresql+psycopg2://erp:\${ERP_COMPANY_DB_PW}@localhost:5432/erp_company_uatb" \
       --fy-start-year 2026
   ```

   The `${ERP_COMPANY_DB_PW}` placeholder is stored **literally** in the registry
   and expanded from the environment at connect time, so the password is never
   written into the `company_databases` table. After this, every request acting
   in company B is routed to `erp_company_uatb`; company A stays on the central DB.

   > Company A (`DEMO`) may also be given its own dedicated database the same way
   > (`createdb erp_company_demo` + provision with A's id). For UAT it is enough
   > that B is separate; leaving A on the central DB is the supported default and
   > still fully isolated at the company_id level.

### Option B — single database, company-scoped (lighter; isolation by `company_id`)

Skip the `createdb`/provision step. Both companies live in the central DB and are
isolated by `company_id` on every row and by the tenant-context guard. This is
simpler but does **not** demonstrate physical database separation. Use it only if
you cannot create extra databases; the multi-company checklist (`03`) still runs.

---

## 5. Create factories (branches) and warehouses in each company

A **factory == a branch** (`core.Branch`). Warehouses belong to a company and,
optionally, to a factory. There is no REST endpoint to create companies or
branches (they are structural), so create them with this one-off bootstrap. Run
from `backend/` with the venv active.

> In **Option A** (dedicated DB for B), company B's factories/warehouses must be
> created **inside B's database** via `company_session(B_id)`; the script below
> does that automatically. Company A's go in whichever DB A resolves to.

```bash
python - <<'PY'
import uuid
from sqlalchemy import select
from app.core.database import SessionLocal
from app.core.db_router import company_session
from app.models.core import Company, Branch
from app.models.masters import Warehouse

def ensure_branch(db, cid, code, name):
    b = db.execute(select(Branch).where(Branch.company_id==cid, Branch.code==code)).scalar_one_or_none()
    if b is None:
        b = Branch(company_id=cid, code=code, name=name); db.add(b); db.flush()
    return b

def ensure_wh(db, cid, bid, code, name, kind):
    w = db.execute(select(Warehouse).where(Warehouse.company_id==cid, Warehouse.code==code)).scalar_one_or_none()
    if w is None:
        w = Warehouse(company_id=cid, branch_id=bid, code=code, name=name, kind=kind); db.add(w); db.flush()
    return w

# resolve the two companies from the CENTRAL db
ctl = SessionLocal()
A = ctl.execute(select(Company).where(Company.code=="DEMO")).scalar_one()
B = ctl.execute(select(Company).where(Company.code=="UATB")).scalar_one()
ctl.close()

# Company A: two factories, each with greige + finished-goods + chemical stores
with company_session(A.id) as db:
    f1 = ensure_branch(db, A.id, "A-F1", "A Knitting Unit")
    f2 = ensure_branch(db, A.id, "A-F2", "A Dyeing Unit")
    ensure_wh(db, A.id, f1.id, "A-GREIGE", "A Greige Store", "GREIGE")
    ensure_wh(db, A.id, f1.id, "A-CHEM",   "A Chemical Store", "STORE")
    ensure_wh(db, A.id, f2.id, "A-FG",     "A Finished Goods", "FG")
    db.commit(); print("A factories/warehouses ready")

# Company B: two factories of its own
with company_session(B.id) as db:
    g1 = ensure_branch(db, B.id, "B-F1", "B Knitting Unit")
    g2 = ensure_branch(db, B.id, "B-F2", "B Dyeing Unit")
    ensure_wh(db, B.id, g1.id, "B-GREIGE", "B Greige Store", "GREIGE")
    ensure_wh(db, B.id, g1.id, "B-CHEM",   "B Chemical Store", "STORE")
    ensure_wh(db, B.id, g2.id, "B-FG",     "B Finished Goods", "FG")
    db.commit(); print("B factories/warehouses ready")
PY
```

> Warehouses can also be created later from the UI (**Master Data → Warehouses**,
> `POST /api/masters/warehouses`) once logged in, but factories/branches are
> created only as above.

---

## 6. Configure users, memberships, permissions and factory access

Access is decided **server-side** from membership rows — never trusted from the
client. The model:

- **Superuser** (seeded `admin`) — unrestricted; use it only for setup.
- **`UserCompanyMembership`** — grants a user a company; one is marked default.
- **`UserFactoryMembership`** — grants a user a *specific* factory. **Company
  membership alone does NOT grant factory access** unless the user's role holds
  the company-wide `factory.access_all` permission.
- Roles/permissions come from `app/seed/rbac.py`. Confirm the exact codes with
  `GET /api/admin/roles`. Roles that hold `factory.access_all` (so they see **any**
  factory) are **`OWNER`, `SUPER_ADMIN`, `GENERAL_MANAGER`**. For the
  factory-restricted manager used in the isolation test, pick a role **without**
  it, e.g. **`PRODUCTION_MANAGER`** — then the per-factory membership is what
  grants access and other factories are correctly refused. The accountant role is
  **`ACCOUNTANT`**.

> Note on `POST /api/admin/users`: it creates the user under the **admin's own
> company** (A). That is fine — the user's **acting company** is decided by their
> *default company membership* (step 6b), so granting `acct_b` a default
> membership to company B makes them act in B regardless of their home company.
> Users, roles and memberships are **global tables in the central database**, so
> these calls are correct even when company B has its own dedicated database.

### 6a. Create users (REST, as admin)

First log in as admin to get a token:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -d 'username=admin&password=<ADMIN_PASSWORD>' | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
```

Create the UAT users (`POST /api/admin/users` with a role list):

```bash
# Company A accountant
curl -s -X POST http://localhost:8000/api/admin/users -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"username":"acct_a","password":"<pw>","full_name":"Accountant A","email":"acct_a@yourmill.local","role_codes":["ACCOUNTANT"]}'

# Company A factory manager (factory-scoped)
curl -s -X POST http://localhost:8000/api/admin/users -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"username":"fm_a1","password":"<pw>","full_name":"Factory Manager A-F1","email":"fm_a1@yourmill.local","role_codes":["PRODUCTION_MANAGER"]}'

# Company B accountant
curl -s -X POST http://localhost:8000/api/admin/users -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"username":"acct_b","password":"<pw>","full_name":"Accountant B","email":"acct_b@yourmill.local","role_codes":["ACCOUNTANT"]}'

# Group administrator (consolidation)
curl -s -X POST http://localhost:8000/api/admin/users -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"username":"group_admin","password":"<pw>","full_name":"Group Admin","email":"group_admin@yourmill.local","role_codes":["SUPER_ADMIN"]}'
```

Check role codes available with `GET /api/admin/roles`.

### 6b. Grant company memberships (REST, as admin)

```bash
# acct_a -> company A (default); acct_b -> company B (default)
curl -s -X POST http://localhost:8000/api/admin/memberships/company -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"<ACCT_A_ID>","company_id":"<COMPANY_A_ID>","role_code":"ACCOUNTANT","is_default":true}'

curl -s -X POST http://localhost:8000/api/admin/memberships/company -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"<ACCT_B_ID>","company_id":"<COMPANY_B_ID>","role_code":"ACCOUNTANT","is_default":true}'

# fm_a1 -> company A
curl -s -X POST http://localhost:8000/api/admin/memberships/company -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"<FM_A1_ID>","company_id":"<COMPANY_A_ID>","role_code":"PRODUCTION_MANAGER","is_default":true}'
```

### 6c. Grant factory membership (REST, as admin)

The factory manager must be tied to **A-F1 only** so UAT can prove they cannot
see A-F2. The company is derived from the branch, so the body takes just
`user_id`, `branch_id` and an optional `role_code`:

```bash
curl -s -X POST http://localhost:8000/api/admin/memberships/factory -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"<FM_A1_ID>","branch_id":"<A_F1_BRANCH_ID>","role_code":"PRODUCTION_MANAGER"}'
```

User ids come from `GET /api/admin/users`; company ids from the step-4 bootstrap;
branch ids from `GET /api/admin/companies` → factory list, or the step-5 script
(print `f1.id` etc.).

### 6d. How scope is selected at request time

A logged-in user acts in their **default company** unless they send an
`X-Company-Id` header (validated against membership → 403 if not a member). A
factory is selected with `X-Factory-Id` (validated against factory membership or
`factory.access_all` → 403 otherwise). Confirm the resolved scope any time with:

```bash
curl -s http://localhost:8000/api/me/context -H "Authorization: Bearer $TOKEN"
```

---

## 7. Start the backend and frontend locally

The backend serves both the REST API and the self-contained React UI.

```bash
cd backend
. .venv/bin/activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

- **Web UI:** http://localhost:8000/app/
- **API docs (Swagger):** http://localhost:8000/docs
- **Health:** http://localhost:8000/health/ready

The frontend is **already compiled** (`frontend/app.js`) and served at `/app/` —
no separate frontend server and no npm needed. Only rebuild it if you edit
`frontend/app.src.jsx`:

```bash
cd frontend && ./build-ui.sh    # needs npm; re-vendors libs + recompiles app.js
```

Sign in at `/app/` with the admin user (or the UAT users from step 6). Use the
company/factory switcher in the UI to change scope (sends the `X-Company-Id` /
`X-Factory-Id` headers for you).

---

## 8. Load clearly identified sample data only

Load the **named UAT sample set** so results match `04_EXPECTED_VALUES.md`
exactly. Everything created carries obvious `UAT`/`DEMO` codes so it can never be
mistaken for real data. Run from `backend/` with the venv active:

```bash
python - <<'PY'
from sqlalchemy import select
from app.core.database import SessionLocal
from app.core.db_router import company_session
from app.models.core import Company
from app.seed.demo import seed_manufacturing_demo

ctl = SessionLocal()
ids = {c.code: c.id for c in ctl.execute(select(Company).where(Company.code.in_(["DEMO","UATB"]))).scalars()}
ctl.close()

for code, cid in ids.items():
    with company_session(cid) as db:
        handles = seed_manufacturing_demo(db, cid, opening_qty="500", opening_fy_year=2026)
        db.commit()
        print(code, "sample data loaded: yarn", handles["yarn_id"])
PY
```

This seeds, **per company**: a GST tax code (`GST18`), warehouses (chemical /
greige / finished-goods), a supplier and a customer, chemicals & dyes with priced
opening lots, a yarn (`Y-30S`, 30s combed cotton) with **2000 kg opening @ ₹240/kg**,
knitting + dyeing machines, a shade, a fabric type, and an **approved dye recipe** —
plus a balanced **opening-stock voucher** so the inventory sub-ledger ties to the
GL from the start.

> Chemical BOM automation is **off** by design: the recipe is informational;
> dyeing is costed at actual (dyeing charge + chemical cost + reprocess). Do not
> enable chemical BOM explosion for UAT.

### Sanity check before testing

As each company's accountant (or admin with `X-Company-Id` set), confirm the
baseline is clean:

```bash
# Trial balance must balance; stock must reconcile to the GL.
curl -s "http://localhost:8000/api/reports/trial-balance"        -H "Authorization: Bearer $TOKEN" -H "X-Company-Id: <COMPANY_A_ID>"
curl -s "http://localhost:8000/api/reports/stock-reconciliation" -H "Authorization: Bearer $TOKEN" -H "X-Company-Id: <COMPANY_A_ID>"
```

Expected: trial balance `is_balanced = true`; stock reconciliation
`is_reconciled = true`. If either is false, stop and fix the environment before
running any scenario.

---

## Environment ready — checklist

- [ ] PostgreSQL running; central DB `erp` reachable.
- [ ] `alembic upgrade head` + `python -m app.seed.seed` done (company A = `DEMO`).
- [ ] Company B (`UATB`) registered; (Option A) its dedicated DB provisioned & routed.
- [ ] Two factories + warehouses in **each** company.
- [ ] Users created: `acct_a`, `fm_a1`, `acct_b`, `group_admin`.
- [ ] Memberships granted (company + the factory-only grant for `fm_a1`).
- [ ] Backend up at `:8000`; UI reachable at `/app/`.
- [ ] Named UAT sample data loaded in both companies.
- [ ] Baseline trial balance balances and stock reconciles in both companies.

Proceed to `02_UAT_BUSINESS_CHECKLIST.md`.
