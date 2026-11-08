"""Indian GST reporting.

Sales & purchase registers, GSTR-1 and GSTR-3B summaries, and a
books-vs-registers reconciliation. Registers are built from POSTED source
documents (sales invoices, chemical GRNs) using the same configurable
``compute_gst`` the posting engine used, so the registers tie out to the GST
ledger accounts exactly. Rates are never hardcoded - they come from each line's
tax code.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.accounting import ChartOfAccount, JournalLine, Voucher, VoucherStatus
from app.models.commercial import (
    CreditNote,
    DebitNote,
    DocStatus,
    GoodsReceiptNote,
    SalesInvoice,
)
from app.models.core import Company
from app.models.masters import Customer, Supplier, TaxCode
from app.services.tax import compute_gst

ZERO = quantize(0, MONEY_SCALE)
_POSTED = [s.value for s in (VoucherStatus.POSTED, VoucherStatus.REVERSED, VoucherStatus.REVERSAL)]


def _intra(company: Company, state_code: str | None) -> bool:
    if company.state_code and state_code:
        return company.state_code == state_code
    return True


def _rate_of(tc: TaxCode | None) -> Decimal:
    if tc is None:
        return Decimal("0")
    return Decimal(str(tc.igst_rate)) or (Decimal(str(tc.cgst_rate)) + Decimal(str(tc.sgst_rate)))


# ------------------------------ registers ----------------------------------

def sales_register(db: Session, company_id: uuid.UUID, date_from: date, date_to: date) -> dict:
    company = db.get(Company, company_id)
    invoices = db.execute(
        select(SalesInvoice).where(
            SalesInvoice.company_id == company_id, SalesInvoice.status == DocStatus.POSTED,
            SalesInvoice.invoice_date >= date_from, SalesInvoice.invoice_date <= date_to,
        ).order_by(SalesInvoice.invoice_date, SalesInvoice.number)
    ).scalars().all()

    rows, tot = [], {"taxable": ZERO, "cgst": ZERO, "sgst": ZERO, "igst": ZERO, "total": ZERO}
    for inv in invoices:
        cust = db.get(Customer, inv.customer_id)
        intra = _intra(company, cust.state_code if cust else None)
        tx = {"taxable": ZERO, "cgst": ZERO, "sgst": ZERO, "igst": ZERO}
        for line in inv.lines:
            amount = quantize(Decimal(str(line.qty)) * Decimal(str(line.rate)), MONEY_SCALE)
            tc = db.get(TaxCode, line.tax_code_id) if line.tax_code_id else None
            g = compute_gst(tc, amount, intra_state=intra)
            tx["taxable"] += amount; tx["cgst"] += g["cgst"]; tx["sgst"] += g["sgst"]; tx["igst"] += g["igst"]
        total = tx["taxable"] + tx["cgst"] + tx["sgst"] + tx["igst"]
        rows.append({"document": inv.number, "date": inv.invoice_date,
                     "party": cust.name if cust else None, "gstin": cust.gstin if cust else None,
                     "place_of_supply": cust.state_code if cust else None,
                     "supply_type": "INTRA" if intra else "INTER", **tx, "total": total})
        for k in tx:
            tot[k] += tx[k]
        tot["total"] += total

    # Credit notes reduce outward supply (shown as negative rows).
    notes = db.execute(
        select(CreditNote).where(
            CreditNote.company_id == company_id, CreditNote.status == DocStatus.POSTED,
            CreditNote.note_date >= date_from, CreditNote.note_date <= date_to,
        ).order_by(CreditNote.note_date, CreditNote.number)
    ).scalars().all()
    for note in notes:
        cust = db.get(Customer, note.customer_id)
        intra = _intra(company, cust.state_code if cust else None)
        tx = {"taxable": ZERO, "cgst": ZERO, "sgst": ZERO, "igst": ZERO}
        for line in note.lines:
            amount = quantize(Decimal(str(line.qty)) * Decimal(str(line.rate)), MONEY_SCALE)
            tc = db.get(TaxCode, line.tax_code_id) if line.tax_code_id else None
            g = compute_gst(tc, amount, intra_state=intra)
            tx["taxable"] += amount; tx["cgst"] += g["cgst"]; tx["sgst"] += g["sgst"]; tx["igst"] += g["igst"]
        total = tx["taxable"] + tx["cgst"] + tx["sgst"] + tx["igst"]
        neg = {k: -v for k, v in tx.items()}
        rows.append({"document": note.number, "date": note.note_date,
                     "party": cust.name if cust else None, "gstin": cust.gstin if cust else None,
                     "place_of_supply": cust.state_code if cust else None,
                     "supply_type": ("INTRA" if intra else "INTER"), "doc_type": "CREDIT_NOTE",
                     **neg, "total": -total})
        for k in tx:
            tot[k] -= tx[k]
        tot["total"] -= total
    return {"date_from": date_from, "date_to": date_to, "rows": rows, "totals": tot}


def purchase_register(db: Session, company_id: uuid.UUID, date_from: date, date_to: date) -> dict:
    company = db.get(Company, company_id)
    grns = db.execute(
        select(GoodsReceiptNote).where(
            GoodsReceiptNote.company_id == company_id, GoodsReceiptNote.status == DocStatus.POSTED,
            GoodsReceiptNote.grn_date >= date_from, GoodsReceiptNote.grn_date <= date_to,
        ).order_by(GoodsReceiptNote.grn_date, GoodsReceiptNote.number)
    ).scalars().all()

    rows, tot = [], {"taxable": ZERO, "cgst": ZERO, "sgst": ZERO, "igst": ZERO, "total": ZERO}
    for grn in grns:
        sup = db.get(Supplier, grn.supplier_id)
        intra = _intra(company, sup.state_code if sup else None)
        tx = {"taxable": ZERO, "cgst": ZERO, "sgst": ZERO, "igst": ZERO}
        for line in grn.lines:
            amount = quantize(Decimal(str(line.qty)) * Decimal(str(line.unit_cost)), MONEY_SCALE)
            tc = db.get(TaxCode, line.tax_code_id) if line.tax_code_id else None
            g = compute_gst(tc, amount, intra_state=intra)
            tx["taxable"] += amount; tx["cgst"] += g["cgst"]; tx["sgst"] += g["sgst"]; tx["igst"] += g["igst"]
        total = tx["taxable"] + tx["cgst"] + tx["sgst"] + tx["igst"]
        rows.append({"document": grn.number, "date": grn.grn_date,
                     "party": sup.name if sup else None, "gstin": sup.gstin if sup else None,
                     "supply_type": "INTRA" if intra else "INTER", **tx, "total": total})
        for k in tx:
            tot[k] += tx[k]
        tot["total"] += total

    # Debit notes (purchase returns) reduce inward supply / ITC.
    notes = db.execute(
        select(DebitNote).where(
            DebitNote.company_id == company_id, DebitNote.status == DocStatus.POSTED,
            DebitNote.note_date >= date_from, DebitNote.note_date <= date_to,
        ).order_by(DebitNote.note_date, DebitNote.number)
    ).scalars().all()
    for note in notes:
        sup = db.get(Supplier, note.supplier_id)
        intra = _intra(company, sup.state_code if sup else None)
        tx = {"taxable": ZERO, "cgst": ZERO, "sgst": ZERO, "igst": ZERO}
        for line in note.lines:
            amount = quantize(Decimal(str(line.cost_value or 0)), MONEY_SCALE)
            tc = db.get(TaxCode, line.tax_code_id) if line.tax_code_id else None
            g = compute_gst(tc, amount, intra_state=intra)
            tx["taxable"] += amount; tx["cgst"] += g["cgst"]; tx["sgst"] += g["sgst"]; tx["igst"] += g["igst"]
        total = tx["taxable"] + tx["cgst"] + tx["sgst"] + tx["igst"]
        neg = {k: -v for k, v in tx.items()}
        rows.append({"document": note.number, "date": note.note_date,
                     "party": sup.name if sup else None, "gstin": sup.gstin if sup else None,
                     "supply_type": ("INTRA" if intra else "INTER"), "doc_type": "DEBIT_NOTE",
                     **neg, "total": -total})
        for k in tx:
            tot[k] -= tx[k]
        tot["total"] -= total
    return {"date_from": date_from, "date_to": date_to, "rows": rows, "totals": tot}


# ------------------------------ GSTR-1 --------------------------------------

def gstr1_summary(db: Session, company_id: uuid.UUID, date_from: date, date_to: date) -> dict:
    """Outward supplies split B2B/B2C and summarised rate-wise and HSN-wise."""
    company = db.get(Company, company_id)
    invoices = db.execute(
        select(SalesInvoice).where(
            SalesInvoice.company_id == company_id, SalesInvoice.status == DocStatus.POSTED,
            SalesInvoice.invoice_date >= date_from, SalesInvoice.invoice_date <= date_to,
        )
    ).scalars().all()

    b2b = {"taxable": ZERO, "cgst": ZERO, "sgst": ZERO, "igst": ZERO, "invoices": 0}
    b2c = {"taxable": ZERO, "cgst": ZERO, "sgst": ZERO, "igst": ZERO, "invoices": 0}
    by_rate: dict[str, dict] = {}
    by_hsn: dict[str, dict] = {}

    for inv in invoices:
        cust = db.get(Customer, inv.customer_id)
        intra = _intra(company, cust.state_code if cust else None)
        bucket = b2b if (cust and cust.gstin) else b2c
        bucket["invoices"] += 1
        for line in inv.lines:
            amount = quantize(Decimal(str(line.qty)) * Decimal(str(line.rate)), MONEY_SCALE)
            tc = db.get(TaxCode, line.tax_code_id) if line.tax_code_id else None
            g = compute_gst(tc, amount, intra_state=intra)
            for k in ("cgst", "sgst", "igst"):
                bucket[k] += g[k]
            bucket["taxable"] += amount

            rate = format(_rate_of(tc).normalize(), "f")  # e.g. "18", "2.5"
            r = by_rate.setdefault(rate, {"taxable": ZERO, "cgst": ZERO, "sgst": ZERO, "igst": ZERO})
            hsn = (tc.hsn_sac if tc and tc.hsn_sac else "NA")
            h = by_hsn.setdefault(hsn, {"taxable": ZERO, "cgst": ZERO, "sgst": ZERO, "igst": ZERO})
            for agg in (r, h):
                agg["taxable"] += amount; agg["cgst"] += g["cgst"]; agg["sgst"] += g["sgst"]; agg["igst"] += g["igst"]

    return {"date_from": date_from, "date_to": date_to, "b2b": b2b, "b2c": b2c,
            "rate_wise": by_rate, "hsn_wise": by_hsn}


# ------------------------------ GSTR-3B -------------------------------------

def gstr3b_summary(db: Session, company_id: uuid.UUID, date_from: date, date_to: date) -> dict:
    out = sales_register(db, company_id, date_from, date_to)["totals"]
    inp = purchase_register(db, company_id, date_from, date_to)["totals"]

    def net(o, i):
        return o - i  # negative => input credit carried forward

    payable = {
        "cgst": net(out["cgst"], inp["cgst"]),
        "sgst": net(out["sgst"], inp["sgst"]),
        "igst": net(out["igst"], inp["igst"]),
    }
    payable["total"] = payable["cgst"] + payable["sgst"] + payable["igst"]
    return {
        "date_from": date_from, "date_to": date_to,
        "outward_taxable_supplies": {"taxable_value": out["taxable"], "cgst": out["cgst"],
                                     "sgst": out["sgst"], "igst": out["igst"]},
        "eligible_itc": {"cgst": inp["cgst"], "sgst": inp["sgst"], "igst": inp["igst"]},
        "net_tax_payable": payable,
    }


# -------------------------- reconciliation ----------------------------------

def _account_period(db: Session, company_id: uuid.UUID, code: str, date_from: date, date_to: date) -> tuple:
    acct = db.execute(
        select(ChartOfAccount).where(ChartOfAccount.company_id == company_id, ChartOfAccount.code == code)
    ).scalar_one_or_none()
    if acct is None:
        return ZERO, ZERO
    d = c = ZERO
    for line, voucher in db.execute(
        select(JournalLine, Voucher).join(Voucher, JournalLine.voucher_id == Voucher.id).where(
            Voucher.company_id == company_id, Voucher.status.in_(_POSTED),
            JournalLine.account_id == acct.id,
            Voucher.voucher_date >= date_from, Voucher.voucher_date <= date_to,
        )
    ):
        d += line.debit or ZERO
        c += line.credit or ZERO
    return d, c


def book_vs_gst_reconciliation(db: Session, company_id: uuid.UUID, date_from: date, date_to: date) -> dict:
    """Compare register tax totals with the GST ledger account movements."""
    sales = sales_register(db, company_id, date_from, date_to)["totals"]
    purch = purchase_register(db, company_id, date_from, date_to)["totals"]

    # Output tax = credit side of 2041/2042/2043; input tax = debit side of 1121/1122/1123.
    checks = []
    all_ok = True
    for label, code, register_amt, side in [
        ("Output CGST", "2041", sales["cgst"], "credit"),
        ("Output SGST", "2042", sales["sgst"], "credit"),
        ("Output IGST", "2043", sales["igst"], "credit"),
        ("Input CGST", "1121", purch["cgst"], "debit"),
        ("Input SGST", "1122", purch["sgst"], "debit"),
        ("Input IGST", "1123", purch["igst"], "debit"),
    ]:
        d, c = _account_period(db, company_id, code, date_from, date_to)
        book = c if side == "credit" else d
        diff = book - register_amt
        ok = diff == ZERO
        all_ok = all_ok and ok
        checks.append({"head": label, "account": code, "register": register_amt,
                       "books": book, "difference": diff, "matched": ok})
    return {"date_from": date_from, "date_to": date_to, "checks": checks, "is_reconciled": all_ok}
