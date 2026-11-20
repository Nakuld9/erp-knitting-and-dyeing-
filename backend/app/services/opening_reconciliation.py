"""Opening reconciliation engine (Stage 6F-P6).

Consolidates the P1-P5 reconciliation primitives into company-wide, factory-aware,
persistent reconciliation runs + discrepancy records. Every computation is
**read/compute only**: it SELECTs from existing opening/ledger/GST tables and writes
ONLY its own ``OpeningReconciliationRun`` / ``OpeningDiscrepancy`` / audit rows. It
never posts a voucher, never creates a stock layer / inventory transaction / roll /
lot / job-work record, and never invents an account mapping — a missing or ambiguous
mapping is reported as a discrepancy and marks the run incomplete.

Components:
  P6A inventory + vendor-custody · P6B AR/AP · P6C bank/cash + fixed assets ·
  P6D GST/statutory · P6E consolidated dashboard · status lifecycle + discrepancies.
"""
from __future__ import annotations

import json
import uuid
from calendar import monthrange
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.accounting import ChartOfAccount, JournalLine, Voucher, VoucherStatus
from app.models.assets import FixedAsset
from app.models.hybrid import WarehouseCategory
from app.models.inventory import ItemType, StockLayer
from app.models.masters import BankAccount, Customer, Supplier, Warehouse
from app.models.opening import OpeningBatch
from app.models.opening_financial import (
    OpeningApLine,
    OpeningArLine,
    OpeningBankLine,
    OpeningCaDecision,
    OpeningFixedAssetLine,
    OpeningGLLine,
    OpeningStatutoryLine,
    CaDecisionStatus,
)
from app.models.opening_reconciliation import (
    ALLOWED_TRANSITIONS,
    DiscrepancyStatus,
    OpeningDiscrepancy,
    OpeningReconciliationRun,
    ReconStatus,
    ReconType,
)
from app.models.opening_stock import OpeningLineValidation
from app.services.accounting import trial_balance as tb
from app.services.accounting.posting_rules import resolve_account
from app.services.audit import record_audit

MZERO = quantize(0, MONEY_SCALE)
_TOL = Decimal("0.01")
_V_OK = (OpeningLineValidation.VALID.value, OpeningLineValidation.COMMITTED.value)
_POSTED = [VoucherStatus.POSTED.value, VoucherStatus.REVERSED.value, VoucherStatus.REVERSAL.value]

# Verified inventory control accounts (from chart_of_accounts + posting_rules DEFAULTS).
_AR_CONTROL = "1040"
_AP_CONTROL = "2010"
_ACCUM_DEP = "1590"
# item_type -> control account for the manufacturing stock whose mapping is fixed in
# DEFAULT_RULES / the seeded chart. Trading fabric + stores are resolved dynamically
# (company-configured) and reported as missing if unmapped — never invented.
_YARN_ACC = "1060"
_DYE_ACC = "1090"
_CHEM_ACC = "1100"
_GREIGE_ACC = "1070"
_FINISHED_ACC = "1085"

# GST / statutory heads (verified against the seeded chart).
_GST_HEADS = {
    "GST Input CGST": "1121", "GST Input SGST": "1122", "GST Input IGST": "1123",
    "GST Output CGST": "2041", "GST Output SGST": "2042", "GST Output IGST": "2043",
    "TDS Payable": "2050",
}


class OpeningReconError(ValueError):
    """An invalid opening-reconciliation request."""


class OpeningReconStateError(OpeningReconError):
    """An illegal reconciliation status transition or action."""


# ------------------------------- helpers ------------------------------------

def _now():
    return datetime.now(timezone.utc)


def _m(v):
    if v is None or str(v).strip() == "":
        return MZERO
    try:
        return quantize(Decimal(str(v)), MONEY_SCALE)
    except Exception:  # noqa: BLE001
        return MZERO


def _eom(d: date) -> date:
    return date(d.year, d.month, monthrange(d.year, d.month)[1])


def _account(db, company_id, code):
    return db.execute(select(ChartOfAccount).where(
        ChartOfAccount.company_id == company_id, ChartOfAccount.code == code)).scalars().first()


def _gl_balance(db, company_id, code, as_of):
    """Signed GL balance (debit - credit) for an account as of a date (posted only)."""
    acct = _account(db, company_id, code)
    if acct is None:
        return None
    rows = db.execute(
        select(JournalLine.debit, JournalLine.credit)
        .join(Voucher, Voucher.id == JournalLine.voucher_id)
        .where(Voucher.company_id == company_id, JournalLine.account_id == acct.id,
               Voucher.status.in_(_POSTED),
               (Voucher.voucher_date <= as_of) if as_of else (Voucher.id == Voucher.id))).all()
    bal = MZERO
    for dr, cr in rows:
        bal = quantize(bal + _m(dr) - _m(cr), MONEY_SCALE)
    return bal


