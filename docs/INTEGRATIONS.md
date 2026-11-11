# External Integrations — honest classification (Phase 12)

**Date:** 2026-11-11 · **Branch:** `feat/multi-company-production-ready`

Every external integration is classified as **LIVE**, **TESTED SANDBOX**,
**SIMULATED**, or **NOT IMPLEMENTED**. No simulator is presented as a real
hardware or government integration. "Configurable real adapter" means a code seam
exists for the live endpoint but it has **not** been exercised against the real
service in this repository.

---

## Classification

| Integration | Status | Evidence / notes |
|---|---|---|
| **Color Service dispensing** | **SIMULATED** | `services/colorservice/adapter.py` — `ColorServiceAdapter` protocol + `ColorServiceSimulator` (deterministic acks). Idempotent ingest, `integration_events` audit. No live SOAP/OPC-UA/Modbus/DB adapter is wired. |
| **Knitting machine data** | **SIMULATED** | `services/hardware.py` — `KnittingMachineAdapter` protocol + simulator; idempotent ingest on external log id. No live machine feed. |
| **Weighing scales** | **SIMULATED** | `services/hardware.py` — `WeighingScaleAdapter` protocol + simulator; idempotent on external reading id. No live scale feed. |
| **Barcode / QR** | **PARTIAL** | Roll barcodes/QR are **generated** and printed on labels (implemented). Reading via a physical scanner is manual keyboard-wedge entry; a dedicated **scanner device** integration is **NOT IMPLEMENTED**. |
| **E-invoice (IRN)** | **SIMULATED** (+ configurable real GSP adapter, untested) | `services/einvoice/` — canonical NIC payload, `EInvoiceSimulator` (deterministic IRN, no network) + `GspAdapter` (httpx, auth token flow, **timeout** via `einvoice_timeout_seconds`, subscription-key header for WeP/Pinnacle). Idempotent IRN, cancel, `integration_events` audit. **No live IRP call has been made.** |
| **E-way bill** | **SIMULATED** (+ configurable real GSP adapter, untested) | `services/ewaybill/` — NIC EWB payload, `EWayBillSimulator` + `GspAdapter` reusing the e-invoice GSP connection, links to the IRN. Idempotent, cancel, audit. **No live EWB call.** |
| **GST / GSP** | **SIMULATED** | The GSP connection is the e-invoice/e-way adapter above. Returns calculations + **draft** GSTR-1 / GSTR-3B. **No government-portal filing** is performed or claimed. |
| **Bank feeds** | **PARTIAL** | CSV statement **import** + reconciliation implemented (`services/banking.py::import_statement_csv`). A **live bank API** feed is **NOT IMPLEMENTED**. |
| **Email & notifications** | **PARTIAL** | In-app notification feed implemented (broadcast on approvals, unread badge). **Email / SMTP delivery is NOT IMPLEMENTED** (no SMTP client in the codebase). |

---

## Adapter quality (what exists vs. hardening gaps)

Present on the GSP (e-invoice / e-way) path:
- **Protocol seam** (simulator ⇄ real adapter) so the live wiring is a config
  switch, not a rewrite.
- **Idempotency** — IRN/EWB generation is idempotent; ingest paths
  (dispensing, scale, machine) are idempotent on the external id.
- **Timeouts** — httpx client uses a configured timeout.
- **Audit trail** — `integration_events` records generate/cancel with the
  external reference.

Hardening gaps to close before any live use:
- **Retries / backoff** on transient GSP failures are **not** implemented (single
  attempt). Add bounded retry with jitter for the live path.
- **Failure-recovery / dead-letter** for a GSP call that times out after the
  server may have accepted it — reconcile via a status query before retrying, to
  avoid duplicate IRNs.
- **Circuit breaking / rate limiting** toward the GSP.
- **Secret handling** — GSP credentials and subscription keys must come from the
  environment / a secret store, never committed.

## Going live (per integration)

- **E-invoice / e-way:** provide the GSTIN's GSP credentials + endpoints in config,
  run the GSP's **sandbox** first (moves it to TESTED SANDBOX), add retries, then a
  controlled production pilot. Get GST calculations reviewed by a qualified GST
  professional before statutory use (see `docs/CONSOLIDATION.md` / GST notes).
- **Color Service / scales / machines:** implement the vendor's real protocol
  behind the existing adapter interface and validate on the actual hardware
  (moves from SIMULATED to LIVE only after on-hardware tests).
- **Bank feed / email:** implement the bank API client and an SMTP/email provider
  behind small adapter seams.

**Rule:** none of the SIMULATED items may be described as LIVE until validated
against the real service/hardware. This document is the source of truth for that
status.
