"""Cost-centre ledger: accumulated cost per work centre (machine) and the
department it rolls up to, read straight from the posted ledger.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.accounting import JournalLine, Voucher, VoucherStatus
from app.models.masters import CostCentre

ZERO = quantize(0, MONEY_SCALE)
_POSTED = [s.value for s in (VoucherStatus.POSTED, VoucherStatus.REVERSED, VoucherStatus.REVERSAL)]


def cost_centre_report(db: Session, company_id: uuid.UUID, date_from: date, date_to: date) -> dict:
    """Net cost (debit - credit) per cost centre in the period, with a
    department roll-up (work centres summed into their parent department)."""
    rows = db.execute(
        select(JournalLine.cost_centre_id, JournalLine.debit, JournalLine.credit)
        .join(Voucher, JournalLine.voucher_id == Voucher.id)
        .where(Voucher.company_id == company_id, Voucher.status.in_(_POSTED),
               Voucher.voucher_date >= date_from, Voucher.voucher_date <= date_to,
               JournalLine.cost_centre_id.isnot(None))
    ).all()

    agg: dict[uuid.UUID, Decimal] = {}
    for cc_id, debit, credit in rows:
        agg[cc_id] = quantize(agg.get(cc_id, ZERO) + Decimal(str(debit)) - Decimal(str(credit)), MONEY_SCALE)

    centres = []
    dept_totals: dict[uuid.UUID, Decimal] = {}
    for cc_id, net in agg.items():
        cc = db.get(CostCentre, cc_id)
        if cc is None:
            continue
        kind = cc.centre_type.value if hasattr(cc.centre_type, "value") else cc.centre_type
        centres.append({"id": str(cc.id), "code": cc.code, "name": cc.name, "type": kind,
                        "parent_id": str(cc.parent_id) if cc.parent_id else None, "net_cost": net})
        # Roll a work centre's cost up to its department (else to itself).
        dept = cc.parent_id or cc.id
        dept_totals[dept] = quantize(dept_totals.get(dept, ZERO) + net, MONEY_SCALE)

    departments = []
    for dept_id, net in dept_totals.items():
        dc = db.get(CostCentre, dept_id)
        departments.append({"id": str(dept_id), "code": dc.code if dc else "?",
                            "name": dc.name if dc else "?", "net_cost": net})

    centres.sort(key=lambda c: c["code"])
    departments.sort(key=lambda d: d["code"])
    total = quantize(sum((c["net_cost"] for c in centres), ZERO), MONEY_SCALE)
    return {"date_from": date_from, "date_to": date_to, "centres": centres,
            "departments": departments, "total_net_cost": total}
