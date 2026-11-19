# GST Portal, ITC Reconciliation & Adjustment Module (Stage 6F-P5 GST)

This module adds a BUSY-comparable GST ITC workflow on top of the existing GST
reporting (`app/services/accounting/gst.py`) and the existing double-entry ITC
adjustment engine (`app/services/accounting/gst_adjustment.py`). It covers portal
imports, GSTR-2A/2B vs purchase-register reconciliation, the full ITC status
lifecycle (defer / carry-forward / claim / reversal / reclaim), CA-gated credit
cross-utilisation, electronic-ledger reconciliation, reporting and audit.

> **Statutory posting stays disabled** until the required CA approvals and an
> explicit authorisation flag are supplied. No GST filing or payment is performed.

## 1. Investigation — the official GSTN download route

There is **no public, taxpayer-facing REST API** to download GSTR-2A / GSTR-2B
directly. The verified routes are:

1. **Manual portal download (default).** The taxpayer downloads the JSON / Excel
   from the GST portal's Returns Dashboard. GSTR-2B is a **static** statement
   generated after the 14th of the following month; GSTR-2A is **dynamic**. Both
   are preserved separately here and never merged.
2. **GSP/ASP channel (optional dependency).** Programmatic download requires a
   **GSTN-approved GSP (GST Suvidha Provider)** together with an **ASP**, after
   enabling *Manage API Access* on the portal and authenticating with an
   **OTP-based session**. GSTN does not expose its APIs to arbitrary software.

**This build ships no live GSP integration.** The connector
(`app/services/gst_portal/connector.py`) is pluggable:

- `ManualImportConnector` (default) — refuses to "download" and directs the user to
  import the file manually. It never fabricates a successful download.
- `GspConnector` — the extension point for a real, authorised GSP integration. It
  is disabled unless configured and, without a tested integration + runtime OTP
  session, refuses honestly naming the GSP/ASP dependency.

Configuration lives in `app/core/config.py` (`gst_returns_mode`,
`gst_returns_gsp_*`). **The GST portal password is never stored**; a GSP uses a
runtime OTP session token. We do not scrape portal pages, bypass OTP/CAPTCHA, store
portal passwords or invent undocumented APIs.

**Manual fallback** is always available: upload the portal JSON (GSTR-2A/2B or an
electronic-ledger JSON) or the canonical Excel template
(`GET /api/gst/templates/gst-import`). Every import retains the **original bytes +
SHA-256** for audit; an identical re-import is rejected; a corrected file supersedes
the previous import for that period and both are kept with timestamps and history.

## 2. Reconciliation (purchase register vs 2A/2B)

`reconcile.py` matches the ITC register (books purchase invoices, captured
read-only — never modifying the invoice) against the latest imported portal
documents and classifies each row: `EXACT`, `INVOICE_FORMAT_DIFF`, `DATE_DIFF`,
`TAXABLE_DIFF`, `RATE_DIFF`, `HEAD_DIFF`, `MISSING_IN_BOOKS`, `MISSING_IN_2A`,
`MISSING_IN_2B`, `DUPLICATE`, `AMENDMENT`, `CREDIT_NOTE`, `DEBIT_NOTE`. Differences
are recorded for an authorised user to resolve **with a remark**; nothing is
silently changed.

## 3. ITC lifecycle (monthly register + carry-forward)

Each invoice carries a status: `BOOKED_NOT_IN_2B`, `IN_2A_NOT_IN_2B`,
`IN_2B_MATCHED`, `MISMATCH_UNDER_REVIEW`, `ITC_DEFERRED`, `ELIGIBLE_FOR_REVIEW`,
`ITC_CLAIMED`, `ITC_REVERSED`, `ITC_RECLAIMED`, `ITC_INELIGIBLE`. Status changes are
**append-only** (`gst_itc_status_history`). An invoice missing from a period's 2B is
kept intact with its ITC flagged separately and excluded from claimable ITC;
subsequent 2B periods are checked, and when it reappears it is matched to the
original books invoice and presented for authorised review. **ITC is never
auto-claimed** — the CA-approved policy governs claim treatment.

## 4. Reversal & reclaim

`gst_itc_reversal_reclaim` is an append-only, linked lifecycle: original credit →
reversal (reason, period, reference) → reclaim (linked to the reversal). Partial
reversal and partial reclaim are supported; a reclaim can never exceed the
net-reversed balance and a duplicate reclaim reference is rejected (DB-enforced).
Accounting corrections post through the existing `gst_adjustment` engine **only when
`authorize=True`**.

## 5. Cross-utilisation

`utilisation.py` follows a **versioned, CA-approved policy** (`gst_policy_versions`)
for the legal set-off order; **prohibited CGST↔SGST cross-utilisation is rejected**
unless a policy version explicitly permits it. A proposal is previewed, CA-approved,
then posted (one balanced voucher through the existing engine) **only when
authorised**. A plain journal transfer is not treated as valid statutory
utilisation.

## 6. Electronic-ledger reconciliation

`ledger_recon.py` reconciles **portal** electronic credit/cash/liability balances
(imported, never fabricated) against **ERP** GST account balances, reporting both
sides side by side and clearly distinguished.

## 7. Reports

`reports.py`: 2A/2B vs purchase register, month-wise ITC available / deferred /
mismatched / claimed / reversed / reclaimed, ITC eligible for subsequent-period
review, cross-adjustments, supplier-wise missing / mismatch, period-wise unresolved
differences — all exportable to Excel and PDF.

## 8. Security, audit & isolation

Every import, reconciliation, resolution, ITC tag, reversal, reclaim and adjustment
records company/GSTIN, period, user, timestamp, source file + SHA-256, references,
status transitions and approval identity via the existing audit log and RBAC. GST
data is strictly company-scoped (a GSTIN not registered to the acting company is
refused), and the DB-enforced unique indexes are proven under real PostgreSQL
concurrency.

## RBAC

- `gst.read` — view imports, register, reconciliation, reports.
- `gst.itc_manage` — import, reconcile, tag, defer/claim, resolve differences.
- `gst.itc_post` — CA-gated posting: policy approval, ITC reversal/reclaim GL
  posting, and credit cross-utilisation posting.

## Data model (migration `0035_gst_itc_module`, additive)

`gst_return_imports`, `gst_portal_documents`, `gst_itc_invoice_status`,
`gst_itc_reco_runs`, `gst_itc_reco_matches`, `gst_itc_status_history`,
`gst_itc_reversal_reclaim`, `gst_policy_versions`, `gst_cross_adjustments`,
`gst_electronic_ledger_balances`.