def _disc(category, *, expected, actual, discrepancy_key, account_code=None,
          inventory_category=None, source_batch_id=None, source_reference=None,
          qty_difference=None, explanation=None):
    exp, act = _m(expected), _m(actual)
    return {"recon_category": category, "account_code": account_code,
            "inventory_category": inventory_category, "source_batch_id": source_batch_id,
            "source_reference": source_reference, "discrepancy_key": discrepancy_key,
            "expected_value": exp, "actual_value": act,
            "difference": quantize(exp - act, MONEY_SCALE),
            "qty_difference": qty_difference, "explanation": explanation}


def _opening_batches(db, company_id, factory_id=None):
    stmt = select(OpeningBatch).where(OpeningBatch.company_id == company_id)
    if factory_id:
        stmt = stmt.where(OpeningBatch.factory_id == factory_id)
    return db.execute(stmt).scalars().all()


# ----------------------------- P6A inventory --------------------------------

_CATEGORY_LABELS = [c.value for c in WarehouseCategory]


def compute_inventory(db, company_id, *, as_of, factory_id=None) -> tuple[dict, list[dict]]:
    """Reconcile perpetual opening stock vs GL inventory control accounts, with a
    warehouse-category + factory presentation. Vendor custody is counted exactly once
    (each stock layer belongs to one warehouse). Trading-fabric and stores control
    accounts are resolved from company configuration; if unmapped the run is marked
    incomplete and a MAPPING_MISSING discrepancy is raised (never invented)."""
    discs: list[dict] = []
    incomplete = False

    # --- warehouse-category + factory presentation (no double count) ---
    wh = {w.id: w for w in db.execute(select(Warehouse).where(
        Warehouse.company_id == company_id)).scalars().all()}
    cat_view: dict = {c: {"value": MZERO, "qty": Decimal("0")} for c in _CATEGORY_LABELS}
    cat_view["UNASSIGNED"] = {"value": MZERO, "qty": Decimal("0")}
    factory_view: dict = {}
    layers = db.execute(select(StockLayer).where(StockLayer.company_id == company_id)).scalars().all()
    for lyr in layers:
        w = wh.get(lyr.warehouse_id) if lyr.warehouse_id else None
        if factory_id and (w is None or w.branch_id != factory_id):
            continue
        cat = (w.category if w and w.category in cat_view else "UNASSIGNED")
        cat_view[cat]["value"] = quantize(cat_view[cat]["value"] + _m(lyr.value_remaining), MONEY_SCALE)
        cat_view[cat]["qty"] = cat_view[cat]["qty"] + Decimal(str(lyr.qty_remaining or 0))
        fk = str(w.branch_id) if w and w.branch_id else "UNASSIGNED"
        factory_view.setdefault(fk, MZERO)
        factory_view[fk] = quantize(factory_view[fk] + _m(lyr.value_remaining), MONEY_SCALE)

    # --- GL control tie-out by item type (the real backing accounts) ---
    def _stock_value(item_type_value, *, dye=None):
        q = select(func.coalesce(func.sum(StockLayer.value_remaining), 0)).where(
            StockLayer.company_id == company_id, StockLayer.item_type == item_type_value)
        total = _m(db.execute(q).scalar())
        return total

    control_rows = []

    def _control(label, code, stock_value, *, batch_hint=None):
        nonlocal incomplete
        gl = _gl_balance(db, company_id, code, as_of)
        if gl is None:
            incomplete = True
            discs.append(_disc(ReconType.INVENTORY.value, expected=stock_value, actual=0,
                               account_code=code, inventory_category=label,
                               discrepancy_key=f"INV|MAPPING|{label}",
                               explanation=f"inventory control account {code} not found in chart"))
            control_rows.append({"label": label, "account": code, "stock_value": str(stock_value),
                                 "gl_value": None, "difference": None, "mapped": False})
            return
        diff = quantize(gl - stock_value, MONEY_SCALE)
        control_rows.append({"label": label, "account": code, "stock_value": str(stock_value),
                             "gl_value": str(gl), "difference": str(diff),
                             "mapped": True, "reconciled": abs(diff) <= _TOL})
        if abs(diff) > _TOL:
            discs.append(_disc(ReconType.INVENTORY.value, expected=gl, actual=stock_value,
                               account_code=code, inventory_category=label,
                               discrepancy_key=f"INV|{code}",
                               explanation=f"{label}: GL control {gl} vs opening sub-ledger {stock_value}"))

    # Yarn / dye / chemical: split chemical into dye vs non-dye like stock_reconciliation.
    from app.services.accounting.stock_reconciliation import _stock_value as _srv  # reuse exact logic
    _control("Yarn Inventory", _YARN_ACC, _srv(db, company_id, ItemType.YARN, None))
    _control("Dye Inventory", _DYE_ACC, _srv(db, company_id, ItemType.CHEMICAL, True))
    _control("Chemical Inventory", _CHEM_ACC, _srv(db, company_id, ItemType.CHEMICAL, False))
    # Fabric split greige vs finished (roll-level identity preserved; value only here).
    from app.services.accounting.stock_reconciliation import _fabric_stock_split
    greige_val, finished_val = _fabric_stock_split(db, company_id)
    _control("Greige Fabric Inventory", _GREIGE_ACC, greige_val)
    _control("Finished Fabric Inventory", _FINISHED_ACC, finished_val)

    # Trading fabric: control account is company-configured; resolve or, only when
    # trading stock actually exists, report the missing mapping (never invented).
    trade_val = _stock_value(ItemType.TRADING_FABRIC.value)
    if trade_val > MZERO:
        try:
            trade_acc = resolve_account(db, company_id, "TRADING_GRN", "inventory")
            _control("Trading Fabric Inventory", trade_acc, trade_val)
        except KeyError:
            incomplete = True
            control_rows.append({"label": "Trading Fabric Inventory", "account": None,
                                 "stock_value": str(trade_val), "gl_value": None,
                                 "difference": None, "mapped": False})
            discs.append(_disc(
                ReconType.INVENTORY.value, expected=trade_val, actual=0,
                inventory_category="Trading Fabric Inventory",
                discrepancy_key="INV|MAPPING|TRADING_FABRIC",
                explanation=("Trading-fabric inventory control account is not configured "
                             "(no PostingRule for TRADING_GRN/inventory). Configure the "
                             "mapping before cutover; not invented.")))

    # Stores / packing: ambiguous mapping (1110 packing vs 1115 stores) -> report, don't invent.
    store_val = _stock_value(ItemType.STORE.value)
    if store_val > MZERO:
        incomplete = True
        discs.append(_disc(
            ReconType.INVENTORY.value, expected=store_val, actual=0,
            inventory_category="Stores/Packing Inventory",
            discrepancy_key="INV|MAPPING|STORE",
            explanation=("Stores/packing opening stock present but the control-account split "
                         "(1110 packing vs 1115 stores) is ambiguous; configure explicit "
                         "mapping before cutover.")))

    total_stock = quantize(sum((_m(l.value_remaining) for l in layers), MZERO), MONEY_SCALE)
    summary = {
        "as_of": as_of.isoformat() if as_of else None,
        "warehouse_categories": {k: {"value": str(v["value"]), "qty": str(v["qty"])}
                                 for k, v in cat_view.items()},
        "factory_breakdown": {k: str(v) for k, v in factory_view.items()},
        "vendor_custody_value": str(cat_view[WarehouseCategory.VENDOR_CUSTODY.value]["value"]),
        "vendor_custody_note": "counted once within its owning company's item-type control totals",
        "control_accounts": control_rows,
        "total_stock_value": str(total_stock),
        "incomplete": incomplete,
    }
    return summary, discs


