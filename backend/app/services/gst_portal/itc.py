"""ITC lifecycle: defer, carry-forward review, claim, reversal and reclaim.

The monthly ITC register (``ItcInvoiceStatus``) tracks each purchase invoice's ITC
status. This service drives the lifecycle transitions — all append-only (an
``ItcStatusHistory`` row per change) and non-destructive:

* defer / mark ineligible / claim (claim never auto-happens — it is an explicit,
  authorised action; deferred credit is excluded from claimable ITC);
* reversal and reclaim, with partials and strict duplicate-reclaim prevention (a
  reclaim can never exceed the net-reversed balance), each linked to the invoice and
  optionally posted through the EXISTING ``gst_adjustment`` engine — but statutory
  GL posting stays disabled unless ``authorize=True`` is explicitly supplied.

No earlier reversal or reclaim is ever deleted or overwritten.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.gst_itc import (
    ItcAction,
    ItcInvoiceStatus,
    ItcReversalReclaim,
    ItcStatus,
    ItcStatusHistory,
)
from app.services.accounting import gst_adjustment as gadj
from app.services.audit import record_audit
from app.services.gst_portal.common import (
    MZERO,
    TAX_HEADS,
    GstItcError,
    GstItcStateError,
    m,
    now,
    period_valid,
    record_status,
)

_CLAIMABLE_FROM = {ItcStatus.IN_2B_MATCHED.value, ItcStatus.ELIGIBLE_FOR_REVIEW.value}
_REVERSIBLE_FROM = {ItcStatus.ITC_CLAIMED.value, ItcStatus.IN_2B_MATCHED.value,
                    ItcStatus.ELIGIBLE_FOR_REVIEW.value, ItcStatus.ITC_RECLAIMED.value,
                    ItcStatus.ITC_REVERSED.value}


def _get(db, company_id, invoice_id) -> ItcInvoiceStatus:
    inv = db.get(ItcInvoiceStatus, invoice_id)
    if inv is None or inv.company_id != company_id:
        raise GstItcError("ITC invoice not found")
    return inv


def _vdate(period: str) -> date:
    return date(int(period[2:]), int(period[:2]), 1)


def register(db: Session, company_id: uuid.UUID, *, status: str | None = None,
             period: str | None = None, limit: int = 200, offset: int = 0):
    stmt = select(ItcInvoiceStatus).where(ItcInvoiceStatus.company_id == company_id)
    if status:
        stmt = stmt.where(ItcInvoiceStatus.status == status)
    if period:
        stmt = stmt.where(ItcInvoiceStatus.booked_period == period)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = db.execute(stmt.order_by(ItcInvoiceStatus.booked_period, ItcInvoiceStatus.created_at)
                      .limit(max(1, min(limit, 1000))).offset(max(0, offset))).scalars().all()
    return rows, total


def history(db: Session, company_id: uuid.UUID, invoice_id: uuid.UUID):
    inv = _get(db, company_id, invoice_id)
    rows = db.execute(select(ItcStatusHistory).where(
        ItcStatusHistory.invoice_status_id == inv.id).order_by(ItcStatusHistory.at)).scalars().all()
    return inv, rows


def claimable_summary(db: Session, company_id: uuid.UUID, period: str) -> dict:
    """Month-wise ITC: available (matched in 2B / eligible, net of reversals) vs
    deferred (excluded from claimable) vs mismatched. Deferred credit is NOT part of
    the claimable figure."""
    rows = db.execute(select(ItcInvoiceStatus).where(
        ItcInvoiceStatus.company_id == company_id).where(
        (ItcInvoiceStatus.matched_period == period) |
        (ItcInvoiceStatus.booked_period == period))).scalars().all()
    buckets = {"available": {h: MZERO for h in TAX_HEADS},
               "deferred": {h: MZERO for h in TAX_HEADS},
               "mismatched": {h: MZERO for h in TAX_HEADS},
               "claimed": {h: MZERO for h in TAX_HEADS},
               "reversed": {h: MZERO for h in TAX_HEADS}}
    cnt = {"available": 0, "deferred": 0, "mismatched": 0, "claimed": 0, "reversed": 0}
    for r in rows:
        if r.status in _CLAIMABLE_FROM:
            k = "available"
        elif r.status == ItcStatus.ITC_DEFERRED.value or r.status == ItcStatus.IN_2A_NOT_IN_2B.value \
                or r.status == ItcStatus.BOOKED_NOT_IN_2B.value:
            k = "deferred"
        elif r.status == ItcStatus.MISMATCH_UNDER_REVIEW.value:
            k = "mismatched"
        elif r.status == ItcStatus.ITC_CLAIMED.value:
            k = "claimed"
        elif r.status in (ItcStatus.ITC_REVERSED.value, ItcStatus.ITC_RECLAIMED.value):
            k = "reversed"
        else:
            continue
        cnt[k] += 1
        for h in TAX_HEADS:
            buckets[k][h] = quantize(buckets[k][h] + m(getattr(r, h)), MONEY_SCALE)
    out = {"period": period, "counts": cnt,
           "amounts": {k: {h: str(v) for h, v in heads.items()} for k, heads in buckets.items()}}
    return out


def defer_itc(db: Session, company_id, invoice_id, *, reason: str, period: str | None = None,
              actor=None) -> ItcInvoiceStatus:
    inv = _get(db, company_id, invoice_id)
    if not (reason or "").strip():
        raise GstItcError("a reason is required to defer ITC")
    record_status(db, inv, ItcStatus.ITC_DEFERRED.value, period=period or inv.booked_period,
                  reason=reason, actor=actor)
    inv.deferred_reason = reason.strip()[:255]
    db.flush()
    record_audit(db, action="GST_ITC_DEFERRED", entity="gst_itc_invoice_status",
                 entity_id=str(inv.id), actor_id=actor,
                 detail={"company_id": str(company_id), "reason": inv.deferred_reason})
    return inv


def mark_ineligible(db: Session, company_id, invoice_id, *, reason: str, actor=None):
    inv = _get(db, company_id, invoice_id)
    if not (reason or "").strip():
        raise GstItcError("a reason is required to mark ITC ineligible")
    record_status(db, inv, ItcStatus.ITC_INELIGIBLE.value, reason=reason, actor=actor)
    inv.eligibility_note = reason.strip()[:255]
    db.flush()
    return inv


def claim_itc(db: Session, company_id, invoice_id, *, period: str, actor=None) -> ItcInvoiceStatus:
    """Mark ITC claimed for the return period (an explicit, authorised register action;
    never automatic). The input tax was already booked at purchase, so no GL posting
    is created here."""
    inv = _get(db, company_id, invoice_id)
    if not period_valid(period):
        raise GstItcError("period must be MMYYYY")
    if inv.status not in _CLAIMABLE_FROM:
        raise GstItcStateError(
            f"ITC can only be claimed from {sorted(_CLAIMABLE_FROM)} (is {inv.status})")
    record_status(db, inv, ItcStatus.ITC_CLAIMED.value, period=period,
                  reason="ITC claimed", actor=actor)
    inv.claim_period = period
    db.flush()
    record_audit(db, action="GST_ITC_CLAIMED", entity="gst_itc_invoice_status",
                 entity_id=str(inv.id), actor_id=actor,
                 detail={"company_id": str(company_id), "period": period})
    return inv


def _rr_net(db, invoice_id) -> dict:
    """Net-reversed-outstanding per head = sum(REVERSAL) - sum(RECLAIM)."""
    net = {h: MZERO for h in TAX_HEADS}
    rows = db.execute(select(ItcReversalReclaim).where(
        ItcReversalReclaim.invoice_status_id == invoice_id)).scalars().all()
    for r in rows:
        sign = 1 if r.action == ItcAction.REVERSAL.value else -1
        for h in TAX_HEADS:
            net[h] = quantize(net[h] + sign * m(getattr(r, h)), MONEY_SCALE)
    return net


def reverse_itc(db: Session, company_id, invoice_id, *, tax_period: str, reference: str,
                igst=0, cgst=0, sgst=0, cess=0, reason: str | None = None, is_partial=False,
                post: bool = False, authorize: bool = False, actor=None) -> ItcReversalReclaim:
    """Record an ITC reversal (append-only), optionally posting the GL correction via
    the existing engine when ``authorize=True``. A reversal can never exceed the
    invoice's remaining (unreversed) ITC per head."""
    inv = _get(db, company_id, invoice_id)
    if not period_valid(tax_period):
        raise GstItcError("tax_period must be MMYYYY")
    if inv.status not in _REVERSIBLE_FROM:
        raise GstItcStateError(f"ITC cannot be reversed from status {inv.status}")
    amt = {"igst": m(igst), "cgst": m(cgst), "sgst": m(sgst), "cess": m(cess)}
    if sum(amt.values(), MZERO) <= MZERO:
        raise GstItcError("reversal needs a positive tax amount")
    net = _rr_net(db, inv.id)
    for h in TAX_HEADS:
        available = quantize(m(getattr(inv, h)) - net[h], MONEY_SCALE)
        if amt[h] > available:
            raise GstItcStateError(
                f"reversal of {h} {amt[h]} exceeds remaining ITC {available} on the invoice")

    rr = ItcReversalReclaim(
        company_id=company_id, invoice_status_id=inv.id, action=ItcAction.REVERSAL.value,
        tax_period=tax_period, igst=amt["igst"], cgst=amt["cgst"], sgst=amt["sgst"],
        cess=amt["cess"], is_partial=is_partial, reason=(reason or "")[:500] or None,
        reference=reference, recorded_at=now(), created_at_actor=actor,
        created_by=actor, updated_by=actor)
    db.add(rr)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise GstItcStateError(f"a reversal/reclaim with reference '{reference}' already exists")

    voucher_id = None
    if post:
        if not authorize:
            raise GstItcStateError(
                "statutory GL posting of a reversal requires explicit authorisation (authorize=True)")
        voucher = gadj.post_itc_reversal(
            db, company_id=company_id, vdate=_vdate(tax_period), reference=reference,
            cgst=amt["cgst"], sgst=amt["sgst"], igst=amt["igst"],
            narration=f"ITC reversal {reference}", created_by=actor)
        voucher_id = voucher.id
        rr.voucher_id = voucher_id

    inv.reversed_total = quantize(m(inv.reversed_total) + sum(amt.values(), MZERO), MONEY_SCALE)
    record_status(db, inv, ItcStatus.ITC_REVERSED.value, period=tax_period,
                  reason=(reason or "ITC reversed"), reference=reference, voucher_id=voucher_id,
                  actor=actor)
    db.flush()
    record_audit(db, action="GST_ITC_REVERSED", entity="gst_itc_reversal_reclaim",
                 entity_id=str(rr.id), actor_id=actor,
                 detail={"company_id": str(company_id), "invoice": str(inv.id),
                         "reference": reference, "tax_period": tax_period, "posted": bool(voucher_id),
                         "amounts": {k: str(v) for k, v in amt.items()}})
    return rr


