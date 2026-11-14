# Company Branding & ERP Document Printing — Implementation Plan

**Status:** Documentation only. No application code changed. Not merged, not
deployed, no live data migrated, no historical transactions rewritten, chemical
BOM automation untouched, database-per-legal-company architecture preserved.

**Branch:** `feat/multi-company-production-ready`
**Scope:** branding (active legal company identity + logo), a reusable document
print framework, and a defined set of printable ERP documents, all strictly
read-only with respect to posted data.

> This plan records the owner's four final design decisions (logo storage; event
> document numbering; Purchase Return as two linked documents; commit the plan)
> and the grounded design for implementation **in later, separately-approved
> stages**.

---

## 1. Company branding architecture

### 1.1 Current state (inspected)

- Sidebar label is **hardcoded**: `frontend/app.src.jsx:163` (`<h2>Textile ERP</h2>`)
  and `:2999` (`◆ Textile ERP`). The SPA already calls `/api/me/context`.
- `Company` (`backend/app/models/core.py:33`) has `name, legal_name, gstin,
  state_code, address, currency`. **Branch/factory** (`:54`) has its own
  `code, name, gstin, state_code, address`.
- **Missing company fields:** `cin, phone, email, website`, and **no logo**.
- `/api/me/context` (`backend/app/api/routes/tenancy.py:72`) returns company
  `id/code/name` + factories, but **not** `legal_name/gstin/address/logo`.

### 1.2 Target

- Resolve the **active legal company** from the authenticated tenant context
  (never a hardcoded name), and render:
  - **legal company name** (prefer `Company.legal_name`, fall back to `name`),
  - **company logo** (or a clean text monogram when none exists),
  - **selected factory** shown **separately** (its own line/chip) — a factory name
    is never substituted for or merged with the legal company name.
- **Per-legal-company branding**: each company's identity and logo are independent
  and isolated; company A must never display company B's name, GSTIN, address or
  logo.
- The same resolved identity feeds **every** printed document, report and print
  preview.

### 1.3 New/extended company settings fields

Additive, per legal company:

| Field | Notes |
|---|---|
| `cin` | Corporate Identity Number (where applicable; optional) |
| `phone`, `email`, `website` | contact block for letterhead |
| registered `address`, `gstin`, `state_code` | already present on `Company` |
| logo | stored separately — see §2 |

`legal_name`, `gstin`, `state_code`, `address` already exist and are reused.

---

## 2. Company logo storage and upload/retrieval design

**Decision: store logo bytes in PostgreSQL `BYTEA`, in a `CompanyBranding` table
inside each company's operational database. No Base64-in-text. No filesystem
paths. No shared storage.**

### 2.1 `CompanyBranding` model (new, additive)

Lives in the **company-scoped** schema (same migrations run per company DB), so a
company's logo travels with its own database and is covered by that database's
backup/restore.

| Column | Type | Notes |
|---|---|---|
| `id` | Uuid PK | |
| `company_id` | Uuid, unique, indexed | one branding row per company |
| `logo_bytes` | **BYTEA / LargeBinary**, nullable | raw image bytes |
| `logo_filename` | String | original filename |
| `logo_mime_type` | String | `image/png`, `image/jpeg`, `image/webp` only |
| `logo_size_bytes` | Integer | stored size |
| `logo_uploaded_at` | DateTime(tz) | upload date |
| (contact mirror, optional) | — | or keep `cin/phone/email/website` on `Company`; one or the other, decided at build |

### 2.2 Upload / retrieval

- **Upload** (`POST`, admin + company-scoped): accept a multipart image; **validate**
  MIME type ∈ {PNG, JPEG, WebP} and **file-size cap** (e.g. ≤ 512 KB, final cap set
  at build); reject anything else with 400. Store bytes + metadata; replace in place
  on re-upload (no history of the image bytes required, but the `uploaded_at`
  updates and the action is audit-logged).
- **Retrieve** (`GET`): stream bytes with the stored `Content-Type` for the
  **active company only**, resolved from the tenant context — **cross-company logo
  access is prevented** by the same company guard used across the app
  (`get_company_db` routing + company_id match). A request for another company's
  logo returns 404 (existence not leaked).
- **Fallback**: when no logo row/bytes exist, the UI and PDFs render a **clean
  text-based identity** (monogram from the legal name) — never a broken image.

### 2.3 Backup / restore implications

- Because branding lives in the **company database**, it is included automatically
  in that database's `pg_dump` and restored by `pg_restore` (see
  `scripts/backup.sh` / `restore.sh` and `docs/uat/07_WINDOWS_INSTALL_GUIDE.md`).
