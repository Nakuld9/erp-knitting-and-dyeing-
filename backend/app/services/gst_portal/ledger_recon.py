"""GST electronic-ledger reconciliation (Stage 6F-P5 GST).

Reconciles PORTAL electronic-ledger balances (imported, never fabricated) against
ERP accounting balances for the mapped GST accounts. Portal balances and ERP
balances are reported side by side and clearly distinguished; where no ERP account
maps (e.g. the electronic cash ledger / PLA), the ERP side is reported as null
rather than invented.
"""
from __future__ import annotations

import uuid
from calendar import monthrange
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.accounting import ChartOfAccount, JournalLine, Voucher, VoucherStatus
from app.models.gst_itc import GstElectronicLedgerBalance, GstReturnType
from app.services.gst_portal import importer as imp
from app.services.gst_portal.common import MZERO, GstItcError, m, period_valid

_POSTED = [VoucherStatus.POSTED.value, VoucherStatus.REVERSED.value, VoucherStatus.REVERSAL.value]

# head -> ERP account code, per ledger type. Credit (input) accounts are assets
# (debit-positive); liability (output) accounts are credit-positive.
_ERP_MAP = {
    "CREDIT": {"IGST": "1123", "CGST": "1121", "SGST": "1122"},
    "LIABILITY": {"IGST": "2043", "CGST": "2041", "SGST": "2042"},
    "CASH": {},   # electronic cash ledger / PLA has no standard ERP control account here
}
_LEDGER_RETURN = {
    "CREDIT": GstReturnType.ECL_CREDIT.value,
    "CASH": GstReturnType.ECL_CASH.value,
    "LIABILITY": GstReturnType.ELIABILITY.value,
}


def _erp_balance(db, company_id, code, as_of) -> "object":
    rows = db.execute(
        select(JournalLine.debit, JournalLine.credit)
        .join(Voucher, Voucher.id == JournalLine.voucher_id)
        .join(ChartOfAccount, ChartOfAccount.id == JournalLine.account_id)
        .where(Voucher.company_id == company_id, ChartOfAccount.code == code,
               Voucher.status.in_(_POSTED), Voucher.voucher_date <= as_of)).all()
    bal = MZERO
    for dr, cr in rows:
        bal = quantize(bal + m(dr) - m(cr), MONEY_SCALE)
    return bal


def ledger_reconciliation(db: Session, company_id: uuid.UUID, *, ledger_type: str, period: str,
                          tol: str = "1") -> dict:
    ledger_type = (ledger_type or "").upper()
    if ledger_type not in _ERP_MAP:
        raise GstItcError("ledger_type must be CREDIT, CASH or LIABILITY")
    if not period_valid(period):
        raise GstItcError("period must be MMYYYY")
    tol = m(tol)
    mm, yyyy = int(period[:2]), int(period[2:])
    as_of = date(yyyy, mm, monthrange(yyyy, mm)[1])

    import_row = imp.latest_import(db, company_id, _LEDGER_RETURN[ledger_type], period)
    portal = {}
    if import_row is not None:
        for b in db.execute(select(GstElectronicLedgerBalance).where(
                GstElectronicLedgerBalance.import_id == import_row.id)).scalars().all():
            portal[b.head.upper()] = m(b.closing_balance)

    erp_map = _ERP_MAP[ledger_type]
    heads = sorted(set(portal) | set(erp_map))
    rows = []
    for head in heads:
        p = portal.get(head)
        erp = None
        if head in erp_map:
            raw = _erp_balance(db, company_id, erp_map[head], as_of)
            # Liability accounts are credit-positive: flip to a positive balance.
            erp = quantize(-raw, MONEY_SCALE) if ledger_type == "LIABILITY" else raw
        diff = None
        reconciled = None
        if p is not None and erp is not None:
            diff = quantize(p - erp, MONEY_SCALE)
            reconciled = abs(diff) <= tol
        rows.append({
            "head": head,
            "portal_closing": (str(p) if p is not None else None),
            "erp_account": erp_map.get(head),
            "erp_balance": (str(erp) if erp is not None else None),
            "difference": (str(diff) if diff is not None else None),
            "reconciled": reconciled,
        })
    return {
        "ledger_type": ledger_type, "period": period, "as_of": as_of.isoformat(),
        "portal_source": ("import:" + str(import_row.id) if import_row else None),
        "portal_available": import_row is not None,
        "note": ("Portal balances are from an imported electronic ledger; ERP balances "
                 "are from the accounting ledger. They are distinct sources."),
        "rows": rows,
    }
