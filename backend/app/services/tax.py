"""GST computation from a configurable tax code.

Intra-state supply splits into CGST + SGST; inter-state uses IGST. Rates come
from the ``tax_codes`` row (never hardcoded). All amounts are Decimal and
quantised to the money scale.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.types import MONEY_SCALE, quantize
from app.models.masters import TaxCode


class GstStateError(ValueError):
    """Place of supply cannot be determined — a required GST state code is missing."""


def resolve_intra_state(seller_state_code: str | None, buyer_state_code: str | None,
                        *, context: str = "GST") -> bool:
    """True for an intra-state supply (CGST+SGST), False for inter-state (IGST).

    Fails closed: if either state code is missing, the CGST/SGST-vs-IGST split
    cannot be decided safely, so we raise rather than silently assume intra-state.
    """
    s = (seller_state_code or "").strip()
    b = (buyer_state_code or "").strip()
    if not s or not b:
        raise GstStateError(
            f"{context}: both seller and buyer GST state codes are required to determine "
            "CGST/SGST vs IGST (set the state code on the company and the party)")
    return s == b


def compute_gst(tax_code: TaxCode | None, taxable, *, intra_state: bool) -> dict:
    taxable = quantize(taxable, MONEY_SCALE)
    if tax_code is None:
        return {"cgst": Decimal("0.00"), "sgst": Decimal("0.00"), "igst": Decimal("0.00"),
                "total_tax": Decimal("0.00"), "total": taxable}

    def pct(rate) -> Decimal:
        return quantize(taxable * Decimal(str(rate)) / Decimal("100"), MONEY_SCALE)

    if intra_state:
        cgst, sgst, igst = pct(tax_code.cgst_rate), pct(tax_code.sgst_rate), Decimal("0.00")
    else:
        cgst, sgst, igst = Decimal("0.00"), Decimal("0.00"), pct(tax_code.igst_rate)

    total_tax = cgst + sgst + igst
    return {"cgst": cgst, "sgst": sgst, "igst": igst, "total_tax": total_tax,
            "total": taxable + total_tax}
