"""Reporting routes: trial balance, ledger, and financial statements."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_permission
from app.models.core import User
from app.services import exports
from app.services.accounting import financial_statements as fs
from app.services.accounting import gst as gst_svc
from app.services.accounting import stock_reconciliation as stock_recon
from app.services.accounting import trial_balance as tb
from app.services.exports import builders as export_builders

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _stringify(obj):
    """Recursively convert Decimals to strings for lossless JSON."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _stringify(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stringify(v) for v in obj]
    return obj


def _fy_start(d: date) -> date:
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1)


@router.get("/trial-balance")
def get_trial_balance(
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("report.financial")),
) -> dict:
    return tb.trial_balance(db, user.company_id, date_from=date_from, date_to=date_to)


@router.get("/ledger/{account_code}")
def get_account_ledger(
    account_code: str,
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("report.read")),
) -> dict:
    try:
        return _stringify(tb.account_ledger(
            db, user.company_id, account_code, date_from=date_from, date_to=date_to
        ))
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.get("/profit-and-loss")
def profit_and_loss(
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("report.financial")),
) -> dict:
    to = date_to or date.today()
    frm = date_from or _fy_start(to)
    return _stringify(fs.profit_and_loss(db, user.company_id, frm, to))


@router.get("/cost-centres")
def cost_centres(
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("report.read")),
) -> dict:
    from app.services.accounting.cost_centres import cost_centre_report
    # Default to the whole fiscal year so period-end postings (e.g. month-end
    # overhead allocations, year-end entries) are included.
    frm = date_from or _fy_start(date.today())
    to = date_to or date(frm.year + 1, 3, 31)
    return _stringify(cost_centre_report(db, user.company_id, frm, to))


@router.get("/balance-sheet")
def balance_sheet(
    as_of: date | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("report.financial")),
) -> dict:
    return _stringify(fs.balance_sheet(db, user.company_id, as_of or date.today()))


@router.get("/cash-flow")
def cash_flow(
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("report.financial")),
) -> dict:
    to = date_to or date.today()
    frm = date_from or _fy_start(to)
    return _stringify(fs.cash_flow(db, user.company_id, frm, to))


@router.get("/debtors-ageing")
def debtors_ageing(
    as_of: date | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("report.read")),
) -> dict:
    return _stringify(fs.debtors_ageing(db, user.company_id, as_of or date.today()))


@router.get("/creditors-ageing")
def creditors_ageing(
    as_of: date | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("report.read")),
) -> dict:
    return _stringify(fs.creditors_ageing(db, user.company_id, as_of or date.today()))


def _range(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    to = date_to or date.today()
    return (date_from or _fy_start(to)), to


@router.get("/gst/sales-register")
def gst_sales_register(date_from: date | None = None, date_to: date | None = None,
                       db: Session = Depends(get_db),
                       user: User = Depends(require_permission("report.financial"))) -> dict:
    frm, to = _range(date_from, date_to)
    return _stringify(gst_svc.sales_register(db, user.company_id, frm, to))


@router.get("/gst/purchase-register")
def gst_purchase_register(date_from: date | None = None, date_to: date | None = None,
                          db: Session = Depends(get_db),
                          user: User = Depends(require_permission("report.financial"))) -> dict:
    frm, to = _range(date_from, date_to)
    return _stringify(gst_svc.purchase_register(db, user.company_id, frm, to))


@router.get("/gst/gstr1")
def gst_gstr1(date_from: date | None = None, date_to: date | None = None,
              db: Session = Depends(get_db),
              user: User = Depends(require_permission("report.financial"))) -> dict:
    frm, to = _range(date_from, date_to)
    return _stringify(gst_svc.gstr1_summary(db, user.company_id, frm, to))


@router.get("/gst/gstr3b")
def gst_gstr3b(date_from: date | None = None, date_to: date | None = None,
               db: Session = Depends(get_db),
               user: User = Depends(require_permission("report.financial"))) -> dict:
    frm, to = _range(date_from, date_to)
    return _stringify(gst_svc.gstr3b_summary(db, user.company_id, frm, to))


@router.get("/gst/reconciliation")
def gst_reconciliation(date_from: date | None = None, date_to: date | None = None,
                       db: Session = Depends(get_db),
                       user: User = Depends(require_permission("report.financial"))) -> dict:
    frm, to = _range(date_from, date_to)
    return _stringify(gst_svc.book_vs_gst_reconciliation(db, user.company_id, frm, to))


@router.get("/stock-reconciliation")
def stock_reconciliation(as_of: date | None = None, db: Session = Depends(get_db),
                         user: User = Depends(require_permission("report.financial"))) -> dict:
    return _stringify(stock_recon.stock_ledger_reconciliation(db, user.company_id, as_of=as_of))


_MEDIA = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


@router.get("/export/{report}")
def export_report(
    report: str,
    format: str = "xlsx",
    date_from: date | None = None,
    date_to: date | None = None,
    as_of: date | None = None,
    bath_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("report.read")),
) -> Response:
    if format not in _MEDIA:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "format must be 'xlsx' or 'pdf'")
    try:
        doc = export_builders.build_report(
            db, user.company_id, report, date_from=date_from, date_to=date_to,
            as_of=as_of, bath_id=bath_id,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    content = exports.render_excel(doc) if format == "xlsx" else exports.render_pdf(doc)
    filename = f"{report}-{date.today().isoformat()}.{format}"
    return Response(
        content=content, media_type=_MEDIA[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