# ----------------------------- P6B AR / AP ----------------------------------

_AGE_BUCKETS = [(0, 30), (31, 60), (61, 90), (91, 180), (181, 10 ** 9)]


def _ageing(days):
    for lo, hi in _AGE_BUCKETS:
        if lo <= days <= hi:
            return f"{lo}-{hi if hi < 10 ** 9 else 'plus'}"
    return "current" if days < 0 else "180-plus"


def _ar_ap_common(db, company_id, model, *, as_of, control_code, kind_attr, party_model, party_fk):
    lines = db.execute(select(model).where(
        model.company_id == company_id, model.validation_status.in_(_V_OK))).scalars().all()
    by_party: dict = {}
    ageing: dict = {}
    advances = unallocated = credit_bal = MZERO
    subledger = MZERO
    invoice_detail = []
    for l in lines:
        amt = _m(l.outstanding_amount)
        kind = getattr(l, kind_attr)
        pid = getattr(l, party_fk)
        by_party.setdefault(str(pid), MZERO)
        by_party[str(pid)] = quantize(by_party[str(pid)] + amt, MONEY_SCALE)
        subledger = quantize(subledger + amt, MONEY_SCALE)
        if kind == "ADVANCE":
            advances = quantize(advances + amt, MONEY_SCALE)
        elif kind in ("UNALLOCATED_RECEIPT", "UNALLOCATED_PAYMENT"):
            unallocated = quantize(unallocated + amt, MONEY_SCALE)
        elif kind == "CREDIT_BALANCE":
            credit_bal = quantize(credit_bal + amt, MONEY_SCALE)
        days = (as_of - l.invoice_date).days if (as_of and l.invoice_date) else 0
        bucket = _ageing(days)
        ageing.setdefault(bucket, MZERO)
        ageing[bucket] = quantize(ageing[bucket] + amt, MONEY_SCALE)
        invoice_detail.append({"party_id": str(pid), "kind": kind, "invoice_no": l.invoice_no,
                               "invoice_date": l.invoice_date.isoformat() if l.invoice_date else None,
                               "outstanding": str(amt), "ageing": bucket})
    gl = _gl_balance(db, company_id, control_code, as_of)
    discs = []
    incomplete = False
    if gl is None:
        incomplete = True
        discs.append(_disc(
            (ReconType.CUSTOMER_AR.value if model is OpeningArLine else ReconType.SUPPLIER_AP.value),
            expected=subledger, actual=0, account_code=control_code,
            discrepancy_key=f"{'AR' if model is OpeningArLine else 'AP'}|MAPPING|{control_code}",
            explanation=f"control account {control_code} not found in chart"))
        gl_cmp = None
    else:
        # AR control is debit-positive (gl); AP control is credit-positive (-gl).
        gl_cmp = gl if model is OpeningArLine else quantize(-gl, MONEY_SCALE)
        diff = quantize(gl_cmp - subledger, MONEY_SCALE)
        if abs(diff) > _TOL:
            discs.append(_disc(
                (ReconType.CUSTOMER_AR.value if model is OpeningArLine else ReconType.SUPPLIER_AP.value),
                expected=gl_cmp, actual=subledger, account_code=control_code,
                discrepancy_key=f"{'AR' if model is OpeningArLine else 'AP'}|{control_code}",
                explanation=f"control {control_code} GL {gl_cmp} vs sub-ledger {subledger}"))
    summary = {
        "as_of": as_of.isoformat() if as_of else None, "control_account": control_code,
        "subledger_total": str(subledger), "gl_control": (str(gl_cmp) if gl is not None else None),
        "party_count": len(by_party),
        "party_totals": {k: str(v) for k, v in by_party.items()},
        "ageing": {k: str(v) for k, v in ageing.items()},
        "advances": str(advances), "unallocated": str(unallocated), "credit_balances": str(credit_bal),
        "invoice_detail": invoice_detail, "incomplete": incomplete,
    }
    return summary, discs


