# 07 — Windows PC Installation Guide (Development / UAT)

A step-by-step guide for a **non-technical operator** to run the ERP on a single
**Windows 10/11 PC** for development and UAT. This is **not** a production
deployment. Every command is typed into **PowerShell** (press **Start**, type
`PowerShell`, press Enter). Lines starting with `#` are comments — don't type them.

> Rule of thumb: do each step in order, and don't continue until the step's
> "Check" works. If a command "isn't recognized", close and reopen PowerShell so
> it picks up newly installed programs.

---

## Step 1 — Install PostgreSQL

1. Go to the EDB PostgreSQL download page and download the **PostgreSQL 16**
   Windows installer.
2. Run the installer. When asked:
   - **Password for the `postgres` superuser:** choose one and **write it down**
     (e.g. in a password manager). You'll need it.
   - **Port:** keep **5432**.
   - **Components:** keep *PostgreSQL Server*, *pgAdmin 4*, and *Command Line
     Tools* ticked. (pgAdmin is a friendly database viewer; the command line
     tools give you `psql`, `createdb`, `pg_dump`.)
3. Finish. You can skip "Stack Builder" at the end.

**Add the tools to your PATH** (so `psql` works in PowerShell). The tools live in
`C:\Program Files\PostgreSQL\16\bin`. Run PowerShell **as Administrator** once:

```powershell
setx /M PATH "$($env:PATH);C:\Program Files\PostgreSQL\16\bin"
```

Close and reopen PowerShell.

**Check:**
```powershell
psql --version        # should print: psql (PostgreSQL) 16.x
```

---

## Step 2 — Create the database login and the central database

