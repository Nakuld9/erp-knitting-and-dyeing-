"""Sample document used to demonstrate and test the printing framework.

This is NOT a business document — it carries no real ledger/inventory data. It
exercises every part of the engine (letterhead, parties, line items with UOM,
tax, totals, references, signatures, watermarks) so the framework can be previewed
and tested before any individual business document is built on top of it.
"""
from __future__ import annotations

from app.services.printing.document import (
    DocumentColumn, DocumentParty, PrintDocument, SignatureBlock,
)
from app.services.printing.numbering import build_letterhead


def sample_print_document(db, company_id, *, number: str, date: str,
                          is_preview: bool = False, is_cancelled: bool = False,
                          reprint_count: int = 0) -> PrintDocument:
    lh = build_letterhead(db, company_id)
    return PrintDocument(
        title="SAMPLE DOCUMENT",
        document_kind="Print-framework demonstration",
        letterhead=lh,
        number=number,
        date=date,
        parties=[
            DocumentParty("Bill To", "Sample Customer", ["Plot 1, Industrial Area",
                                                         "GSTIN: 03AAAAA0000A1Z5", "State: 03"]),
            DocumentParty("Ship To", "Sample Customer — Unit B", ["Warehouse Rd", "State: 03"]),
            DocumentParty("Factory", "Sample Factory Unit", ["Knitting & Dyeing"]),
        ],
        columns=[
            DocumentColumn("#", "sl", width=0.4),
            DocumentColumn("Description", "desc", width=2.4),
            DocumentColumn("HSN", "hsn", width=0.8),
            DocumentColumn("Qty", "qty", numeric=True),
            DocumentColumn("UOM", "uom", width=0.6),
            DocumentColumn("Rate", "rate", numeric=True),
            DocumentColumn("Amount", "amount", numeric=True),
        ],
        rows=[
            {"sl": 1, "desc": "Single Jersey 160 GSM — Navy", "hsn": "6006",
             "qty": "60.000", "uom": "KG", "rate": "400.00", "amount": "24,000.00"},
            {"sl": 2, "desc": "Single Jersey 160 GSM — Navy", "hsn": "6006",
             "qty": "40.000", "uom": "KG", "rate": "400.00", "amount": "16,000.00"},
        ],
        tax_lines=[("Taxable value", "40,000.00"), ("CGST 9%", "3,600.00"),
                   ("SGST 9%", "3,600.00")],
        totals=[("Grand Total", "47,200.00")],
        references=[("PO Ref", "SAMPLE/PO/001"), ("Transport", "Sample Carrier")],
        signatures=[SignatureBlock("Prepared by"),
                    SignatureBlock("Authorised Signatory", for_line=f"For {lh.legal_name}")],
        notes=["Amount chargeable (in words): Forty-seven thousand two hundred rupees only.",
               "Sample content for the printing framework — not a financial record."],
        footer_note="Computer-generated sample document — printing created no transaction.",
        is_preview=is_preview, is_cancelled=is_cancelled, reprint_count=reprint_count,
    )