def compute_ar(db, company_id, *, as_of, factory_id=None):
    return _ar_ap_common(db, company_id, OpeningArLine, as_of=as_of, control_code=_AR_CONTROL,
                         kind_attr="ar_kind", party_model=Customer, party_fk="customer_id")


def compute_ap(db, company_id, *, as_of, factory_id=None):
    return _ar_ap_common(db, company_id, OpeningApLine, as_of=as_of, control_code=_AP_CONTROL,
                         kind_attr="ap_kind", party_model=Supplier, party_fk="supplier_id")


# -------------------------- P6C bank / cash / FA ----------------------------

def compute_bank_cash(db, company_id, *, as_of, factory_id=None):
    lines = db.execute(select(OpeningBankLine).where(
        OpeningBankLine.company_id == company_id,
        OpeningBankLine.validation_status.in_(_V_OK))).scalars().all()
    discs = []
    rows = []
    cash_total = bank_total = MZERO
    for l in lines:
        book = _m(l.book_balance)
        stmt = _m(l.statement_balance)
        oc = _m(l.outstanding_cheques)
        dit = _m(l.deposits_in_transit)
        # BRS identity: book = statement + deposits_in_transit - outstanding_cheques.
        adjusted_statement = quantize(stmt + dit - oc, MONEY_SCALE)
        recon_diff = quantize(book - adjusted_statement, MONEY_SCALE)
        gl = _gl_balance(db, company_id, l.account_code, as_of) if l.account_code else None
        gl_diff = quantize((gl if gl is not None else MZERO) - book, MONEY_SCALE)
        rows.append({"account_code": l.account_code, "is_cash": l.is_cash,
                     "book_balance": str(book), "statement_balance": str(stmt),
                     "outstanding_cheques": str(oc), "deposits_in_transit": str(dit),
                     "adjusted_statement": str(adjusted_statement), "recon_difference": str(recon_diff),
                     "gl_balance": (str(gl) if gl is not None else None),
                     "gl_difference": (str(gl_diff) if gl is not None else None),
                     "reconciled": abs(recon_diff) <= _TOL})
        if l.is_cash:
            cash_total = quantize(cash_total + book, MONEY_SCALE)
        else:
            bank_total = quantize(bank_total + book, MONEY_SCALE)
        if abs(recon_diff) > _TOL:
            discs.append(_disc(ReconType.BANK_CASH.value, expected=adjusted_statement, actual=book,
                               account_code=l.account_code,
                               discrepancy_key=f"BANK|{l.account_code}|{l.bank_account_id or ''}",
                               explanation=("book vs (statement + deposits-in-transit - outstanding "
                                            f"cheques) differ by {recon_diff}")))
        elif gl is not None and abs(gl_diff) > _TOL:
            discs.append(_disc(ReconType.BANK_CASH.value, expected=gl, actual=book,
                               account_code=l.account_code,
                               discrepancy_key=f"BANK|GL|{l.account_code}",
                               explanation=f"bank/cash GL {gl} vs opening book balance {book}"))
    summary = {"as_of": as_of.isoformat() if as_of else None, "lines": rows,
               "cash_total": str(cash_total), "bank_total": str(bank_total),
               "total": str(quantize(cash_total + bank_total, MONEY_SCALE))}
    return summary, discs


