"""Shared helpers for the GST/ITC module: period math, keys, money, status trail."""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.gst_itc import ItcInvoiceStatus, ItcStatusHistory

MZERO = quantize(0, MONEY_SCALE)
TAX_HEADS = ("igst", "cgst", "sgst", "cess")


class GstItcError(ValueError):
    """An invalid GST/ITC reconciliation or lifecycle request."""


class GstItcStateError(GstItcError):
    """Wrong state for the requested ITC lifecycle action."""


def now():
    return datetime.now(timezone.utc)


def m(v) -> Decimal:
    if v is None or str(v).strip() == "":
        return MZERO
    try:
        return quantize(Decimal(str(v)), MONEY_SCALE)
    except Exception:  # noqa: BLE001
        return MZERO


def norm_gstin(v) -> str:
    return (str(v).strip().upper() if v else "")


def norm_invoice_no(v) -> str:
    """Normalise an invoice/document number for matching: upper-case, drop every
    non-alphanumeric character (so 'INV-0012', 'inv 0012' and 'INV/0012' collide)."""
    if not v:
        return ""
    return re.sub(r"[^A-Za-z0-9]", "", str(v)).upper()


def make_books_key(supplier_gstin, supplier_invoice_no, doc_type="INV") -> str:
    return f"{norm_gstin(supplier_gstin)}|{norm_invoice_no(supplier_invoice_no)}|{(doc_type or 'INV').upper()}"


def period_valid(period: str) -> bool:
    return bool(period) and len(period) == 6 and period.isdigit()


def period_from_date(d: date) -> str:
    return f"{d.month:02d}{d.year:04d}"


def next_period(period: str) -> str:
    mm, yyyy = int(period[:2]), int(period[2:])
    mm += 1
    if mm > 12:
        mm, yyyy = 1, yyyy + 1
    return f"{mm:02d}{yyyy:04d}"


def period_le(a: str, b: str) -> bool:
    """a <= b chronologically (MMYYYY)."""
    return (int(a[2:]), int(a[:2])) <= (int(b[2:]), int(b[:2]))


def record_status(db: Session, inv: ItcInvoiceStatus, to_status: str, *, period=None,
                  reason=None, reference=None, voucher_id=None, actor=None):
    """Append an immutable history row and set the new status. Never deletes or
    overwrites earlier history."""
    from_status = inv.status
    db.add(ItcStatusHistory(
        company_id=inv.company_id, invoice_status_id=inv.id, from_status=from_status,
        to_status=to_status, period=period, reason=(reason[:500] if reason else None),
        reference=(reference[:120] if reference else None), voucher_id=voucher_id,
        actor_id=actor, at=now(), created_by=actor, updated_by=actor))
    inv.status = to_status