- **No** extra asset store to back up, and **no** filesystem dependency — a key
  reason BYTEA-in-company-DB was chosen over file paths.
- The control/central database carries no logo bytes; it only maps companies to
  their databases.

---

## 3. Sidebar company / factory identity

- Extend `/api/me/context` (and/or a dedicated `GET /api/me/branding`) to return,
  for the active company: `legal_name`, `name`, `gstin`, `state_code`, `address`,
  `cin`, `phone`, `email`, `website`, and a `has_logo` flag + logo URL.
- **Sidebar** renders: logo (or monogram) + **legal company name** prominently;
  **selected factory** (`active.factory_id` → its `name`) on a **separate** line,
  clearly labelled "Factory", so the two are never confused.
- Re-renders on company/factory switch (the switch already drives `X-Company-Id` /
  `X-Factory-Id`). Branding is read fresh per active company, so two companies in
  the same session never bleed branding.

---

## 4. Complete list of printable ERP documents

Legend — **Backing:** D = dedicated numbered document object exists; E = event/
source record (print number stamped per §5); R = report (no per-row number).

| # | Document | Backing | Source model(s) (inspected) |
|---|---|---|---|
| 1 | GST Sales Tax Invoice | D | `SalesInvoice` (`commercial.py:173`) |
| 2 | Sales Delivery Challan | D | `DeliveryNote` (`commercial.py:209`) |
| 3 | Credit Note | D | `CreditNote` (`commercial.py:247`) |
| 4 | Purchase Order | D | `PurchaseOrder` (`commercial.py:107`) |
| 5 | Goods Receipt Note | D | `GoodsReceiptNote` (`commercial.py:137`) |
| 6 | **Purchase Return Challan** (physical) | D (new) | new `PurchaseReturn` header + lines (see §6) |
| 7 | **Purchase Debit Note** (financial/GST) | D | existing `DebitNote` (`commercial.py:285`) — reused, not duplicated |
| 8 | Yarn Issue Challan | E | `ProductionConsumption` / issue voucher `PO:{n}:issue:{id}` |
| 9 | Grey Fabric Receipt | E | grey valuation (`value_grey_output`) on `ProductionOrder` |
| 10 | Dyeing Job Challan | D | `DyeingJob` (`dyeing_cost.py:40`) |
| 11 | Dyed Fabric Receipt | E | `DyeingOutput` / `complete_job` |
| 12 | Outside Job-Work Challan | D | `JobWork` (`jobwork.py:46`) |
| 13 | Stock Transfer Challan | D | `FactoryStockTransfer` (`transfers.py:32`) / `StockTransfer` (`inventory.py`) |
| 14 | Inventory Movement Challan | E | `InventoryTransaction` (`inventory.py`) |
| 15 | Material Return Challan | E | `PRODUCTION_RETURN` inventory movement |
| 16 | Stock Adjustment Voucher | E | physical-count variance movement |
| 17 | Production Reports | R | costing/production services (reuse `ReportDoc`) |
| 18 | Stock Ledger | R | FIFO sub-ledger / stock-reconciliation services |

**Delivery surfaces (both required):**
1. **Central Documents / Print Centre** — one screen to search any document by
   type + number + date + party/factory, preview, print, download, reprint.
2. **Print & Preview buttons inside the relevant transaction screens** (invoice,
   dispatch, PO, GRN, dyeing job, transfer, etc.).

All three outputs supported per document: **PDF download**, **browser print**
(A4 `@media print`), and **on-screen preview**.

---

## 5. Document numbering strategy

**Reuse the existing, proven service — do not build a new engine.**

- `backend/app/services/sequences.py::next_document_number` keys on
  `(company_id, doc_type, fiscal_year)`, formats `PREFIX + FYYY-NNNNN`, and
  **row-locks** the sequence (`with_for_update` on PostgreSQL) so concurrent
  callers never collide. Backed by `DocumentSequence` with
  `UniqueConstraint(company_id, doc_type, fiscal_year)` (`models/audit.py:29`).
- Properties this already guarantees: **company-specific**, **doc-type-specific**,
  **financial-year-aware**, **concurrency-safe**, **never duplicated**.

### 5.1 Documents that already have numbers

Invoice, delivery note, credit note, debit note, PO, GRN, dyeing job, job-work,
stock transfer: **print the stored `number`**. No allocation at print time.

### 5.2 Event-based documents — "stamp on first print" (owner decision)