def compute_fixed_asset(db, company_id, *, as_of, factory_id=None):
    lines = db.execute(select(OpeningFixedAssetLine).where(
        OpeningFixedAssetLine.company_id == company_id,
        OpeningFixedAssetLine.validation_status.in_(_V_OK))).scalars().all()
    discs = []
    by_cost_acc: dict = {}
    by_accum_acc: dict = {}
    tot_cost = tot_accum = MZERO
    detail = []
    for l in lines:
        cost = _m(l.original_cost)
        accum = _m(l.accumulated_depreciation)
        nbv = quantize(cost - accum, MONEY_SCALE)
        tot_cost = quantize(tot_cost + cost, MONEY_SCALE)
        tot_accum = quantize(tot_accum + accum, MONEY_SCALE)
        ca = l.asset_account_code
        aa = l.accum_dep_account_code or _ACCUM_DEP
        by_cost_acc[ca] = quantize(by_cost_acc.get(ca, MZERO) + cost, MONEY_SCALE)
        by_accum_acc[aa] = quantize(by_accum_acc.get(aa, MZERO) + accum, MONEY_SCALE)
        detail.append({"asset_code": l.asset_code, "category": l.category,
                       "cost": str(cost), "accumulated_depreciation": str(accum), "nbv": str(nbv),
                       "cost_account": ca, "accum_account": aa})
    for code, cost in by_cost_acc.items():
        gl = _gl_balance(db, company_id, code, as_of)
        if gl is None:
            continue
        diff = quantize(gl - cost, MONEY_SCALE)
        if abs(diff) > _TOL:
            discs.append(_disc(ReconType.FIXED_ASSET.value, expected=gl, actual=cost,
                               account_code=code, discrepancy_key=f"FA|COST|{code}",
                               explanation=f"asset cost GL {code} {gl} vs register {cost}"))
    for code, accum in by_accum_acc.items():
        gl = _gl_balance(db, company_id, code, as_of)
        if gl is None:
            continue
        gl_cmp = quantize(-gl, MONEY_SCALE)   # accumulated depreciation is credit-positive
        diff = quantize(gl_cmp - accum, MONEY_SCALE)
        if abs(diff) > _TOL:
            discs.append(_disc(ReconType.FIXED_ASSET.value, expected=gl_cmp, actual=accum,
                               account_code=code, discrepancy_key=f"FA|ACCUM|{code}",
                               explanation=f"accumulated depreciation GL {code} {gl_cmp} vs register {accum}"))
    summary = {"as_of": as_of.isoformat() if as_of else None,
               "total_cost": str(tot_cost), "total_accumulated_depreciation": str(tot_accum),
               "total_nbv": str(quantize(tot_cost - tot_accum, MONEY_SCALE)),
               "by_cost_account": {k: str(v) for k, v in by_cost_acc.items()},
               "by_accum_account": {k: str(v) for k, v in by_accum_acc.items()},
               "assets": detail}
    return summary, discs


# -------------------------- P6D GST / statutory -----------------------------

def compute_gst_statutory(db, company_id, *, as_of, factory_id=None):
    discs = []
    # Opening statutory lines grouped by account.
    lines = db.execute(select(OpeningStatutoryLine).where(
        OpeningStatutoryLine.company_id == company_id,
        OpeningStatutoryLine.validation_status.in_(_V_OK))).scalars().all()
    by_acc: dict = {}
    for l in lines:
        net = quantize(_m(l.debit) - _m(l.credit), MONEY_SCALE)
        by_acc[l.account_code] = quantize(by_acc.get(l.account_code, MZERO) + net, MONEY_SCALE)
    statutory_rows = []
    for code, net in by_acc.items():
        gl = _gl_balance(db, company_id, code, as_of)
        statutory_rows.append({"account": code, "opening_net": str(net),
                               "gl_balance": (str(gl) if gl is not None else None),
                               "difference": (str(quantize(gl - net, MONEY_SCALE)) if gl is not None else None)})
        if gl is not None and abs(quantize(gl - net, MONEY_SCALE)) > _TOL:
            discs.append(_disc(ReconType.GST_STATUTORY.value, expected=gl, actual=net,
                               account_code=code, discrepancy_key=f"STAT|{code}",
                               explanation=f"statutory account {code}: GL {gl} vs opening {net}"))
    # GST ledger-account balances (ERP GL) — informational, distinct from portal data.
    gst_gl = {}
    for label, code in _GST_HEADS.items():
        gl = _gl_balance(db, company_id, code, as_of)
        gst_gl[label] = {"account": code, "gl_balance": (str(gl) if gl is not None else None)}
    # ITC lifecycle counts from the P5 GST module (ERP eligibility tracking, not GL).
    itc_counts = {}
    portal_imports = 0
    try:
        from app.models.gst_itc import GstReturnImport, ItcInvoiceStatus, ItcStatus
        for st in ItcStatus:
            n = db.execute(select(func.count()).select_from(ItcInvoiceStatus).where(
                ItcInvoiceStatus.company_id == company_id,
                ItcInvoiceStatus.status == st.value)).scalar()
            if n:
                itc_counts[st.value] = n
        portal_imports = db.execute(select(func.count()).select_from(GstReturnImport).where(
            GstReturnImport.company_id == company_id)).scalar() or 0
    except Exception:  # noqa: BLE001 - GST module optional at this layer
        pass
    summary = {
        "as_of": as_of.isoformat() if as_of else None,
        "statutory_opening": statutory_rows,
        "gst_erp_gl": gst_gl,
        "itc_tracking_counts": itc_counts,
        "portal_import_count": portal_imports,
        "note": ("ERP accounting balances, imported GST portal statements and ITC eligibility "
                 "tracking are distinct and not interchangeable."),
    }
    return summary, discs