In PowerShell (replace `postgres`'s password when prompted):

```powershell
# Create an application login 'erp' (it will ask you to set a password — use e.g. erp_dev_pw)
createuser -U postgres --pwprompt erp

# Create the central/control database owned by 'erp'
createdb -U postgres --owner=erp erp
```

**Check:**
```powershell
psql "postgresql://erp:erp_dev_pw@localhost:5432/erp" -c "\conninfo"
```
It should say you are connected to database `erp` as user `erp`.

---

## Step 3 — Install Python

1. Download **Python 3.12 (or 3.11+)** for Windows from python.org.
2. Run the installer. **IMPORTANT:** on the first screen, tick
   **"Add python.exe to PATH"**, then click *Install Now*.

**Check** (reopen PowerShell first):
```powershell
python --version      # should print Python 3.11 or newer
```

---

## Step 4 — Get the ERP code onto the PC

If you were given a ZIP, unzip it to `C:\erp`. If you use Git:

```powershell
cd C:\
git clone <your-repository-url> erp
```

You should now have a folder `C:\erp` containing `backend`, `frontend`, `docs`.

---

## Step 5 — Set up the backend

```powershell
cd C:\erp\backend

# Create an isolated Python environment and turn it on
python -m venv .venv
.\.venv\Scripts\Activate.ps1
# If PowerShell blocks the script, run this once, then retry the line above:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

# Install the backend's Python packages
pip install -r requirements.txt
```

When the environment is active your prompt shows `(.venv)` at the start.

**Create the settings file.** Copy the example and open it in Notepad:

```powershell
copy .env.example .env
notepad .env
```

Set these lines (keep the rest as-is), then **Save** and close Notepad:

```ini
ENVIRONMENT=development
DATABASE_URL=postgresql+psycopg2://erp:erp_dev_pw@localhost:5432/erp
SECRET_KEY=paste-a-long-random-value-here
ERP_ADMIN_USERNAME=admin
ERP_ADMIN_PASSWORD=choose-a-strong-admin-password
ERP_ADMIN_EMAIL=admin@yourmill.local
ERP_COMPANY_DB_PW=erp_dev_pw
```

To generate a random `SECRET_KEY`, run this and paste the output into the file:
```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

---

## Step 6 — Create the database tables and the first company

With `(.venv)` active, in `C:\erp\backend`:

```powershell
alembic upgrade head          # builds all the tables in the 'erp' database
python -m app.seed.seed       # creates roles, the demo company, chart of accounts,
                              #   accounting periods, and the 'admin' login
```

The second command prints a line like `Seed complete: {...'admin': 'admin'...}`.

> To set up the **second company**, its **factories/warehouses**, **users** and
> **sample data**, follow `01_ENVIRONMENT_SETUP.md` steps 4–8 (the same Python
> snippets work on Windows — paste them at the `(.venv)` PowerShell prompt). Those
> steps are shared across operating systems, so they're not repeated here.

---

## Step 7 — Start the application

```powershell
cd C:\erp\backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Leave this window open — it is the running server. You'll see log lines; that's
normal. To stop the server later, click the window and press **Ctrl + C**.

**Open the app** in your web browser:
- ERP web app: **http://localhost:8000/app/**
- Technical API page: http://localhost:8000/docs

The frontend is already built and served by the same server — there is **no
separate frontend to start** and **no npm needed**.

---

## Step 8 — Log in (create your login)

The **admin** login was created by `python -m app.seed.seed` in Step 6, using the
`ERP_ADMIN_USERNAME` / `ERP_ADMIN_PASSWORD` from your `.env`.

1. Open **http://localhost:8000/app/**.
2. Sign in with `admin` and the admin password you set.
3. Go to the **Administration** page to create more users and assign their roles,
   or follow `01_ENVIRONMENT_SETUP.md` §6 to create the UAT users
   (`acct_a`, `fm_a1`, `acct_b`, `group_admin`) and their company/factory access.

> **Change the admin password** away from any example value before letting others
> use the PC. Never reuse these development passwords in production.

---

## Step 9 — Back up and restore (do this before trusting any data)

Your UAT data lives in PostgreSQL. Backups are plain files you can copy to a USB
drive or network share.

### Make a backup
PostgreSQL's `pg_dump` writes a single compressed backup file. In PowerShell:

```powershell
# Create a backups folder once
mkdir C:\erp\backups -Force

# Back up the central database (repeat per company database if you made dedicated ones)
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
pg_dump --format=custom --no-owner "postgresql://erp:erp_dev_pw@localhost:5432/erp" --file "C:\erp\backups\erp-$stamp.dump"
```

**Check:** a file like `erp-20261114-093000.dump` appears in `C:\erp\backups`.
Copy it somewhere safe (USB / network / cloud).

> If you made a **dedicated company database** (e.g. `erp_company_uatb`), back it
> up too — change the database name in the connection string and the output file
> name. The whole system = the central database **plus** every company database.

### Restore a backup (into a SEPARATE database first — never overwrite live data by reflex)

```powershell
# Create an empty restore target, then load the backup into it
createdb -U postgres --owner=erp erp_restore
pg_restore --no-owner --clean --if-exists --dbname "postgresql://erp:erp_dev_pw@localhost:5432/erp_restore" "C:\erp\backups\erp-20261114-093000.dump"
```

**Verify the restore** before relying on it: point the app at the restore database
(temporarily set `DATABASE_URL=...erp_restore` in `.env` and restart Step 7), then
open **Reports → Trial Balance** (must balance) and **Reports → Stock vs GL
Reconciliation** (must reconcile). Change `.env` back to the live `erp` database
afterward.

### A simple daily routine
- Run the **backup** command at the end of each UAT day.
- Keep at least the **last 7** daily backups off the PC (USB/network).
- Once a week, do a **restore into `erp_restore`** and check the trial balance —
  a backup you have never restored is not a backup you can trust. (The repo's
  `scripts/backup_restore_drill.sh` automates this full drill on Linux/Mac.)

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `psql`/`createdb` "not recognized" | Reopen PowerShell; re-check the PATH step 1. |
| `Activate.ps1 cannot be loaded` | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then retry. |
| `password authentication failed` | The password in `DATABASE_URL` must match the one you set for `erp` in step 2. |
| Browser shows "can't reach the page" | The server window (step 7) must stay open and show no error; use `http://localhost:8000/app/`. |
| Port 5432 already in use | Another PostgreSQL is running; use it, or change the port in both the installer and `DATABASE_URL`. |
| `alembic`/`uvicorn` "not recognized" | The `(.venv)` must be active — run `.\.venv\Scripts\Activate.ps1` first. |

---

## What this guide does and does not set up

- **Does:** a single-PC development/UAT instance — PostgreSQL, the central
  database, the backend API, the built-in web UI, the admin login, and a
  backup/restore routine.
- **Does not:** production hosting, HTTPS/TLS, multi-user networking, automated
  scheduled backups as a service, monitoring, or the GST statutory and security
  reviews. Those are production concerns (`docs/DEPLOYMENT.md`,
  `docs/PRODUCTION_DEPLOYMENT_RUNBOOK.md`, `docs/OPERATIONS.md`) and are **out of
  scope for UAT**.