def reclaim_itc(db: Session, company_id, invoice_id, *, tax_period: str, reference: str,
                igst=0, cgst=0, sgst=0, cess=0, reason: str | None = None,
                linked_reversal_id: uuid.UUID | None = None, post: bool = False,
                authorize: bool = False, actor=None) -> ItcReversalReclaim:
    """Record an ITC reclaim linked to a prior reversal (append-only). A reclaim can
    never exceed the net-reversed-outstanding balance per head (duplicate / excess
    reclaim prevented). Posts the mirror GL entry only when ``authorize=True``."""
    inv = _get(db, company_id, invoice_id)
    if not period_valid(tax_period):
        raise GstItcError("tax_period must be MMYYYY")
    amt = {"igst": m(igst), "cgst": m(cgst), "sgst": m(sgst), "cess": m(cess)}
    if sum(amt.values(), MZERO) <= MZERO:
        raise GstItcError("reclaim needs a positive tax amount")
    net = _rr_net(db, inv.id)
    if sum(net.values(), MZERO) <= MZERO:
        raise GstItcStateError("nothing is reversed on this invoice to reclaim")
    for h in TAX_HEADS:
        if amt[h] > net[h]:
            raise GstItcStateError(
                f"reclaim of {h} {amt[h]} exceeds net-reversed {net[h]} (duplicate/excess reclaim)")
    if linked_reversal_id is not None:
        rev = db.get(ItcReversalReclaim, linked_reversal_id)
        if rev is None or rev.company_id != company_id or rev.invoice_status_id != inv.id \
                or rev.action != ItcAction.REVERSAL.value:
            raise GstItcError("linked_reversal_id does not reference a reversal on this invoice")

    remaining_after = {h: quantize(net[h] - amt[h], MONEY_SCALE) for h in TAX_HEADS}
    partial = sum(remaining_after.values(), MZERO) > MZERO
    rr = ItcReversalReclaim(
        company_id=company_id, invoice_status_id=inv.id, action=ItcAction.RECLAIM.value,
        tax_period=tax_period, igst=amt["igst"], cgst=amt["cgst"], sgst=amt["sgst"],
        cess=amt["cess"], is_partial=partial, reason=(reason or "")[:500] or None,
        reference=reference, linked_reversal_id=linked_reversal_id, recorded_at=now(),
        created_at_actor=actor, created_by=actor, updated_by=actor)
    db.add(rr)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise GstItcStateError(f"a reversal/reclaim with reference '{reference}' already exists")

    voucher_id = None
    if post:
        if not authorize:
            raise GstItcStateError(
                "statutory GL posting of a reclaim requires explicit authorisation (authorize=True)")
        voucher = gadj.post_itc_reclaim(
            db, company_id=company_id, vdate=_vdate(tax_period), reference=reference,
            cgst=amt["cgst"], sgst=amt["sgst"], igst=amt["igst"],
            narration=f"ITC reclaim {reference}", created_by=actor)
        voucher_id = voucher.id
        rr.voucher_id = voucher_id

    inv.reclaimed_total = quantize(m(inv.reclaimed_total) + sum(amt.values(), MZERO), MONEY_SCALE)
    new_status = ItcStatus.ITC_REVERSED.value if partial else ItcStatus.ITC_RECLAIMED.value
    record_status(db, inv, new_status, period=tax_period,
                  reason=(reason or "ITC reclaimed") + (" (partial)" if partial else ""),
                  reference=reference, voucher_id=voucher_id, actor=actor)
    db.flush()
    record_audit(db, action="GST_ITC_RECLAIMED", entity="gst_itc_reversal_reclaim",
                 entity_id=str(rr.id), actor_id=actor,
                 detail={"company_id": str(company_id), "invoice": str(inv.id),
                         "reference": reference, "tax_period": tax_period, "partial": partial,
                         "posted": bool(voucher_id),
                         "amounts": {k: str(v) for k, v in amt.items()}})
    return rr


def reversal_history(db: Session, company_id: uuid.UUID, invoice_id: uuid.UUID):
    inv = _get(db, company_id, invoice_id)
    rows = db.execute(select(ItcReversalReclaim).where(
        ItcReversalReclaim.invoice_status_id == inv.id)
        .order_by(ItcReversalReclaim.recorded_at)).scalars().all()
    return inv, rows, _rr_net(db, inv.id)
