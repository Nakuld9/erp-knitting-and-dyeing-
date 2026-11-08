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

### Webtel Electrosoft / Pinnacle Finserv (and similar GSPs)

These GSPs' API specs are **issued to registered clients** (portal login /
integration guide), not published, so the exact endpoint paths, auth style and
field names must come from *your* GSP account. There are two shapes in the wild:

1. **Simplified REST** — the GSP handles the NIC crypto server-side; you send
   plain JSON with an API **subscription key** header and get a token. This is
   what `GspAdapter` does. Set `EINVOICE_SUBSCRIPTION_KEY` and, if the header is
   not `subscription-key`, `EINVOICE_SUBSCRIPTION_HEADER` (Webtel/TaxPro often
   use a key/aspid-style header — confirm the exact name in your guide).
2. **NIC-style encrypted handshake** — you RSA-encrypt an app-key with the IRP
   public key, receive a session key (SEK), AES-256 encrypt the payload and
   decrypt the response with the SEK. If your GSP passes this through, that
   crypto is the one piece to add in `GspAdapter` (it needs a crypto library and
   can only be validated against the GSP sandbox).

**Ask your GSP (Webtel / Pinnacle) for their _API / ERP integration guide_ and
_sandbox credentials_**, specifically:
- Base URL (sandbox + production) and the auth / generate / cancel paths.
- Auth style: token in JSON, or the RSA/AES/SEK handshake (and the public-key
  `.pem` if so).
- The subscription-key header name, and any `client_id` / `client_secret`.
- The response field names for the IRN, Ack No, Ack Date and Signed QR.

With those four answers the simplified-REST path is pure configuration; only the
encrypted-handshake style needs the one code addition noted above. The
credentials you already use in Busy are the same GSTIN API credentials — reuse
them, but put them in the server environment, never in Busy-style config files
in the repo.

## Data the IRP requires

For a real IRN (the simulator does not enforce these):

- **Company** (seller): `gstin`, `state_code`, `address` with a 6-digit PIN.
- **Customer** (buyer): `gstin`, `state_code`, `address` with a PIN. B2B only —
  a buyer without a GSTIN is B2C and is not e-invoiced.
- **Tax codes**: an `hsn_sac` on each line's tax code (falls back to a generic
  manufacturing HSN if absent, which a real IRP may reject).

A missing GSTIN or state code fails fast with a clear `422` at generate time, so
the gap is obvious before the IRP ever sees the document.

## E-way bill

The e-way bill (EWB) reuses the **same GSP connection** (base URL, credentials,
subscription key) — only the endpoint paths differ (`EWB_PATH`,
`EWB_CANCEL_PATH`). It is generated from the invoice **plus transport details**
(mode, vehicle no., distance, optional transporter id) and, when the invoice
already has an IRN, **links to it** automatically. Mirrors e-invoice: canonical
NIC payload, simulator + GSP adapter, idempotent generate, cancel, and an
`integration_events` audit trail.

- API: `POST /api/ewaybill/{invoice_id}/generate` (transport body),
  `POST /api/ewaybill/{invoice_id}/cancel`, `GET /api/ewaybill/{invoice_id}`,
  `GET /api/ewaybill/config`.
- UI: an **E-way bill** panel on the Sales Invoice page (transport form →
  Generate → EWB no + validity → Cancel).
- Mandatory above the ₹50,000 consignment threshold (`EWB_THRESHOLD`).
- The live EWB response field names (`ewayBillNo`, `validUpto`) follow the NIC
  EWB API; adjust in `EWayBillGspAdapter.generate_ewb` if your GSP differs — the
  same single seam as e-invoice.

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
