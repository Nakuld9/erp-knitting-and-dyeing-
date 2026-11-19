"""Financial opening-balance services (Stage 6F-P5).

Builds the complete financial opening cutover on top of the P1 ``OpeningBatch``
lifecycle and the EXISTING double-entry posting engine. A Financial Opening Batch
(``OpeningBatch`` of type GL_BALANCES) carries six kinds of typed line (GL / AR /
AP / bank / fixed-asset / statutory). Nothing here rebuilds the ledger — it
validates, reconciles, and, only when a hard gate is satisfied, posts ONE balanced
``OB`` voucher through ``posting_engine.post_voucher`` and locks the batch.

HARD POSTING GATE (``post_financial``):
  1. batch APPROVED (P1 maker-checker; preparer cannot approve own batch);
  2. every REQUIRED ``OpeningCaDecision`` is APPROVED;
  3. every reconciliation passes (trial balance balances; AR/AP detail ties to the
     control accounts; inventory control ties to the P1-P4 sub-ledger total);
  4. an explicit ``authorize=True`` posting authorisation is supplied.
No value is ever auto-plugged to equity/retained earnings; no sub-ledger is
re-created (``inventory.receive`` is never called); no fake invoice/receipt/
filing is generated. Corrections use ``reverse_voucher``.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.accounting import ChartOfAccount
from app.models.core import Branch
from app.models.inventory import StockLayer
from app.models.masters import BankAccount, Customer, Supplier
from app.models.opening import OpeningBatchStatus, OpeningBatchType
from app.models.opening_financial import (
    ApKind,
    ArKind,
    CaDecisionStatus,
    FinancialLineType,
    FinancialPostingState,
    OpeningApLine,
    OpeningArLine,
    OpeningBankLine,
    OpeningCaDecision,
    OpeningFixedAssetLine,
    OpeningGLLine,
    OpeningStatutoryLine,
    REQUIRED_CA_DECISIONS,
)
from app.models.opening_stock import OpeningLineValidation
from app.services import opening as batch_svc
from app.services.accounting import posting_engine as pe
from app.services.audit import record_audit

MZERO = quantize(0, MONEY_SCALE)
_TOL = Decimal("0.01")
_V = OpeningLineValidation
_FIN_BATCH_TYPE = OpeningBatchType.GL_BALANCES.value
_FINOPEN_VT = "OB"
# Inventory control accounts the P1-P4 sub-ledger total must reconcile against.
_INVENTORY_CONTROL_CODES = {"1060", "1070", "1080", "1085", "1090", "1100", "1110"}
_AR_CONTROL_DEFAULT = "1040"
_AP_CONTROL_DEFAULT = "2010"

_LINE_MODEL = {
    FinancialLineType.GL.value: OpeningGLLine,
    FinancialLineType.AR.value: OpeningArLine,
    FinancialLineType.AP.value: OpeningApLine,
    FinancialLineType.BANK.value: OpeningBankLine,
    FinancialLineType.FIXED_ASSET.value: OpeningFixedAssetLine,
    FinancialLineType.STATUTORY.value: OpeningStatutoryLine,
}


class OpeningFinancialError(ValueError):
    """An invalid financial-opening request."""


class OpeningFinancialStateError(OpeningFinancialError):
    """Wrong batch/posting state for the operation."""


class PostingBlockedError(OpeningFinancialError):
    """The hard posting gate is not satisfied."""


# ------------------------------- helpers ------------------------------------

def _now():
    return datetime.now(timezone.utc)


def _clean(v, n):
    if v is None:
        return None
    t = str(v).strip()
    return t[:n] if t else None


def _norm(v):
    return " ".join(str(v).strip().upper().split()) if v else None


def _m(raw):
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return quantize(Decimal(str(raw).strip()), MONEY_SCALE)
    except (InvalidOperation, ValueError):
        return None


def _as_date(raw):
    if raw is None:
        return None
    if isinstance(raw, date):
        return raw
    s = str(raw).strip()
    try:
        return date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None


def _as_bool(raw):
    return str(raw).strip().lower() in ("1", "true", "yes", "y") if raw is not None else False


def _audit(db, actor, action, obj, detail):
    record_audit(db, action=action, entity="opening_financial", entity_id=str(obj.id),
                 actor_id=actor, detail=detail)


def _account(db, company_id, code):
    code = _clean(code, 20)
    if not code:
        return None
    return db.execute(select(ChartOfAccount).where(
        ChartOfAccount.company_id == company_id, ChartOfAccount.code == code)).scalars().first()


def _require_fin_batch(db, company_id, batch_id):
    batch = batch_svc.get_batch(db, company_id, batch_id)
    if batch.batch_type != _FIN_BATCH_TYPE:
        raise OpeningFinancialError(
            f"Batch type '{batch.batch_type}' is not a financial opening batch (GL_BALANCES)")
    return batch


# ----------------------------- per-type builders ----------------------------

def _common(line, batch, row):
    line.company_id = batch.company_id
    line.batch_id = batch.id
    line.row_number = row.get("_row")
    line.source_document = _clean(row.get("source_document"), 120)
    line.source_reference = _clean(row.get("source_reference"), 200)
    line.disputed_unknown = _as_bool(row.get("disputed_unknown"))


def _build_gl(db, batch, row):
    errors = []
    line = OpeningGLLine()
    _common(line, batch, row)
    line.account_code = _clean(row.get("account_code"), 20)
    line.debit = _m(row.get("debit")) or MZERO
    line.credit = _m(row.get("credit")) or MZERO
    line.narration = _clean(row.get("narration"), 255)
    if not line.account_code:
        errors.append({"field": "account_code", "reason": "account_code is required"})
    else:
        acct = _account(db, batch.company_id, line.account_code)
        if acct is None:
            errors.append({"field": "account_code", "reason": "account not found in company chart"})
        elif acct.is_group or not acct.allow_posting:
            errors.append({"field": "account_code", "reason": "account is a group/non-postable account"})
        elif not acct.is_active:
            errors.append({"field": "account_code", "reason": "account is inactive"})
        else:
            line.account_id = acct.id
            line.is_control_split = (line.account_code in (_AR_CONTROL_DEFAULT, _AP_CONTROL_DEFAULT))
    if (line.debit > MZERO) == (line.credit > MZERO):
        errors.append({"field": "debit", "reason": "exactly one of debit/credit must be > 0"})
    if line.debit < MZERO or line.credit < MZERO:
        errors.append({"field": "debit", "reason": "amounts cannot be negative"})
    return line, errors


def _build_ar(db, batch, row):
    errors = []
    line = OpeningArLine()
    _common(line, batch, row)
    line.ar_kind = (_clean(row.get("ar_kind"), 20) or ArKind.INVOICE.value).upper()
    line.invoice_no = _clean(row.get("invoice_no"), 40)
    line.invoice_date = _as_date(row.get("invoice_date"))
    line.due_date = _as_date(row.get("due_date"))
    line.original_amount = _m(row.get("original_amount")) or MZERO
    line.received_amount = _m(row.get("received_amount")) or MZERO
    line.outstanding_amount = _m(row.get("outstanding_amount"))
    line.tax_reference = _clean(row.get("tax_reference"), 60)
    line.currency = _clean(row.get("currency"), 3)
    line.notes = _clean(row.get("notes"), 255)
    if line.ar_kind not in {k.value for k in ArKind}:
        errors.append({"field": "ar_kind", "reason": "invalid ar_kind"})
    cust = None
    code = _clean(row.get("customer_code"), 20)
    if not code:
        errors.append({"field": "customer_code", "reason": "customer_code is required"})
    else:
        cust = db.execute(select(Customer).where(
            Customer.company_id == batch.company_id, Customer.code == code)).scalars().first()
        if cust is None:
            errors.append({"field": "customer_code", "reason": "unknown customer for this company"})
        else:
            line.customer_id = cust.id
    if line.outstanding_amount is None:
        errors.append({"field": "outstanding_amount", "reason": "outstanding_amount is required"})
    elif line.outstanding_amount < MZERO:
        errors.append({"field": "outstanding_amount", "reason": "outstanding cannot be negative"})
    if line.ar_kind == ArKind.INVOICE.value and not line.invoice_no:
        errors.append({"field": "invoice_no", "reason": "invoice_no required for INVOICE kind"})
    if line.disputed_unknown:
        errors.append({"field": "disputed_unknown", "reason": "disputed/unknown line must be resolved"})
    return line, errors


def _build_ap(db, batch, row):
    errors = []
    line = OpeningApLine()
    _common(line, batch, row)
    line.ap_kind = (_clean(row.get("ap_kind"), 20) or ApKind.INVOICE.value).upper()
    line.invoice_no = _clean(row.get("invoice_no"), 40)
    line.invoice_date = _as_date(row.get("invoice_date"))
    line.due_date = _as_date(row.get("due_date"))
    line.original_amount = _m(row.get("original_amount")) or MZERO
    line.paid_amount = _m(row.get("paid_amount")) or MZERO
    line.outstanding_amount = _m(row.get("outstanding_amount"))
    line.tds_amount = _m(row.get("tds_amount")) or MZERO
    line.tax_reference = _clean(row.get("tax_reference"), 60)
    line.notes = _clean(row.get("notes"), 255)
    if line.ap_kind not in {k.value for k in ApKind}:
        errors.append({"field": "ap_kind", "reason": "invalid ap_kind"})
    code = _clean(row.get("supplier_code"), 20)
    if not code:
        errors.append({"field": "supplier_code", "reason": "supplier_code is required"})
    else:
        sup = db.execute(select(Supplier).where(
            Supplier.company_id == batch.company_id, Supplier.code == code)).scalars().first()
        if sup is None:
            errors.append({"field": "supplier_code", "reason": "unknown supplier for this company"})
        else:
            line.supplier_id = sup.id
    if line.outstanding_amount is None:
        errors.append({"field": "outstanding_amount", "reason": "outstanding_amount is required"})
    elif line.outstanding_amount < MZERO:
        errors.append({"field": "outstanding_amount", "reason": "outstanding cannot be negative"})
    if line.ap_kind in (ApKind.INVOICE.value, ApKind.JOBWORK_BILL.value) and not line.invoice_no:
        errors.append({"field": "invoice_no", "reason": "invoice_no required for INVOICE/JOBWORK_BILL"})
    if line.disputed_unknown:
        errors.append({"field": "disputed_unknown", "reason": "disputed/unknown line must be resolved"})
    return line, errors


def _build_bank(db, batch, row):
    errors = []
    line = OpeningBankLine()
    _common(line, batch, row)
    line.is_cash = _as_bool(row.get("is_cash"))
    line.book_balance = _m(row.get("book_balance")) or MZERO
    line.statement_balance = _m(row.get("statement_balance")) or MZERO
    line.outstanding_cheques = _m(row.get("outstanding_cheques")) or MZERO
    line.deposits_in_transit = _m(row.get("deposits_in_transit")) or MZERO
    line.effective_date = _as_date(row.get("effective_date")) or batch.effective_date
    line.notes = _clean(row.get("notes"), 255)
    line.account_code = _clean(row.get("account_code"), 20)
    bname = _clean(row.get("bank_account_name"), 120)
    if bname:
        ba = db.execute(select(BankAccount).where(
            BankAccount.company_id == batch.company_id, BankAccount.name == bname)).scalars().first()
        if ba is None:
            errors.append({"field": "bank_account_name", "reason": "unknown bank account"})
        else:
            line.bank_account_id = ba.id
            if not line.account_code and ba.coa_account_id:
                acct = db.get(ChartOfAccount, ba.coa_account_id)
                line.account_code = acct.code if acct else None
    if not line.account_code:
        errors.append({"field": "account_code", "reason": "account_code (bank/cash GL account) is required"})
    else:
        acct = _account(db, batch.company_id, line.account_code)
        if acct is None or acct.is_group or not acct.allow_posting or not acct.is_active:
            errors.append({"field": "account_code", "reason": "invalid/non-postable GL account"})
        else:
            line.account_id = acct.id
    return line, errors


def _build_fa(db, batch, row):
    errors = []
    line = OpeningFixedAssetLine()
    _common(line, batch, row)
    line.asset_code = _clean(row.get("asset_code"), 40)
    line.name = _clean(row.get("name"), 160)
    line.category = _clean(row.get("category"), 60)
    line.asset_account_code = _clean(row.get("asset_account_code"), 20)
    line.accum_dep_account_code = _clean(row.get("accum_dep_account_code"), 20)
    line.acquisition_date = _as_date(row.get("acquisition_date"))
    line.original_cost = _m(row.get("original_cost")) or MZERO
    line.accumulated_depreciation = _m(row.get("accumulated_depreciation")) or MZERO
    line.salvage_value = _m(row.get("salvage_value")) or MZERO
    line.method = (_clean(row.get("method"), 3) or None)
    line.useful_life_months = int(row["useful_life_months"]) if _clean(row.get("useful_life_months"), 10) else None
    line.verification_status = _clean(row.get("verification_status"), 30)
    line.notes = _clean(row.get("notes"), 255)
    fac_code = _clean(row.get("factory_code"), 20)
    if fac_code:
        fac = db.execute(select(Branch).where(
            Branch.company_id == batch.company_id, Branch.code == fac_code)).scalars().first()
        if fac is None:
            errors.append({"field": "factory_code", "reason": "unknown factory"})
        else:
            line.factory_id = fac.id
    if not line.asset_code:
        errors.append({"field": "asset_code", "reason": "asset_code is required"})
    if line.original_cost < MZERO or line.accumulated_depreciation < MZERO:
        errors.append({"field": "original_cost", "reason": "cost/accumulated depreciation cannot be negative"})
    if line.accumulated_depreciation > line.original_cost:
        errors.append({"field": "accumulated_depreciation",
                       "reason": "accumulated depreciation exceeds original cost"})
    line.net_book_value = quantize(line.original_cost - line.accumulated_depreciation, MONEY_SCALE)
    for code, fld in ((line.asset_account_code, "asset_account_code"),
                      (line.accum_dep_account_code, "accum_dep_account_code")):
        if code:
            acct = _account(db, batch.company_id, code)
            if acct is None or acct.is_group or not acct.allow_posting:
                errors.append({"field": fld, "reason": f"invalid GL account '{code}'"})
    return line, errors


def _build_statutory(db, batch, row):
    errors = []
    line = OpeningStatutoryLine()
    _common(line, batch, row)
    line.statutory_type = (_clean(row.get("statutory_type"), 20) or None)
    line.account_code = _clean(row.get("account_code"), 20)
    line.debit = _m(row.get("debit")) or MZERO
    line.credit = _m(row.get("credit")) or MZERO
    line.period_reference = _clean(row.get("period_reference"), 40)
    line.challan_reference = _clean(row.get("challan_reference"), 60)
    line.notes = _clean(row.get("notes"), 255)
    if not line.account_code:
        errors.append({"field": "account_code", "reason": "account_code is required"})
    else:
        acct = _account(db, batch.company_id, line.account_code)
        if acct is None or acct.is_group or not acct.allow_posting or not acct.is_active:
            errors.append({"field": "account_code",
                           "reason": "statutory account not found/postable (new accounts need CA approval)"})
        else:
            line.account_id = acct.id
    if (line.debit > MZERO) == (line.credit > MZERO):
        errors.append({"field": "debit", "reason": "exactly one of debit/credit must be > 0"})
    return line, errors


_BUILDERS = {
    FinancialLineType.GL.value: _build_gl,
    FinancialLineType.AR.value: _build_ar,
    FinancialLineType.AP.value: _build_ap,
    FinancialLineType.BANK.value: _build_bank,
    FinancialLineType.FIXED_ASSET.value: _build_fa,
    FinancialLineType.STATUTORY.value: _build_statutory,
}


# Natural dedup keys are derived from each line's already-validated fields. They
# are assigned to the ``dedup_key`` column ONLY in the dedup pass (below), never by
# the per-type builders — otherwise two valid rows sharing a key would collide on
# the partial unique index at the first flush, before duplicates can be nulled.
def _nk_gl(l):
    return f"GL|{l.account_code}"


def _nk_ar(l):
    return f"AR|{l.customer_id}|{l.ar_kind}|{(l.invoice_no or l.ar_kind or '').upper()}"


def _nk_ap(l):
    return f"AP|{l.supplier_id}|{l.ap_kind}|{(l.invoice_no or l.ap_kind or '').upper()}"


def _nk_bank(l):
    return f"BANK|{l.account_code}|{l.bank_account_id or ''}"


def _nk_fa(l):
    return f"FA|{(l.asset_code or '').upper()}"


def _nk_statutory(l):
    return f"STAT|{l.account_code}|{l.period_reference or ''}"


_NATURAL_KEY = {
    FinancialLineType.GL.value: _nk_gl,
    FinancialLineType.AR.value: _nk_ar,
    FinancialLineType.AP.value: _nk_ap,
    FinancialLineType.BANK.value: _nk_bank,
    FinancialLineType.FIXED_ASSET.value: _nk_fa,
    FinancialLineType.STATUTORY.value: _nk_statutory,
}


def _finalize(line, errors):
    # dedup_key is deliberately left NULL here for every line (valid or invalid);
    # the dedup pass assigns it to the first occurrence of each natural key.
    line.dedup_key = None
    if errors:
        line.validation_status = _V.INVALID.value
        line.validation_errors = json.dumps(errors)
    else:
        line.validation_status = _V.VALID.value
        line.validation_errors = None


def _dedup_pass(db, batch, model, line_type):
    keyfn = _NATURAL_KEY[line_type]
    lines = db.execute(select(model).where(model.batch_id == batch.id)
                       .order_by(model.row_number, model.created_at)).scalars().all()
    seen = set()
    for line in lines:
        if line.validation_status != _V.VALID.value:
            continue
        key = keyfn(line)
        if key in seen:
            errs = json.loads(line.validation_errors) if line.validation_errors else []
            errs.append({"field": "dedup", "reason": "duplicate line within batch"})
            line.validation_status = _V.INVALID.value
            line.validation_errors = json.dumps(errs)
            line.dedup_key = None
        else:
            seen.add(key)
            line.dedup_key = key


# ------------------------------- import -------------------------------------

def import_rows(db, batch, *, line_type, rows, import_file, actor=None) -> dict:
    if batch.status != OpeningBatchStatus.DRAFT.value:
        raise OpeningFinancialStateError("Financial lines can only be imported while the batch is DRAFT")
    if batch.batch_type != _FIN_BATCH_TYPE:
        raise OpeningFinancialError("Not a financial opening batch (GL_BALANCES)")
    builder = _BUILDERS.get(line_type)
    model = _LINE_MODEL.get(line_type)
    if builder is None:
        raise OpeningFinancialError(f"Unknown financial line type '{line_type}'")
    built = []
    for row in rows:
        line, errors = builder(db, batch, row)
        line.import_file_id = import_file.id
        line.created_by = actor
        line.updated_by = actor
        _finalize(line, errors)
        db.add(line)
        built.append(line)
    db.flush()
    _dedup_pass(db, batch, model, line_type)
    db.flush()
    valid = sum(1 for l in built if l.validation_status == _V.VALID.value)
    invalid = len(built) - valid
    import_file.row_count = len(built)
    import_file.valid_count = valid
    import_file.invalid_count = invalid
    db.flush()
    _audit(db, actor, "OPENING_FINANCIAL_IMPORT_PARSED", import_file,
           {"company_id": str(batch.company_id), "batch_id": str(batch.id),
            "batch_number": batch.batch_number, "line_type": line_type,
            "file_name": import_file.file_name, "file_hash": import_file.file_hash,
            "rows": len(built), "valid": valid, "invalid": invalid})
    return {"import_file_id": str(import_file.id), "line_type": line_type, "rows": len(built),
            "valid": valid, "invalid": invalid, "line_ids": [str(l.id) for l in built]}


def list_lines(db, company_id, batch_id, line_type, *, status=None, limit=200, offset=0):
    batch = _require_fin_batch(db, company_id, batch_id)
    model = _LINE_MODEL.get(line_type)
    if model is None:
        raise OpeningFinancialError(f"Unknown financial line type '{line_type}'")
    stmt = select(model).where(model.batch_id == batch.id)
    if status:
        stmt = stmt.where(model.validation_status == status)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = db.execute(stmt.order_by(model.row_number, model.created_at)
                      .limit(max(1, min(limit, 1000))).offset(max(0, offset))).scalars().all()
    return batch, rows, total


def rejections(db, company_id, batch_id):
    batch = _require_fin_batch(db, company_id, batch_id)
    out = {}
    for lt, model in _LINE_MODEL.items():
        rows = db.execute(select(model).where(
            model.batch_id == batch.id, model.validation_status == _V.INVALID.value)).scalars().all()
        out[lt] = [{"line_id": str(r.id), "row_number": r.row_number,
                    "errors": json.loads(r.validation_errors) if r.validation_errors else []}
                   for r in rows]
    return batch, out


# --------------------------- CA decision register ---------------------------

def ensure_ca_register(db, batch, actor=None):
    existing = {d.decision_key for d in db.execute(select(OpeningCaDecision).where(
        OpeningCaDecision.batch_id == batch.id)).scalars().all()}
    for key, title in REQUIRED_CA_DECISIONS:
        if key not in existing:
            db.add(OpeningCaDecision(company_id=batch.company_id, batch_id=batch.id,
                                     decision_key=key, title=title, is_required=True,
                                     status=CaDecisionStatus.PENDING.value, created_by=actor))
    db.flush()


def ca_register(db, company_id, batch_id):
    batch = _require_fin_batch(db, company_id, batch_id)
    ensure_ca_register(db, batch)
    rows = db.execute(select(OpeningCaDecision).where(
        OpeningCaDecision.batch_id == batch.id).order_by(OpeningCaDecision.decision_key)).scalars().all()
    return batch, rows


def set_ca_decision(db, company_id, batch_id, decision_key, *, status, approved_treatment=None,
                    supporting_reference=None, reviewer_name=None, reviewer_id=None, notes=None):
    batch = _require_fin_batch(db, company_id, batch_id)
    ensure_ca_register(db, batch)
    d = db.execute(select(OpeningCaDecision).where(
        OpeningCaDecision.batch_id == batch.id,
        OpeningCaDecision.decision_key == decision_key)).scalars().first()
    if d is None:
        raise OpeningFinancialError(f"Unknown CA decision key '{decision_key}'")
    st = str(status).upper()
    if st not in {s.value for s in CaDecisionStatus}:
        raise OpeningFinancialError("invalid CA decision status")
    d.status = st
    d.approved_treatment = _clean(approved_treatment, 4000)
    d.supporting_reference = _clean(supporting_reference, 200)
    d.reviewer_name = _clean(reviewer_name, 120)
    d.reviewer_id = reviewer_id
    d.notes = _clean(notes, 4000)
    d.approved_at = _now() if st == CaDecisionStatus.APPROVED.value else None
    db.flush()
    _audit(db, reviewer_id, "OPENING_CA_DECISION", d,
           {"batch_id": str(batch.id), "decision_key": decision_key, "status": st,
            "reviewer": reviewer_name})
    return d


def _all_ca_approved(db, batch) -> bool:
    ensure_ca_register(db, batch)
    pending = db.execute(select(func.count()).select_from(OpeningCaDecision).where(
        OpeningCaDecision.batch_id == batch.id, OpeningCaDecision.is_required.is_(True),
        OpeningCaDecision.status != CaDecisionStatus.APPROVED.value)).scalar()
    return pending == 0


# ----------------------------- reconciliation -------------------------------

def _gl_totals(db, batch):
    lines = db.execute(select(OpeningGLLine).where(
        OpeningGLLine.batch_id == batch.id,
        OpeningGLLine.validation_status == _V.VALID.value)).scalars().all()
    dr = sum((_m(l.debit) or MZERO for l in lines), MZERO)
    cr = sum((_m(l.credit) or MZERO for l in lines), MZERO)
    return lines, quantize(dr, MONEY_SCALE), quantize(cr, MONEY_SCALE)


def _sum_outstanding(db, model, batch):
    rows = db.execute(select(model).where(
        model.batch_id == batch.id, model.validation_status == _V.VALID.value)).scalars().all()
    return rows, sum((_m(r.outstanding_amount) or MZERO for r in rows), MZERO)


def _inventory_subledger_total(db, company_id):
    rows = db.execute(select(StockLayer.value_remaining).where(
        StockLayer.company_id == company_id)).scalars().all()
    return sum((_m(v) or MZERO for v in rows), MZERO)


def reconcile(db, company_id, batch_id) -> dict:
    batch = _require_fin_batch(db, company_id, batch_id)
    gl_lines, dr, cr = _gl_totals(db, batch)
    tb_diff = quantize(dr - cr, MONEY_SCALE)

    # AR / AP sub-ledger vs their control-account GL lines.
    ar_rows, ar_total = _sum_outstanding(db, OpeningArLine, batch)
    ap_rows, ap_total = _sum_outstanding(db, OpeningApLine, batch)
    gl_by_code = {}
    for l in gl_lines:
        gl_by_code.setdefault(l.account_code, MZERO)
        gl_by_code[l.account_code] = quantize(
            gl_by_code[l.account_code] + (_m(l.debit) or MZERO) - (_m(l.credit) or MZERO), MONEY_SCALE)
    ar_gl = gl_by_code.get(_AR_CONTROL_DEFAULT, MZERO)                     # debit-positive
    ap_gl = quantize(-(gl_by_code.get(_AP_CONTROL_DEFAULT, MZERO)), MONEY_SCALE)  # credit-positive
    ar_ok = (not ar_rows) or abs(ar_total - ar_gl) <= _TOL
    ap_ok = (not ap_rows) or abs(ap_total - ap_gl) <= _TOL

    # Inventory control (GL) vs P1-P4 sub-ledger total.
    inv_gl = sum((gl_by_code.get(c, MZERO) for c in _INVENTORY_CONTROL_CODES), MZERO)
    inv_sub = _inventory_subledger_total(db, company_id)
    inv_ok = abs(inv_gl - inv_sub) <= _TOL

    # Fixed assets: sum NBV vs (asset cost GL - accumulated dep GL) is informational;
    # reconciliation check = each FA line's NBV arithmetic already validated.
    fa_rows = db.execute(select(OpeningFixedAssetLine).where(
        OpeningFixedAssetLine.batch_id == batch.id,
        OpeningFixedAssetLine.validation_status == _V.VALID.value)).scalars().all()
    fa_cost = sum((_m(r.original_cost) or MZERO for r in fa_rows), MZERO)
    fa_accum = sum((_m(r.accumulated_depreciation) or MZERO for r in fa_rows), MZERO)

    bank_rows = db.execute(select(OpeningBankLine).where(
        OpeningBankLine.batch_id == batch.id,
        OpeningBankLine.validation_status == _V.VALID.value)).scalars().all()

    balanced = (tb_diff == MZERO) and (dr > MZERO)
    reconciled = balanced and ar_ok and ap_ok and inv_ok
    return {
        "batch_id": str(batch.id),
        "trial_balance": {"debit": str(dr), "credit": str(cr), "difference": str(tb_diff),
                          "balanced": balanced, "gl_line_count": len(gl_lines)},
        "ar": {"subledger_outstanding": str(quantize(ar_total, MONEY_SCALE)),
               "gl_control_1040": str(ar_gl), "reconciled": ar_ok, "lines": len(ar_rows)},
        "ap": {"subledger_outstanding": str(quantize(ap_total, MONEY_SCALE)),
               "gl_control_2010": str(ap_gl), "reconciled": ap_ok, "lines": len(ap_rows)},
        "inventory": {"gl_control_total": str(inv_gl), "p1_p4_subledger_total": str(inv_sub),
                      "reconciled": inv_ok, "control_codes": sorted(_INVENTORY_CONTROL_CODES)},
        "fixed_assets": {"cost": str(fa_cost), "accumulated_depreciation": str(fa_accum),
                         "net_book_value": str(quantize(fa_cost - fa_accum, MONEY_SCALE)),
                         "lines": len(fa_rows)},
        "bank": {"lines": len(bank_rows)},
        "reconciled": reconciled,
    }


def _invalid_count(db, batch) -> int:
    n = 0
    for model in _LINE_MODEL.values():
        n += db.execute(select(func.count()).select_from(model).where(
            model.batch_id == batch.id, model.validation_status == _V.INVALID.value)).scalar()
    return n


def posting_state(db, company_id, batch_id) -> dict:
    batch = _require_fin_batch(db, company_id, batch_id)
    posted = pe.find_existing(db, batch.company_id, f"FINOPEN:{batch.id}")
    if posted is not None:
        from app.models.accounting import VoucherStatus
        st = (FinancialPostingState.REVERSED.value
              if VoucherStatus(posted.status) == VoucherStatus.REVERSED
              else FinancialPostingState.POSTED.value)
        return {"state": st, "voucher_number": posted.number,
                "posting_reference": posted.posting_reference, "voucher_status": str(posted.status)}
    rec = reconcile(db, company_id, batch_id)
    invalid = _invalid_count(db, batch)
    ca_ok = _all_ca_approved(db, batch)
    if batch.status == OpeningBatchStatus.DRAFT.value or invalid > 0:
        state = FinancialPostingState.DRAFT.value
    elif not rec["reconciled"]:
        state = FinancialPostingState.RECONCILIATION_REQUIRED.value
    elif not ca_ok:
        state = FinancialPostingState.CA_APPROVAL_PENDING.value
    elif batch.status == OpeningBatchStatus.APPROVED.value:
        state = FinancialPostingState.READY_FOR_AUTHORISED_POSTING.value
    else:
        state = FinancialPostingState.VALIDATED.value
    return {"state": state, "batch_status": batch.status, "reconciled": rec["reconciled"],
            "ca_all_approved": ca_ok, "invalid_lines": invalid}


# ------------------------- submit / approve / post --------------------------

def _assert_ready_to_submit(db, batch):
    total = 0
    for model in _LINE_MODEL.values():
        total += db.execute(select(func.count()).select_from(model).where(
            model.batch_id == batch.id)).scalar()
    if total == 0:
        raise OpeningFinancialStateError("The financial opening batch has no lines")
    if _invalid_count(db, batch) > 0:
        raise OpeningFinancialStateError("Invalid lines present; correct them before submission")


def submit_batch(db, company_id, batch_id, *, submitted_by, actor_name=None):
    batch = _require_fin_batch(db, company_id, batch_id)
    if batch.status == OpeningBatchStatus.DRAFT.value:
        _assert_ready_to_submit(db, batch)
        ensure_ca_register(db, batch, actor=submitted_by)
    return batch_svc.submit_batch(db, company_id, batch_id,
                                  submitted_by=submitted_by, actor_name=actor_name)


def approve_batch(db, company_id, batch_id, *, approved_by, actor_name=None):
    """P1 maker-checker approval ONLY. Does NOT post — posting is a separate,
    CA-gated, explicitly-authorised action (``post_financial``)."""
    batch = _require_fin_batch(db, company_id, batch_id)
    if batch.status == OpeningBatchStatus.SUBMITTED.value:
        if _invalid_count(db, batch) > 0:
            raise OpeningFinancialStateError("Invalid lines present; cannot approve")
    return batch_svc.approve_batch(db, company_id, batch_id,
                                   approved_by=approved_by, actor_name=actor_name)


def post_financial(db, company_id, batch_id, *, authorize: bool = False, posted_by=None,
                   actor_name=None):
    """HARD-GATED posting of the single balanced OB opening voucher. Blocks unless
    the batch is APPROVED, every required CA decision is APPROVED, every
    reconciliation passes, and ``authorize=True`` is explicitly supplied."""
    batch = _require_fin_batch(db, company_id, batch_id)
    ref = f"FINOPEN:{batch.id}"
    existing = pe.find_existing(db, batch.company_id, ref)
    if existing is not None:
        return existing, 0   # idempotent: already posted

    if not authorize:
        raise PostingBlockedError(
            "Financial opening posting requires explicit authorisation (authorize=True).")
    if batch.status != OpeningBatchStatus.APPROVED.value:
        raise PostingBlockedError(f"Batch must be APPROVED to post (is {batch.status}).")
    if _invalid_count(db, batch) > 0:
        raise PostingBlockedError("Invalid lines present; cannot post.")
    if not _all_ca_approved(db, batch):
        raise PostingBlockedError("All required CA decisions must be APPROVED before posting.")
    rec = reconcile(db, company_id, batch_id)
    if not rec["reconciled"]:
        raise PostingBlockedError(f"Reconciliation failed; cannot post. {json.dumps(rec)}")

    gl_lines, dr, cr = _gl_totals(db, batch)
    if dr != cr or dr <= MZERO:
        raise PostingBlockedError("Trial balance is not balanced; cannot post.")

    # Build posting lines from the GL trial balance; split AR/AP control lines into
    # party-tagged lines from the sub-ledger detail when it reconciles exactly.
    posting_lines: list[pe.PostingLine] = []
    ar_rows = db.execute(select(OpeningArLine).where(
        OpeningArLine.batch_id == batch.id, OpeningArLine.validation_status == _V.VALID.value)).scalars().all()
    ap_rows = db.execute(select(OpeningApLine).where(
        OpeningApLine.batch_id == batch.id, OpeningApLine.validation_status == _V.VALID.value)).scalars().all()
    ar_by_cust: dict = {}
    for r in ar_rows:
        ar_by_cust[r.customer_id] = quantize(ar_by_cust.get(r.customer_id, MZERO)
                                             + (_m(r.outstanding_amount) or MZERO), MONEY_SCALE)
    ap_by_sup: dict = {}
    for r in ap_rows:
        ap_by_sup[r.supplier_id] = quantize(ap_by_sup.get(r.supplier_id, MZERO)
                                            + (_m(r.outstanding_amount) or MZERO), MONEY_SCALE)

    for l in gl_lines:
        d = _m(l.debit) or MZERO
        c = _m(l.credit) or MZERO
        if l.account_code == _AR_CONTROL_DEFAULT and ar_by_cust and rec["ar"]["reconciled"]:
            for cust_id, amt in ar_by_cust.items():
                if amt > MZERO:
                    posting_lines.append(pe.PostingLine(
                        account_code=_AR_CONTROL_DEFAULT, debit=amt,
                        narration="Opening receivable", party_type="customer", party_id=cust_id))
        elif l.account_code == _AP_CONTROL_DEFAULT and ap_by_sup and rec["ap"]["reconciled"]:
            for sup_id, amt in ap_by_sup.items():
                if amt > MZERO:
                    posting_lines.append(pe.PostingLine(
                        account_code=_AP_CONTROL_DEFAULT, credit=amt,
                        narration="Opening payable", party_type="supplier", party_id=sup_id))
        else:
            posting_lines.append(pe.PostingLine(
                account_code=l.account_code, debit=d, credit=c, narration=l.narration or "Opening balance"))

    voucher = pe.post_voucher(db, pe.PostingRequest(
        company_id=batch.company_id, voucher_type_code=_FINOPEN_VT,
        voucher_date=batch.effective_date, posting_reference=ref,
        narration=f"Financial opening {batch.batch_number}",
        source_module="opening_financial", source_id=str(batch.id),
        created_by=posted_by, approved_by=posted_by, actor_name=actor_name,
        lines=posting_lines))

    now = _now()
    for model in _LINE_MODEL.values():
        for l in db.execute(select(model).where(
                model.batch_id == batch.id, model.validation_status == _V.VALID.value)).scalars().all():
            l.validation_status = _V.COMMITTED.value
            l.committed_at = now
    db.flush()
    _audit(db, posted_by, "OPENING_FINANCIAL_POSTED", batch,
           {"company_id": str(batch.company_id), "batch_number": batch.batch_number,
            "voucher_number": voucher.number, "posting_reference": ref,
            "total": str(dr), "lines": len(posting_lines)})
    # Cutover lock: close the batch (terminal) once posted.
    batch_svc.close_batch(db, company_id, batch_id, closed_by=posted_by, actor_name=actor_name)
    return voucher, len(posting_lines)


def reverse_financial(db, company_id, batch_id, *, reason, reversed_by=None, actor_name=None):
    batch = _require_fin_batch(db, company_id, batch_id)
    ref = f"FINOPEN:{batch.id}"
    voucher = pe.find_existing(db, batch.company_id, ref)
    if voucher is None:
        raise OpeningFinancialStateError("No posted financial opening voucher to reverse")
    from app.models.accounting import VoucherStatus
    if VoucherStatus(voucher.status) != VoucherStatus.POSTED:
        raise OpeningFinancialStateError(f"Voucher is not POSTED (is {voucher.status})")
    rev = pe.reverse_voucher(db, voucher.id, reason=reason, created_by=reversed_by,
                             reversal_date=batch.effective_date, actor_name=actor_name)
    _audit(db, reversed_by, "OPENING_FINANCIAL_REVERSED", batch,
           {"company_id": str(batch.company_id), "batch_number": batch.batch_number,
            "original": voucher.number, "reversal": rev.number, "reason": reason})
    return rev