# ----------------------------- trial balance --------------------------------

def compute_trial_balance(db, company_id, *, as_of, factory_id=None):
    res = tb.trial_balance(db, company_id, date_to=as_of) if as_of else tb.trial_balance(db, company_id)
    totals = res.get("totals", {})
    dr = _m(totals.get("closing_debit"))
    cr = _m(totals.get("closing_credit"))
    diff = quantize(dr - cr, MONEY_SCALE)
    discs = []
    if abs(diff) > _TOL:
        discs.append(_disc(ReconType.TRIAL_BALANCE.value, expected=dr, actual=cr,
                           account_code=None, discrepancy_key="TB|GLOBAL",
                           explanation=f"trial balance debit {dr} != credit {cr}"))
    return {"as_of": as_of.isoformat() if as_of else None, "total_debit": str(dr),
            "total_credit": str(cr), "difference": str(diff), "balanced": abs(diff) <= _TOL}, discs


_COMPUTE = {
    ReconType.INVENTORY.value: compute_inventory,
    ReconType.CUSTOMER_AR.value: compute_ar,
    ReconType.SUPPLIER_AP.value: compute_ap,
    ReconType.BANK_CASH.value: compute_bank_cash,
    ReconType.FIXED_ASSET.value: compute_fixed_asset,
    ReconType.GST_STATUTORY.value: compute_gst_statutory,
    ReconType.TRIAL_BALANCE.value: compute_trial_balance,
}


# ------------------------------- run / persist ------------------------------

def _next_run_number(db, company_id, recon_type) -> int:
    last = db.execute(select(func.max(OpeningReconciliationRun.run_number)).where(
        OpeningReconciliationRun.company_id == company_id,
        OpeningReconciliationRun.recon_type == recon_type)).scalar()
    return (last or 0) + 1


def run_reconciliation(db: Session, company_id: uuid.UUID, *, recon_type: str,
                       effective_date: date | None = None, factory_id: uuid.UUID | None = None,
                       actor: uuid.UUID | None = None) -> OpeningReconciliationRun:
    """Execute a reconciliation (read/compute only) and persist the run + its
    discrepancies. A re-run creates a new run (history preserved); discrepancies are
    de-duplicated within a run by their stable key."""
    if recon_type not in {t.value for t in ReconType}:
        raise OpeningReconError(f"unknown recon_type '{recon_type}'")
    as_of = effective_date
    run = OpeningReconciliationRun(
        company_id=company_id, factory_id=factory_id, recon_type=recon_type,
        effective_date=effective_date, run_number=_next_run_number(db, company_id, recon_type),
        status=ReconStatus.IN_PROGRESS.value, started_at=_now(), initiated_by=actor,
        created_by=actor, updated_by=actor)
    db.add(run)
    db.flush()

    summaries: dict = {}
    all_discs: list[dict] = []
    incomplete = False
    try:
        targets = ([recon_type] if recon_type != ReconType.CONSOLIDATED.value
                   else [t for t in _COMPUTE])
        for t in targets:
            summary, discs = _COMPUTE[t](db, company_id, as_of=as_of, factory_id=factory_id)
            summaries[t] = summary
            incomplete = incomplete or bool(summary.get("incomplete"))
            all_discs.extend(discs)
    except Exception as exc:  # noqa: BLE001 - record the failure, post nothing
        run.status = ReconStatus.RECONCILIATION_REQUIRED.value
        run.error_detail = str(exc)[:4000]
        run.completed_at = _now()
        db.flush()
        raise

    seen = set()
    persisted = 0
    for d in all_discs:
        key = d["discrepancy_key"]
        if key in seen:
            continue
        seen.add(key)
        db.add(OpeningDiscrepancy(
            company_id=company_id, factory_id=factory_id, run_id=run.id,
            recon_category=d["recon_category"], account_code=d.get("account_code"),
            inventory_category=d.get("inventory_category"), source_batch_id=d.get("source_batch_id"),
            source_reference=d.get("source_reference"), discrepancy_key=key,
            expected_value=d["expected_value"], actual_value=d["actual_value"],
            difference=d["difference"], qty_difference=d.get("qty_difference"),
            explanation=d.get("explanation"),
            resolution_status=DiscrepancyStatus.UNRESOLVED.value,
            created_by=actor, updated_by=actor))
        persisted += 1

    run.discrepancy_count = persisted
    run.incomplete = incomplete
    run.summary = json.dumps(summaries, default=str)
    run.completed_at = _now()
    if incomplete:
        run.status = ReconStatus.RECONCILIATION_REQUIRED.value
    elif persisted > 0:
        run.status = ReconStatus.DISCREPANCY_FOUND.value
    else:
        run.status = ReconStatus.RESOLVED.value
    db.flush()
    record_audit(db, action="OPENING_RECONCILED", entity="opening_reconciliation_run",
                 entity_id=str(run.id), actor_id=actor,
                 detail={"company_id": str(company_id), "recon_type": recon_type,
                         "run_number": run.run_number, "discrepancies": persisted,
                         "incomplete": incomplete, "status": run.status})
    return run


