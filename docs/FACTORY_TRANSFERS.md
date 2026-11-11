# Same-Company Factory Transfers (Phase 8)

**Date:** 2026-11-11 · **Branch:** `feat/multi-company-production-ready`

Implements business requirement **I**: moving stock between two factories of the
**same company**, via a goods-in-transit workflow, with **no inter-factory
invoice** (the brief forbids artificial sales/purchase documents between a
company's own factories).

---

## 1. Model & lifecycle

`factory_stock_transfers` (migration `0007`, additive). Status workflow:

```
REQUESTED --authorize--> AUTHORIZED --dispatch--> DISPATCHED --receive--> RECEIVED
         \--cancel--> CANCELLED (only before dispatch)
```

- **REQUESTED** — a transfer request for an item + qty from a source warehouse
  (factory A) to a destination warehouse (factory B), same company, A ≠ B.
- **AUTHORIZED** — a checker approves it. **Maker-checker:** the requester cannot
  authorize their own transfer.
- **DISPATCHED** — the quantity is **consumed from the source warehouse** at FIFO
  cost (`FACTORY_TRANSFER_OUT`); the value is *in transit* (left source, not yet
  at destination) and recorded on the transfer (`dispatched_qty`, `unit_cost`).
- **RECEIVED** — the accepted quantity (received − rejected) is **received into the
  destination warehouse** at the dispatched unit cost (`FACTORY_TRANSFER_IN`).

## 2. GL-neutral

No voucher is posted — a factory transfer only moves the warehouse/factory
dimension of the inventory sub-ledger. Verified by a test asserting no
`source_module = "factory_transfer"` voucher exists after a full cycle.

## 3. Discrepancies — flagged, never silently written off

- **Quantity shortage** (`dispatched − received > 0`) and **quality rejection**
  (`rejected_qty > 0`) are recorded, and the transfer is flagged
  `needs_reconciliation = true`.
- How a transit loss or a rejected quantity is accounted (write-off account, who
  approves) is a **business decision** and is **not** auto-posted here. The flag
  surfaces these for a reconciliation step to be defined with finance.

## 4. Validation (fail-closed)

- Both warehouses must belong to the acting company and be assigned to a factory.
- Source and destination factories must differ (else use a plain intra-factory
  warehouse transfer).
- State transitions are enforced (cannot dispatch before authorize, receive before
  dispatch); received ≤ dispatched; rejected ≤ received.

## 5. API & permissions

| Method | Path | Permission |
|---|---|---|
| GET | `/api/transfers/factory` | `report.read` |
| POST | `/api/transfers/factory` | `transfer.manage` |
| POST | `/api/transfers/factory/{id}/authorize` | `transfer.authorize` (checker) |
| POST | `/api/transfers/factory/{id}/dispatch` | `transfer.manage` |
| POST | `/api/transfers/factory/{id}/receive` | `transfer.manage` |
| POST | `/api/transfers/factory/{id}/cancel` | `transfer.manage` |

Seeded: `transfer.manage` → PRODUCTION_MANAGER (+ `*`); `transfer.authorize` →
GENERAL_MANAGER (+ `*`).

## 6. Not done (business-gated)

- Transit-loss / rejection accounting posting (needs the write-off policy).
- A dedicated in-transit *warehouse* per factory (currently in-transit value is
  held on the transfer record; stock-on-hand correctly shows it has left the
  source and is not yet at the destination).
