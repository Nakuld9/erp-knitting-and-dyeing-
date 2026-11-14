# Design preview assets — Hybrid Manufacturing / Job Work / Sales Documents

**These are DESIGN ARTEFACTS ONLY.** Every page is marked
**"DESIGN PREVIEW — SAMPLE DATA — NOT A TAX DOCUMENT"**. They are rendered with
clearly fictitious sample data for visual review of the layouts proposed in
[`../HYBRID_MANUFACTURING_JOB_WORK_DESIGN.md`](../HYBRID_MANUFACTURING_JOB_WORK_DESIGN.md)
(§21–§23). They are **not** application code, carry **no** transaction effect, and
were **not** produced by posting anything to a ledger or inventory.

## Files

| File | What it shows | Pages |
|---|---|---|
| `01_manufactured_invoice_rollwise` | Manufactured-fabric **GST Tax Invoice** with **roll-wise** detail (Lot, Roll ID, Fabric/Quality, GSM, Shade/Dye Lot, Weight, Length, Rate, Taxable, GST%) + GST summary, amount-in-words, bank, signatures | 1 |
| `02_challan_3roll` | Delivery challan — 3 rolls | 1 |
| `03_challan_6roll` | Delivery challan — 6 rolls (with Width + Length, transport box, 4 signatures) | 1 |
| `04_challan_20roll` | Delivery challan — 20 rolls | 2 |
| `05_challan_40roll_multipage` | Delivery challan — 40 rolls, **multi-page** (repeated headers, "(contd.)" running header, totals/transport/signatures on last page) | 2 |
| `06_challan_no_length` | Delivery challan with the optional **Length** column genuinely absent (omitted, not fabricated) | 1 |
| `07_challan_cancelled` | Delivery challan with the **CANCELLED** watermark | 1 |
| `08_packing_list` | Manufactured-fabric **Packing List** (per-roll lines, packages, net weight; no GST) | 1 |

Both `.pdf` (source) and `-N.png` (page images) are included.

## How these were produced

The invoice and challan previews use the project's **existing** Stage-3 print engine
(`backend/app/services/printing/pdf_sales.py` + builders), driven with sample
`PrintDocument` data — the "closest real renderer output" per the design brief. The
**packing list** renderer is **PROPOSED (not yet implemented)**; its preview reuses the
challan engine as the closest available real output to illustrate the intended layout.

> Note: at the time these previews were produced, the Stage-3 print engine existed in
> the working tree as an **uncommitted** format-correction change awaiting the owner's
> separate approval. These preview **images** are committed as design artefacts; the
> renderer source code is **not** part of this design commit.

## What still needs confirmation

- **Manufactured invoice roll-wise layout** is a proposed design; exact mandatory GST
  invoice fields/wording are a **CA-review** item (see design doc §19, §33).
- **Packing list** layout and whether it is required per customer is an open decision
  (design doc §23, §33).
- Sample values (amounts, lots, GSTINs) are fictitious and internally illustrative
  only.