def get_run(db, company_id, run_id) -> OpeningReconciliationRun:
    run = db.get(OpeningReconciliationRun, run_id)
    if run is None or run.company_id != company_id:
        raise OpeningReconError("reconciliation run not found")
    return run


def list_runs(db, company_id, *, recon_type=None, limit=50, offset=0):
    stmt = select(OpeningReconciliationRun).where(OpeningReconciliationRun.company_id == company_id)
    if recon_type:
        stmt = stmt.where(OpeningReconciliationRun.recon_type == recon_type)
    return db.execute(stmt.order_by(OpeningReconciliationRun.created_at.desc())
                      .limit(max(1, min(limit, 500))).offset(max(0, offset))).scalars().all()


def list_discrepancies(db, company_id, *, run_id=None, resolution_status=None, category=None,
                       limit=500, offset=0):
    stmt = select(OpeningDiscrepancy).where(OpeningDiscrepancy.company_id == company_id)
    if run_id:
        stmt = stmt.where(OpeningDiscrepancy.run_id == run_id)
    if resolution_status:
        stmt = stmt.where(OpeningDiscrepancy.resolution_status == resolution_status)
    if category:
        stmt = stmt.where(OpeningDiscrepancy.recon_category == category)
    return db.execute(stmt.order_by(OpeningDiscrepancy.created_at.desc())
                      .limit(max(1, min(limit, 2000))).offset(max(0, offset))).scalars().all()


def discrepancy_history(db, company_id, discrepancy_key):
    """All occurrences of a discrepancy across runs (append-only history)."""
    return db.execute(select(OpeningDiscrepancy).where(
        OpeningDiscrepancy.company_id == company_id,
        OpeningDiscrepancy.discrepancy_key == discrepancy_key)
        .order_by(OpeningDiscrepancy.created_at)).scalars().all()


def resolve_discrepancy(db, company_id, discrepancy_id, *, resolution_notes, actor=None,
                        supporting_reference=None, responsible_user=None):
    d = db.get(OpeningDiscrepancy, discrepancy_id)
    if d is None or d.company_id != company_id:
        raise OpeningReconError("discrepancy not found")
    if not (resolution_notes or "").strip():
        raise OpeningReconError("a resolution note is required to resolve a discrepancy")
    d.resolution_status = DiscrepancyStatus.RESOLVED.value
    d.resolution_notes = resolution_notes.strip()
    d.supporting_reference = supporting_reference
    d.responsible_user = responsible_user or actor
    d.resolved_by = actor
    d.resolved_at = _now()
    db.flush()
    record_audit(db, action="OPENING_DISCREPANCY_RESOLVED", entity="opening_discrepancy",
                 entity_id=str(d.id), actor_id=actor,
                 detail={"company_id": str(company_id), "key": d.discrepancy_key})
    return d


def reopen_discrepancy(db, company_id, discrepancy_id, *, reason, actor=None):
    d = db.get(OpeningDiscrepancy, discrepancy_id)
    if d is None or d.company_id != company_id:
        raise OpeningReconError("discrepancy not found")
    if not (reason or "").strip():
        raise OpeningReconError("a reason is required to reopen a discrepancy")
    d.resolution_status = DiscrepancyStatus.REOPENED.value
    d.resolution_notes = (d.resolution_notes or "") + f"\nREOPENED: {reason.strip()}"
    d.resolved_by = None
    d.resolved_at = None
    db.flush()
    record_audit(db, action="OPENING_DISCREPANCY_REOPENED", entity="opening_discrepancy",
                 entity_id=str(d.id), actor_id=actor,
                 detail={"company_id": str(company_id), "key": d.discrepancy_key, "reason": reason})
    return d