Applies to: **Yarn Issue Challan, Grey Fabric Receipt, Dyeing Job Challan (where
event-based), Dyed Fabric Receipt, Inventory Movement Challan, Material Return
Challan, Stock Adjustment** and any other event-based production/inventory
document.

Rules:
- **Preview allocates nothing.** A number is allocated **only** when the user
  actually issues/prints the document.
- On **first print**, allocate a **doc-type-specific** number via
  `next_document_number` (company-specific, FY-aware where applicable) and
  **store it on the original event/source record** (a new nullable
  `print_doc_number` + `print_doc_type` + `printed_at` column on the relevant
  source row, additive migration).
- **Reprints reuse the stored number** — always. No re-allocation.
- **Concurrency:** allocation happens inside a transaction under the sequence
  row-lock; stamping the source row is guarded so two simultaneous first-prints
  resolve to **one** number (DB-level protection; the loser reads the stamped
  value).
- **Printing never creates** a new inventory transaction, GL voucher or production
  event. Stamping a print number is metadata on the existing row, not a business
  event.
- **Audit:** issue and each reprint are recorded in the append-only `AuditLog`
  (`action = PRINT` / `REPRINT`, entity + number), preserving issue/reprint
  history without mutating the transaction.

---

## 6. Purchase Return Challan vs Purchase Debit Note (owner decision)

**Both documents, one underlying return transaction.**

- **A. Purchase Return Challan** — the **physical goods-return** document:
  supplier, material, quantity, UOM, lot/batch, warehouse, **return reason**,
  dispatch details. Backed by a **new `PurchaseReturn` header + lines** model
  (additive).
- **B. Purchase Debit Note** — the **financial + GST adjustment**: reuses the
  **existing `DebitNote`** (`commercial.py:285`), which already relieves FIFO
  stock, reduces the payable and reverses input GST. **Existing debit-note
  functionality is not duplicated** — the new work is only the physical challan
  and the links.

Controls:
- Both reference the **same underlying purchase-return transaction**.
- **Inventory is reduced exactly once**; **payable + ITC adjusted exactly once** —
  by the single existing posting path. The challan is a *view/print* of the
  physical movement, not a second stock relief.
- **Links preserved:** original **GRN → Purchase Return Challan → Debit Note**
  (FK references), so the chain is traceable both ways.
- Printing either document creates **no** second return and **no** new posting.
- Cancellation/reversal follows the **existing** accounting reversal + audit
  controls; a cancelled return shows **CANCELLED** on both printed documents.

> Build note: confirm the exact posting sequence so the physical challan and the
> debit note share one return event (challan records the movement; debit note
> records the GL/GST), with the stock relief and the payable/ITC adjustment each
> occurring once.

---

## 7. Print layouts and document templates

### 7.1 Framework (reuse + extend)

- **Reuse** `reportlab` + `backend/app/services/exports/` (`document.py` →
  `pdf.py`/`excel.py`), and the per-document inline-PDF precedent
  `app/services/labels.py::roll_label_pdf` served at `/api/rolls/{id}/label`.
- **Extend** the document layer with a `DocumentDoc` (distinct from the
  report-only `ReportDoc`) composed of:
  1. **Letterhead** — logo (or monogram) + legal company name, registered address,
     GSTIN, CIN, phone/email/website.
  2. **Document title + unique number + date** (and references: PO↔GRN,
     invoice↔challan, GRN↔return↔debit note, dyeing job↔grey order↔yarn lots).
  3. **Parties** — customer / supplier / **factory** block, with GSTIN and
     place-of-supply where relevant.
  4. **Line items** — description, **HSN** (where required), **quantity**, **UOM**,
     rate, value; **roll-wise detail** (roll no, **shade, dye lot, GSM, weight,
     warehouse**) where applicable.
  5. **Tax block** — CGST/SGST/IGST split from the configurable tax code (never
     hardcoded), taxable value, total, rounding, amount in words.
  6. **Authorised-signature area**, document footer, and a **CANCELLED** watermark
     when `status == CANCELLED`, plus a "Reprint — original unchanged" marker on
     reprints.

### 7.2 A4 / print

- PDF pages are A4 with professional margins; the HTML preview uses `@media print`
  for faithful browser printing. Reports (17, 18) keep the existing `ReportDoc`
  path with the new letterhead applied.

### 7.3 Partial dispatch & roll detail

