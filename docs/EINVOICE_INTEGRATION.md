# E-invoice (IRN) Integration

Generates a government **IRN** (Invoice Reference Number) and signed QR for a
sales invoice, through a GST Suvidha Provider (GSP) that fronts the NIC Invoice
Registration Portal (IRP). Built on the same pluggable pattern as the Color
Service: a canonical payload, an adapter, and a persisted audit trail — so it
works offline against a **simulator** today and talks to a real GSP once
credentials are configured.

## How it fits together

```
SalesInvoice ──▶ build_irn_payload() ──▶ adapter.generate_irn() ──▶ EInvoice row
  (posted)        canonical NIC JSON        simulator | GSP           (IRN, QR, Ack)
                                                 │
                                          integration_events (audit)
```

- **Payload** (`app/services/einvoice/payload.py`) builds the NIC e-invoice JSON
  (schema v1.1) from the invoice: seller/buyer GSTIN + state, item lines with
  HSN, and the CGST/SGST vs IGST split decided by comparing seller and buyer GST
  state codes. The IRP re-validates totals, so figures are computed consistently.
- **Adapter** (`app/services/einvoice/adapter.py`) — `EInvoiceSimulator` issues a
  deterministic 64-char IRN offline; `GspAdapter` authenticates to a GSP REST
  endpoint and posts the payload. `get_adapter()` picks one from config.
- **Service** (`app/services/einvoice/service.py`) — one IRN per invoice,
  idempotent (a repeat returns the existing IRN), persists the request/response
  and an `integration_events` record, and supports cancellation.
- **API** (`/api/einvoice/...`): `POST {invoice_id}/generate`,
  `POST {invoice_id}/cancel`, `GET {invoice_id}`, `GET /config`.
- **UI**: the Sales Invoice page shows an **E-invoice (IRN)** panel after posting
  — mode (simulator/live), Generate, the IRN/Ack/QR, and Cancel.

## Going live with your GSP

The credentials you already use (e.g. in Busy) are your GSTIN's **e-invoice API**
credentials, issued for a GSP such as **WeP**, **Tele-electrosoft** or
**Pinnacle**. Put them in the server environment (never in git, never in chat):

```bash
EINVOICE_GSP=gsp
EINVOICE_BASE_URL=https://api.<your-gsp>.example/einvoice
EINVOICE_AUTH_PATH=/auth           # token endpoint (GSP-specific)
EINVOICE_IRN_PATH=/invoice         # IRN generate endpoint
EINVOICE_CANCEL_PATH=/invoice/cancel
EINVOICE_CLIENT_ID=...             # GSP API key
EINVOICE_CLIENT_SECRET=...
EINVOICE_USERNAME=...              # e-invoice portal API user for the GSTIN
EINVOICE_PASSWORD=...
EINVOICE_GSTIN=03ABCDE1234F1Z5
```

The adapter does the common GSP REST flow: `POST auth → bearer token → POST
invoice`. **The one GSP-specific seam** is `GspAdapter._auth_token` /
`_auth_headers` and the response field names (`Irn`, `AckNo`, `AckDt`,
`SignedQRCode`) — most GSPs match these, but check your GSP's API doc and adjust
that single method if it differs (for example the NIC direct API needs an
app-key RSA/AES handshake rather than a plain token). Everything else — the
payload, storage, idempotency, UI — stays the same.

Start against the GSP's **sandbox** base URL, confirm an IRN comes back, then
switch the base URL to production.

## Data the IRP requires

For a real IRN (the simulator does not enforce these):

- **Company** (seller): `gstin`, `state_code`, `address` with a 6-digit PIN.
- **Customer** (buyer): `gstin`, `state_code`, `address` with a PIN. B2B only —
  a buyer without a GSTIN is B2C and is not e-invoiced.
- **Tax codes**: an `hsn_sac` on each line's tax code (falls back to a generic
  manufacturing HSN if absent, which a real IRP may reject).

A missing GSTIN or state code fails fast with a clear `422` at generate time, so
the gap is obvious before the IRP ever sees the document.

## Operational notes

- **Idempotent**: generating twice returns the same IRN; the invoice is never
  double-registered.
- **Cancellation** is allowed within the IRP's window (24h) and marks the
  `EInvoice` row `CANCELLED`; the invoice then needs a fresh document, not a
  re-generate.
- **Audit**: every exchange is in `integration_events` (and the request/response
  payloads on the `einvoices` row) for dispute resolution.
- **Security**: credentials live only in the environment; `GET /api/einvoice/config`
  tells the UI whether it is live without ever exposing them.