def set_run_status(db, company_id, run_id, *, to_status, actor=None):
    run = get_run(db, company_id, run_id)
    if to_status not in {s.value for s in ReconStatus}:
        raise OpeningReconError("invalid status")
    if to_status not in ALLOWED_TRANSITIONS.get(run.status, set()):
        raise OpeningReconStateError(f"transition {run.status} -> {to_status} is not permitted")
    # READY_FOR_CUTOVER requires no unresolved discrepancies and no pending CA decisions.
    if to_status == ReconStatus.READY_FOR_CUTOVER.value:
        if _unresolved_count(db, company_id) > 0:
            raise OpeningReconStateError("cannot mark READY_FOR_CUTOVER with unresolved discrepancies")
        if _pending_ca(db, company_id) > 0:
            raise OpeningReconStateError("cannot mark READY_FOR_CUTOVER with pending CA decisions")
        if run.incomplete:
            raise OpeningReconStateError("cannot mark READY_FOR_CUTOVER on an incomplete reconciliation")
    run.status = to_status
    db.flush()
    record_audit(db, action="OPENING_RECON_STATUS", entity="opening_reconciliation_run",
                 entity_id=str(run.id), actor_id=actor,
                 detail={"company_id": str(company_id), "status": to_status})
    return run


# ------------------------- P6E dashboard / readiness ------------------------

def _unresolved_count(db, company_id):
    return db.execute(select(func.count()).select_from(OpeningDiscrepancy).where(
        OpeningDiscrepancy.company_id == company_id,
        OpeningDiscrepancy.resolution_status.in_(
            (DiscrepancyStatus.UNRESOLVED.value, DiscrepancyStatus.UNDER_REVIEW.value,
             DiscrepancyStatus.REOPENED.value)))).scalar() or 0


def _pending_ca(db, company_id):
    return db.execute(select(func.count()).select_from(OpeningCaDecision).where(
        OpeningCaDecision.company_id == company_id, OpeningCaDecision.is_required.is_(True),
        OpeningCaDecision.status != CaDecisionStatus.APPROVED.value)).scalar() or 0


def dashboard(db: Session, company_id: uuid.UUID, *, effective_date: date | None = None,
              factory_id: uuid.UUID | None = None) -> dict:
    """Live, read-only consolidated opening reconciliation snapshot (computes nothing
    persistent). Cutover readiness is gated: NOT ready while any reconciliation is
    incomplete, a mapping is missing, a discrepancy is unresolved or a CA decision is
    pending. Readiness is NOT authority to post."""
    as_of = effective_date
    sections = {}
    any_incomplete = False
    live_disc = 0
    for t, fn in _COMPUTE.items():
        summary, discs = fn(db, company_id, as_of=as_of, factory_id=factory_id)
        sections[t] = summary
        any_incomplete = any_incomplete or bool(summary.get("incomplete"))
        live_disc += len(discs)

    batches = _opening_batches(db, company_id, factory_id)
    opening_value = quantize(sum(
        (_m(l.debit) for l in db.execute(select(OpeningGLLine).where(
            OpeningGLLine.company_id == company_id,
            OpeningGLLine.validation_status.in_(_V_OK))).scalars().all())), MONEY_SCALE)

    unresolved = _unresolved_count(db, company_id)
    resolved = db.execute(select(func.count()).select_from(OpeningDiscrepancy).where(
        OpeningDiscrepancy.company_id == company_id,
        OpeningDiscrepancy.resolution_status == DiscrepancyStatus.RESOLVED.value)).scalar() or 0
    pending_ca = _pending_ca(db, company_id)

    if any_incomplete:
        readiness = "DISCREPANCIES_PRESENT" if live_disc else "RECONCILIATION_IN_PROGRESS"
    elif live_disc > 0 or unresolved > 0:
        readiness = "DISCREPANCIES_PRESENT"
    elif pending_ca > 0:
        readiness = "CA_APPROVAL_PENDING"
    elif not batches:
        readiness = "RECONCILIATION_NOT_STARTED"
    else:
        readiness = "READY_FOR_CUTOVER"

    return {
        "company_id": str(company_id), "factory_id": (str(factory_id) if factory_id else None),
        "effective_date": as_of.isoformat() if as_of else None,
        "opening_batch_count": len(batches),
        "opening_gl_debit_total": str(opening_value),
        "inventory": sections[ReconType.INVENTORY.value],
        "customer_ar": sections[ReconType.CUSTOMER_AR.value],
        "supplier_ap": sections[ReconType.SUPPLIER_AP.value],
        "bank_cash": sections[ReconType.BANK_CASH.value],
        "fixed_asset": sections[ReconType.FIXED_ASSET.value],
        "gst_statutory": sections[ReconType.GST_STATUTORY.value],
        "trial_balance": sections[ReconType.TRIAL_BALANCE.value],
        "controls": {
            "live_discrepancies": live_disc,
            "persisted_unresolved_discrepancies": unresolved,
            "persisted_resolved_discrepancies": resolved,
            "pending_ca_decisions": pending_ca,
            "any_incomplete": any_incomplete,
        },
        "cutover_readiness": readiness,
        "cutover_note": "Reconciliation readiness is NOT authority to post opening vouchers.",
    }


def cutover_readiness(db, company_id, *, effective_date=None, factory_id=None) -> dict:
    d = dashboard(db, company_id, effective_date=effective_date, factory_id=factory_id)
    return {"company_id": str(company_id), "cutover_readiness": d["cutover_readiness"],
            "controls": d["controls"], "cutover_note": d["cutover_note"]}
