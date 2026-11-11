"""GST adjustment vouchers: ITC reversal and reverse-charge (RCM) self-assessment.

* ITC reversal — give back input credit wrongly or no longer eligible:
    Dr Reversal expense     Cr Input CGST / SGST / IGST
* ITC reclaim — re-take credit earlier reversed (e.g. supplier paid within the
  time limit, or returned goods re-received) — the mirror of a reversal:
    Dr Input CGST / SGST / IGST     Cr Reversal expense
* RCM self-assessment — the recipient accounts for the supplier's GST, creating
  both the output liability and the matching input credit (net-nil to P&L):
    Dr Input CGST / SGST / IGST     Cr Output CGST / SGST / IGST
* Round off — the small rounding difference on a tax total, booked against a
  Rounding Off account:
    amount > 0 (collected / liability up):  Dr counter   Cr Rounding Off
    amount < 0 (given up / liability down):  Dr Rounding Off   Cr counter
* Manual — an explicit two-account adjustment between GST / expense accounts.

All amounts are Decimal, every voucher balances, and posting is idempotent on
the reference.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.services.accounting import posting_engine as pe
from app.services.accounting.posting_rules import resolve_account

MZERO = quantize(0, MONEY_SCALE)


class GSTAdjustmentError(Exception):
    """An invalid GST adjustment request."""


def _D(v) -> Decimal:
    return quantize(Decimal(str(v or 0)), MONEY_SCALE)


def post_itc_reversal(db: Session, *, company_id: uuid.UUID, vdate: date, reference: str,
                      cgst=0, sgst=0, igst=0, expense_account: str | None = None,
                      narration: str | None = None, created_by: uuid.UUID | None = None):
    ref = f"GADJ:ITCREV:{reference}"
    existing = pe.find_existing(db, company_id, ref)
    if existing is not None:
        return existing
    cgst, sgst, igst = _D(cgst), _D(sgst), _D(igst)
    total = cgst + sgst + igst
    if total <= 0:
        raise GSTAdjustmentError("ITC reversal needs a positive tax amount")
    exp = expense_account or resolve_account(db, company_id, "GST_ADJUSTMENT", "reversal_expense")
    lines = [pe.PostingLine(exp, debit=total, narration="ITC reversal")]
    if cgst > 0:
        lines.append(pe.PostingLine(resolve_account(db, company_id, "GST_ADJUSTMENT", "input_cgst"), credit=cgst, narration="Reverse input CGST"))
    if sgst > 0:
        lines.append(pe.PostingLine(resolve_account(db, company_id, "GST_ADJUSTMENT", "input_sgst"), credit=sgst, narration="Reverse input SGST"))
    if igst > 0:
        lines.append(pe.PostingLine(resolve_account(db, company_id, "GST_ADJUSTMENT", "input_igst"), credit=igst, narration="Reverse input IGST"))
    return pe.post_voucher(db, pe.PostingRequest(
        company_id=company_id, voucher_type_code="GADJ", voucher_date=vdate, posting_reference=ref,
        narration=narration or "ITC reversal", source_module="gst_adjustment", created_by=created_by,
        lines=lines,
    ))


def post_itc_reclaim(db: Session, *, company_id: uuid.UUID, vdate: date, reference: str,
                     cgst=0, sgst=0, igst=0, expense_account: str | None = None,
                     narration: str | None = None, created_by: uuid.UUID | None = None):
    """Re-take input credit earlier reversed — the mirror of an ITC reversal."""
    ref = f"GADJ:ITCRECL:{reference}"
    existing = pe.find_existing(db, company_id, ref)
    if existing is not None:
        return existing
    cgst, sgst, igst = _D(cgst), _D(sgst), _D(igst)
    total = cgst + sgst + igst
    if total <= 0:
        raise GSTAdjustmentError("ITC reclaim needs a positive tax amount")
    exp = expense_account or resolve_account(db, company_id, "GST_ADJUSTMENT", "reversal_expense")
    lines = []
    if cgst > 0:
        lines.append(pe.PostingLine(resolve_account(db, company_id, "GST_ADJUSTMENT", "input_cgst"), debit=cgst, narration="Reclaim input CGST"))
    if sgst > 0:
        lines.append(pe.PostingLine(resolve_account(db, company_id, "GST_ADJUSTMENT", "input_sgst"), debit=sgst, narration="Reclaim input SGST"))
    if igst > 0:
        lines.append(pe.PostingLine(resolve_account(db, company_id, "GST_ADJUSTMENT", "input_igst"), debit=igst, narration="Reclaim input IGST"))
    lines.append(pe.PostingLine(exp, credit=total, narration="ITC reclaim"))
    return pe.post_voucher(db, pe.PostingRequest(
        company_id=company_id, voucher_type_code="GADJ", voucher_date=vdate, posting_reference=ref,
        narration=narration or "ITC reclaim", source_module="gst_adjustment", created_by=created_by,
        lines=lines,
    ))


def post_round_off(db: Session, *, company_id: uuid.UUID, vdate: date, reference: str,
                   amount, counter_account: str | None = None, narration: str | None = None,
                   created_by: uuid.UUID | None = None):
    """Book a GST rounding difference against the Rounding Off account.

    ``amount`` is signed: positive is a gain (Dr counter / Cr Rounding Off),
    negative is a cost (Dr Rounding Off / Cr counter)."""
    ref = f"GADJ:ROUND:{reference}"
    existing = pe.find_existing(db, company_id, ref)
    if existing is not None:
        return existing
    amt = _D(amount)
    if amt == 0:
        raise GSTAdjustmentError("Round-off amount cannot be zero")
    round_acct = resolve_account(db, company_id, "GST_ADJUSTMENT", "round_off")
    counter = counter_account or resolve_account(db, company_id, "GST_ADJUSTMENT", "round_off_counter")
    mag = quantize(abs(amt), MONEY_SCALE)
    if amt > 0:
        lines = [pe.PostingLine(counter, debit=mag, narration="Round off (Dr counter)"),
                 pe.PostingLine(round_acct, credit=mag, narration="Rounding off")]
    else:
        lines = [pe.PostingLine(round_acct, debit=mag, narration="Rounding off"),
                 pe.PostingLine(counter, credit=mag, narration="Round off (Cr counter)")]
    return pe.post_voucher(db, pe.PostingRequest(
        company_id=company_id, voucher_type_code="GADJ", voucher_date=vdate, posting_reference=ref,
        narration=narration or "GST round off", source_module="gst_adjustment", created_by=created_by,
        lines=lines,
    ))


def post_rcm(db: Session, *, company_id: uuid.UUID, vdate: date, reference: str,
             cgst=0, sgst=0, igst=0, narration: str | None = None,
             created_by: uuid.UUID | None = None):
    """Reverse-charge self-assessment: book output liability and input credit."""
    ref = f"GADJ:RCM:{reference}"
    existing = pe.find_existing(db, company_id, ref)
    if existing is not None:
        return existing
    cgst, sgst, igst = _D(cgst), _D(sgst), _D(igst)
    if cgst + sgst + igst <= 0:
        raise GSTAdjustmentError("RCM needs a positive tax amount")
    lines = []
    for amt, in_slot, out_slot in (
        (cgst, "input_cgst", "output_cgst"),
        (sgst, "input_sgst", "output_sgst"),
        (igst, "input_igst", "output_igst"),
    ):
        if amt > 0:
            lines.append(pe.PostingLine(resolve_account(db, company_id, "GST_ADJUSTMENT", in_slot),
                                        debit=amt, narration="RCM input credit"))
            lines.append(pe.PostingLine(resolve_account(db, company_id, "GST_ADJUSTMENT", out_slot),
                                        credit=amt, narration="RCM output liability"))
    return pe.post_voucher(db, pe.PostingRequest(
        company_id=company_id, voucher_type_code="GADJ", voucher_date=vdate, posting_reference=ref,
        narration=narration or "RCM self-assessment", source_module="gst_adjustment",
        created_by=created_by, lines=lines,
    ))


def post_manual(db: Session, *, company_id: uuid.UUID, vdate: date, reference: str,
                debit_account: str, credit_account: str, amount,
                narration: str | None = None, created_by: uuid.UUID | None = None):
    ref = f"GADJ:MAN:{reference}"
    existing = pe.find_existing(db, company_id, ref)
    if existing is not None:
        return existing
    amt = _D(amount)
    if amt <= 0:
        raise GSTAdjustmentError("Adjustment amount must be positive")
    return pe.post_voucher(db, pe.PostingRequest(
        company_id=company_id, voucher_type_code="GADJ", voucher_date=vdate, posting_reference=ref,
        narration=narration or "GST adjustment", source_module="gst_adjustment", created_by=created_by,
        lines=[pe.PostingLine(debit_account, debit=amt, narration="GST adjustment (Dr)"),
               pe.PostingLine(credit_account, credit=amt, narration="GST adjustment (Cr)")],
    ))