- Documents print **only the actual dispatched/moved quantities** for that
  document instance (e.g. a partial delivery prints that challan's rolls only),
  read from the stored lines — never recomputed in a way that alters data.

---

## 8. Company / factory access control

- Every document endpoint runs under the existing **tenant guard**
  (`get_company_db` routing + `get_tenant_context`), so a user only reaches
  documents of a company they are a member of; **cross-company access → 404/403**.
- Factory-scoped documents respect **factory-level permissions**
  (`authorize_factory` / `UserFactoryMembership` / `factory.access_all`); a
  factory-restricted user cannot print another factory's documents.
- Branding retrieval is company-scoped (§2.2); **A never shows B's GSTIN, address
  or branding**.
- Report/print permissions reuse existing permission codes (e.g. `report.read`)
  plus document-specific read permissions where needed.

---

## 9. Backup and restore implications

- Logo bytes + branding metadata live in each **company database** → included in
  that DB's `pg_dump`/`pg_restore`; **no separate asset backup**.
- New additive tables/columns (`CompanyBranding`, `PurchaseReturn`, the
  `print_doc_number` stamps) are created by migrations run per company database,
  consistent with provisioning (`scripts/provision_company_db.py`).
- Restores already verified via trial-balance + stock-reconciliation checks
  (`docs/uat/07`); branding and print numbers restore with the company DB.
- Database-per-legal-company architecture is **unchanged**.

---

## 10. Existing code / models / routes to reuse

| Need | Reuse (inspected) |
|---|---|
| Numbering | `services/sequences.py::next_document_number`; `models/audit.py::DocumentSequence` |
| PDF/Excel render | `services/exports/{document,pdf,excel,builders}.py`; `services/labels.py` (per-doc inline PDF) |
| Report export endpoint pattern | `api/routes/reports.py::export_report` (`/api/reports/export/{report}`) |
| Company identity | `models/core.py::Company` (+ new fields); `/api/me/context` (extend) |
| Sales docs | `models/commercial.py` (SalesInvoice/DeliveryNote/CreditNote/DebitNote) + `api/routes/orders.py` GETs |
| Purchase docs | `commercial.py` (PurchaseOrder/GoodsReceiptNote/DebitNote) + `api/routes/orders.py` |
| Production docs | `fabric_bom.py` (ProductionOrder/Consumption), `dyeing_cost.py` (DyeingJob/Output), `jobwork.py` (JobWork) |
| Inventory docs | `inventory.py` (InventoryTransaction/StockTransfer), `transfers.py` (FactoryStockTransfer) |
| Roll detail | `manufacturing.py::FabricRoll` (roll_no, shade, GSM, weight, warehouse, dye lot); `dyeing.trace` |
| Status/cancel | `commercial.py::DocStatus` (incl. `CANCELLED`); per-model `status` |
| Audit | `models/audit.py::AuditLog` (append-only; add PRINT/REPRINT actions) |
| Access control | `core/context.py` (`get_tenant_context`, `authorize_factory`), `core/db_router.py` |

---

## 11. Database migration requirements (all additive)

No schema is altered destructively; no live data migrated. New migrations, run per
company database:

1. **`company_branding`** table (BYTEA logo + metadata) — §2.1.
2. **`companies`** new nullable columns: `cin`, `phone`, `email`, `website`
   (or mirror the contact fields on `company_branding`).
3. **`purchase_returns`** header + **`purchase_return_lines`**, with FKs to GRN and
   the debit note — §6.
4. **Print-stamp columns** (`print_doc_number`, `print_doc_type`, `printed_at`;
   nullable) on the event source rows that need a stamped number — §5.2.

Each is created by the standard migration + provisioning flow; existing rows keep
working (nullable/backfill-free).

---

## 12. GST & statutory review items requiring CA confirmation

No statutory wording will be invented; the following need a practising Indian
CA / GST professional sign-off (ties to the standing **MEDIUM-4** review):

1. **Tax Invoice (CGST Rule 46)** — mandatory field set & wording; HSN granularity
   required at the mill's turnover slab; place-of-supply and reverse-charge flags.
2. **Delivery Challan (Rule 55)** — required fields/title for movement without a
   tax invoice (job-work, dispatch-before-invoice).
3. **Job-work challan (Rule 45 / ITC-04)** — format and ITC-04 linkage for the
   Outside Job-Work Challan.
4. **Purchase Return / Debit Note (Section 34 + Rule 55)** — challan vs debit-note
   roles, mandatory reference to the original GRN/invoice, reason wording, and
   correct ITC-reversal treatment.
5. **Credit/Debit Note (Section 34)** — original-invoice reference and reason.
6. **E-invoice (IRN/signed QR) & e-way bill** — whether printed invoices must carry
   IRN/Ack No/QR, and the applicable thresholds (modules already exist:
   `einvoice`, `ewaybill`).
7. **Rounding, amount-in-words, "computer-generated / authorised signatory"**
   phrasing acceptable for statutory use.
8. **Intra- vs inter-state** determination for the mill's factory/customer states.

---

## 13. Implementation stages and dependencies

Each stage is a **separate, reviewable commit** on
`feat/multi-company-production-ready`; printing stays read-only throughout; no
main merge, no deploy.

| Stage | Deliverable | Depends on |
|---|---|---|
| 0 | This plan (committed) | — |
| 1 | `CompanyBranding` model + company contact fields; migrations (additive) | — |
| 2 | Branding API: logo upload/validate/retrieve (company-scoped), extend `/api/me/context` + `/api/me/branding` | 1 |
| 3 | PDF `DocumentDoc` + letterhead framework (logo/monogram, parties, items, tax, signatures, CANCELLED watermark, amount-in-words) | 1–2 |
| 4 | Sidebar branding (legal name + logo + separate factory) + Company Settings UI (logo upload, contact) | 2 |
| 5 | Event print-number stamping service (stamp-on-first-print, reprint-reuse, concurrency, audit) + stamp columns | 1, 3 |
| 6 | Sales docs: GST Invoice, Delivery Challan, Credit Note | 3 |
| 7 | Purchase docs: PO, GRN, **Purchase Return Challan + Debit Note links** | 3, (6) |
| 8 | Production docs: Yarn Issue, Grey Receipt, Dyeing Job, Dyed Receipt, Job-Work Challan | 3, 5 |
| 9 | Inventory docs: Stock Transfer, Inventory Movement, Material Return, Stock Adjustment | 3, 5 |
| 10 | Reports with letterhead: Production Reports, Stock Ledger | 3 |
| 11 | **Central Documents / Print Centre** + per-screen Print/Preview buttons; browser print (A4) | 6–10 |
| 12 | Controls & tests + docs (see §14) | all |

Dependencies: §3 letterhead (Stage 3) and §5 numbering (Stage 5) underpin all
document stages; branding data (Stage 1–2) underpins every letterhead.

---

## 14. Acceptance criteria and automated tests

**Acceptance criteria**
- Sidebar shows the **active legal company's registered name + logo**; the
  selected **factory is shown separately** and never substituted for the company.
- Each legal company shows **only its own** branding/GSTIN/address (no cross-company
  bleed).
- Every listed document renders a professional **A4** layout with company identity,
  unique number, date, party/factory details, item/roll details, qty, UOM,
  signatures and the correct references.
- **Printing/reprinting never modifies posted transaction data**; reprints reuse
  the original number and data; cancelled documents show **CANCELLED**.
- Event documents allocate a number **only on first print**, never on preview;
  simultaneous prints never duplicate a number.
- Purchase return: inventory reduced **once**, payable/ITC adjusted **once**;
  challan and debit note reference one return and link back to the GRN.
- PDF download, browser print and preview all work; access respects
  company/factory permissions.

**Automated tests (to add in the build stages)**
- Branding: upload validation (type/size), company-scoped retrieval, **cross-company
  logo access → 404**, monogram fallback when no logo.
- Numbering: first-print allocation; **reprint reuses number**; **preview allocates
  nothing**; concurrency (two simultaneous first-prints → one number) on PostgreSQL.
- Read-only guarantee: printing/reprinting leaves the transaction row, GL and
  inventory **byte-for-byte unchanged** (assert no new voucher/stock move).
- Cancelled rendering: `status == CANCELLED` → watermark present.
- Partial dispatch: challan prints only that instance's dispatched rolls/qty.
- Roll detail: shade/dye lot/GSM/weight/warehouse present where applicable.
- Purchase return: inventory and payable/ITC each change exactly once across
  challan + debit note; GRN↔challan↔debit-note links intact; reprint creates no
  second return.
- Isolation: company A's document endpoints never return company B's documents or
  branding; factory-restricted user blocked from another factory's documents.
- Rendering smoke tests: each document builds a valid PDF (non-empty, A4) from
  seeded data.

---

## 15. Restrictions honoured by this plan

Documentation only; no application code yet; no main merge; no deploy; no live-data
migration; no historical-transaction rewrite; chemical BOM automation untouched;
database-per-legal-company architecture, factory-level permissions and accounting
controls preserved. Implementation proceeds only on separate approval.
