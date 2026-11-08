"""Build the canonical NIC e-invoice (IRN) request payload from a sales invoice.

The schema here follows the government e-invoice JSON (schema v1.1) that every
GSP/IRP accepts — the GSP-specific part is only authentication and transport,
not the document shape. Intra-state supplies split tax into CGST+SGST; inter-
state into IGST, decided by comparing the seller's and buyer's GST state codes.

Monetary rounding uses the app's money scale; the IRP re-validates totals, so
the per-line and summary figures are computed consistently here.
"""
from __future__ import annotations

import re
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.commercial import SalesInvoice
from app.models.core import Company
from app.models.masters import Customer, TaxCode


class EInvoicePayloadError(Exception):
    """The invoice is missing data the IRP requires (GSTIN, HSN, ...)."""


def _d(v) -> Decimal:
    return quantize(Decimal(str(v or 0)), MONEY_SCALE)


def _pin(address: str | None, fallback: str = "999999") -> str:
    """Best-effort 6-digit PIN from a free-text address; the IRP needs one."""
    if address:
        m = re.search(r"\b(\d{6})\b", address)
        if m:
            return m.group(1)
    return fallback


def _dt(d) -> str:
    return d.strftime("%d/%m/%Y")


def build_irn_payload(db: Session, invoice: SalesInvoice) -> dict:
    company = db.get(Company, invoice.company_id)
    customer = db.get(Customer, invoice.customer_id)
    if company is None or not company.gstin:
        raise EInvoicePayloadError("Supplier (company) GSTIN is not set")
    if customer is None or not customer.gstin:
        raise EInvoicePayloadError("Buyer (customer) GSTIN is not set")

    seller_stcd = (company.state_code or (company.gstin[:2] if company.gstin else "") or "").strip()
    buyer_stcd = (customer.state_code or (customer.gstin[:2] if customer.gstin else "") or "").strip()
    if not seller_stcd or not buyer_stcd:
        raise EInvoicePayloadError("Seller/buyer GST state code is required")
    intra_state = seller_stcd == buyer_stcd

    items: list[dict] = []
    ass_val = cgst_val = sgst_val = igst_val = tot_inv_val = Decimal("0")

    for idx, ln in enumerate(sorted(invoice.lines, key=lambda x: x.line_no), start=1):
        tax = db.get(TaxCode, ln.tax_code_id) if ln.tax_code_id else None
        hsn = (tax.hsn_sac if tax else None) or "9988"  # 9988: job-work/manufacturing services
        rate_pct = _d(tax.igst_rate) if (tax and _d(tax.igst_rate) > 0) else (
            _d(tax.cgst_rate) + _d(tax.sgst_rate) if tax else Decimal("0"))

        taxable = _d(ln.amount)
        tax_amt = quantize(taxable * rate_pct / Decimal("100"), MONEY_SCALE)
        if intra_state:
            cgst = sgst = quantize(tax_amt / Decimal("2"), MONEY_SCALE)
            igst = Decimal("0")
        else:
            cgst = sgst = Decimal("0")
            igst = tax_amt
        line_total = taxable + cgst + sgst + igst

        ass_val += taxable
        cgst_val += cgst
        sgst_val += sgst
        igst_val += igst
        tot_inv_val += line_total

        items.append({
            "SlNo": str(idx),
            "PrdDesc": (ln.description or "Textile goods")[:300],
            "IsServc": "N",
            "HsnCd": hsn,
            "Qty": float(_d(ln.qty)),
            "Unit": "KGS",
            "UnitPrice": float(_d(ln.rate)),
            "TotAmt": float(taxable),
            "AssAmt": float(taxable),
            "GstRt": float(rate_pct),
            "IgstAmt": float(igst),
            "CgstAmt": float(cgst),
            "SgstAmt": float(sgst),
            "TotItemVal": float(line_total),
        })

    if not items:
        raise EInvoicePayloadError("Invoice has no lines")

    payload = {
        "Version": "1.1",
        "TranDtls": {"TaxSch": "GST", "SupTyp": "B2B", "RegRev": "N", "IgstOnIntra": "N"},
        "DocDtls": {"Typ": "INV", "No": invoice.number, "Dt": _dt(invoice.invoice_date)},
        "SellerDtls": {
            "Gstin": company.gstin,
            "LglNm": company.legal_name or company.name,
            "Addr1": (company.address or "NA")[:100],
            "Loc": (company.address or "NA")[:50],
            "Pin": _pin(company.address),
            "Stcd": seller_stcd,
        },
        "BuyerDtls": {
            "Gstin": customer.gstin,
            "LglNm": getattr(customer, "legal_name", None) or customer.name,
            "Pos": buyer_stcd,
            "Addr1": (customer.address or "NA")[:100],
            "Loc": (customer.address or "NA")[:50],
            "Pin": _pin(customer.address),
            "Stcd": buyer_stcd,
        },
        "ItemList": items,
        "ValDtls": {
            "AssVal": float(ass_val),
            "CgstVal": float(cgst_val),
            "SgstVal": float(sgst_val),
            "IgstVal": float(igst_val),
            "OthChrg": 0,
            "RndOffAmt": 0,
            "TotInvVal": float(tot_inv_val),
        },
    }
    return payload
