"""Stage 6F-P5 — financial opening balances (service-level + API, SQLite).

Covers the complete financial opening cutover built on the P1 batch lifecycle and
the EXISTING double-entry posting engine:

* per-type import/validation for all six line kinds (GL / AR / AP / bank /
  fixed-asset / statutory): required fields, unknown / group / inactive accounts,
  unknown customer/supplier/bank, debit-xor-credit, non-negative amounts, NBV
  arithmetic, disputed-line quarantine, within-batch dedup;
* reconciliation: trial balance balances; AR/AP sub-ledger detail ties to the
  1040 / 2010 control accounts; inventory control GL ties to the P1-P4 StockLayer
  sub-ledger total (never re-created here);
* the CA decision register and the HARD posting gate — posting is blocked without
  an APPROVED batch, every required CA decision APPROVED, every reconciliation
  passing AND an explicit ``authorize=True``;
* an authorised post emits exactly ONE balanced OB voucher with AR/AP control
  lines split into party-tagged journal lines, marks the lines COMMITTED, closes
  the batch (cutover lock), creates NO stock, and is idempotent; reversal works;
* company isolation.

No real company opening balances are posted: every posting here uses disposable
test data inside an isolated in-memory database.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.accounting import JournalLine, Voucher, VoucherStatus
from app.models.core import Company
from app.models.inventory import StockLayer
from app.models.masters import BankAccount, Customer, Supplier
from app.models.opening import OpeningBatchStatus
from app.models.opening_financial import (
    CaDecisionStatus,
    FinancialLineType,
    FinancialPostingState,
    OpeningApLine,
    OpeningArLine,
    OpeningBankLine,
    OpeningFixedAssetLine,
    OpeningGLLine,
    OpeningStatutoryLine,
    REQUIRED_CA_DECISIONS,
)
from app.models.opening_stock import OpeningImportFile, OpeningLineValidation
from app.services import opening as batch_svc
from app.services import opening_financial as fin

ED = date(2026, 11, 15)
V = OpeningLineValidation
GL = FinancialLineType.GL.value
AR = FinancialLineType.AR.value
AP = FinancialLineType.AP.value
BANK = FinancialLineType.BANK.value
FA = FinancialLineType.FIXED_ASSET.value
STAT = FinancialLineType.STATUTORY.value


# ------------------------------- fixtures -----------------------------------

@pytest.fixture()
def masters(db, company):
    """Customers, suppliers and a bank account for the DEMO company (whose full
    chart of accounts is already seeded)."""
    cid = company.id
    c1 = Customer(company_id=cid, code="C1", name="Customer One")
    c2 = Customer(company_id=cid, code="C2", name="Customer Two")
    s1 = Supplier(company_id=cid, code="S1", name="Supplier One")
    s2 = Supplier(company_id=cid, code="S2", name="Supplier Two")
    db.add_all([c1, c2, s1, s2])
    db.flush()
    from app.models.accounting import ChartOfAccount
    bank_acct = db.execute(select(ChartOfAccount).where(
        ChartOfAccount.company_id == cid, ChartOfAccount.code == "1020")).scalars().first()
    ba = BankAccount(company_id=cid, name="HDFC Current", bank_name="HDFC",
                     account_number="123456", coa_account_id=bank_acct.id)
    db.add(ba)
    db.flush()
    return {"cid": cid, "c1": c1, "c2": c2, "s1": s1, "s2": s2, "ba": ba}


def _mk_batch(db, cid, creator=None):
    return batch_svc.create_batch(db, cid, batch_type="GL_BALANCES", effective_date=ED,
                                  created_by=creator or uuid.uuid4())


def _import(db, batch, line_type, rows, actor=None):
    actor = actor or uuid.uuid4()
    f = OpeningImportFile(company_id=batch.company_id, batch_id=batch.id, line_type=line_type,
                          file_name="f.xlsx",
                          file_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                          content=b"x", uploaded_by=actor)
    db.add(f)
    db.flush()
    return fin.import_rows(db, batch, line_type=line_type, rows=rows, import_file=f, actor=actor)


def _lines(db, model, batch):
    return db.execute(select(model).where(model.batch_id == batch.id)
                      .order_by(model.row_number)).scalars().all()


def _glrow(code, debit=None, credit=None, row=3, **over):
    r = {"account_code": code, "debit": debit, "credit": credit, "narration": "opening",
         "source_document": "TB", "source_reference": None, "_row": row}
    r.update(over)
    return r


def _arrow(cust, amt, inv="INV-1", row=3, **over):
    r = {"ar_kind": "INVOICE", "customer_code": cust, "invoice_no": inv,
         "invoice_date": "2026-09-01", "due_date": "2026-10-01",
         "original_amount": amt, "received_amount": "0", "outstanding_amount": amt,
         "tax_reference": None, "currency": "INR", "disputed_unknown": None,
         "source_document": "AR", "source_reference": None, "notes": None, "_row": row}
    r.update(over)
    return r


def _aprow(sup, amt, inv="BILL-1", row=3, **over):
    r = {"ap_kind": "INVOICE", "supplier_code": sup, "invoice_no": inv,
         "invoice_date": "2026-09-01", "due_date": "2026-10-01",
         "original_amount": amt, "paid_amount": "0", "outstanding_amount": amt,
         "tds_amount": "0", "tax_reference": None, "disputed_unknown": None,
         "source_document": "AP", "source_reference": None, "notes": None, "_row": row}
    r.update(over)
    return r


# ------------------------------ GL validation -------------------------------

def test_gl_valid_and_balanced(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, GL, [_glrow("1020", debit="1000", row=3),
                        _glrow("3010", credit="1000", row=4)])
    lines = _lines(db, OpeningGLLine, b)
    assert [l.validation_status for l in lines] == [V.VALID.value, V.VALID.value]
    rec = fin.reconcile(db, company.id, b.id)
    assert rec["trial_balance"]["balanced"] is True
    assert rec["trial_balance"]["difference"] == "0.00"
    assert rec["reconciled"] is True


def test_gl_unbalanced_blocks(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, GL, [_glrow("1020", debit="1000", row=3),
                        _glrow("3010", credit="600", row=4)])
    rec = fin.reconcile(db, company.id, b.id)
    assert rec["trial_balance"]["balanced"] is False
    assert rec["trial_balance"]["difference"] == "400.00"
    assert rec["reconciled"] is False


def test_gl_unknown_account_rejected(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, GL, [_glrow("9999", debit="500", row=3)])
    (l,) = _lines(db, OpeningGLLine, b)
    assert l.validation_status == V.INVALID.value
    assert any(e["field"] == "account_code" for e in __import_errors(l))


def test_gl_group_account_rejected(db, company, masters):
    # 1000 "Current Assets" is a group heading (is_group=True, not postable).
    b = _mk_batch(db, company.id)
    _import(db, b, GL, [_glrow("1000", debit="500", row=3)])
    (l,) = _lines(db, OpeningGLLine, b)
    assert l.validation_status == V.INVALID.value


def test_gl_debit_xor_credit(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, GL, [_glrow("1020", debit="500", credit="500", row=3),
                        _glrow("1020", debit=None, credit=None, row=4)])
    lines = _lines(db, OpeningGLLine, b)
    assert all(l.validation_status == V.INVALID.value for l in lines)


def test_gl_within_batch_dedup(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, GL, [_glrow("1020", debit="500", row=3),
                        _glrow("1020", debit="300", row=4)])
    lines = _lines(db, OpeningGLLine, b)
    # same account_code -> same dedup key; second is quarantined as duplicate.
    statuses = sorted(l.validation_status for l in lines)
    assert statuses == [V.INVALID.value, V.VALID.value]
    dup = [l for l in lines if l.validation_status == V.INVALID.value][0]
    assert any(e["field"] == "dedup" for e in __import_errors(dup))


# --------------------------- AR / AP validation -----------------------------

def test_ar_valid_and_control_reconciles(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, GL, [_glrow("1040", debit="5000", row=3),   # AR control
                        _glrow("3010", credit="5000", row=4)])
    _import(db, b, AR, [_arrow("C1", "3000", inv="A1", row=3),
                        _arrow("C2", "2000", inv="A2", row=4)])
    rec = fin.reconcile(db, company.id, b.id)
    assert rec["ar"]["subledger_outstanding"] == "5000.00"
    assert rec["ar"]["gl_control_1040"] == "5000.00"
    assert rec["ar"]["reconciled"] is True
    assert rec["reconciled"] is True


def test_ar_control_mismatch_detected(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, GL, [_glrow("1040", debit="5000", row=3),
                        _glrow("3010", credit="5000", row=4)])
    _import(db, b, AR, [_arrow("C1", "3000", inv="A1", row=3)])   # only 3000 vs 5000
    rec = fin.reconcile(db, company.id, b.id)
    assert rec["ar"]["reconciled"] is False
    assert rec["reconciled"] is False


def test_ar_unknown_customer_rejected(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, AR, [_arrow("NOPE", "1000", row=3)])
    (l,) = _lines(db, OpeningArLine, b)
    assert l.validation_status == V.INVALID.value


def test_ar_disputed_line_quarantined(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, AR, [_arrow("C1", "1000", disputed_unknown="true", row=3)])
    (l,) = _lines(db, OpeningArLine, b)
    assert l.validation_status == V.INVALID.value
    assert any(e["field"] == "disputed_unknown" for e in __import_errors(l))


def test_ar_invoice_no_required(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, AR, [_arrow("C1", "1000", inv=None, row=3)])
    (l,) = _lines(db, OpeningArLine, b)
    assert l.validation_status == V.INVALID.value


def test_ap_valid_and_control_reconciles(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, GL, [_glrow("1020", debit="4000", row=3),
                        _glrow("2010", credit="4000", row=4)])   # AP control
    _import(db, b, AP, [_aprow("S1", "2500", inv="B1", row=3),
                        _aprow("S2", "1500", inv="B2", row=4)])
    rec = fin.reconcile(db, company.id, b.id)
    assert rec["ap"]["subledger_outstanding"] == "4000.00"
    assert rec["ap"]["gl_control_2010"] == "4000.00"
    assert rec["ap"]["reconciled"] is True
    assert rec["reconciled"] is True


# ------------------------------ bank validation -----------------------------

def test_bank_valid(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, BANK, [{"bank_account_name": "HDFC Current", "account_code": None,
                           "is_cash": "false", "book_balance": "1000",
                           "statement_balance": "1200", "outstanding_cheques": "200",
                           "deposits_in_transit": "0", "effective_date": None,
                           "source_document": "BNK", "source_reference": None,
                           "notes": None, "_row": 3}])
    (l,) = _lines(db, OpeningBankLine, b)
    assert l.validation_status == V.VALID.value
    assert l.bank_account_id == masters["ba"].id
    assert l.account_code == "1020"   # resolved from the bank account's GL link


def test_bank_unknown_account_rejected(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, BANK, [{"bank_account_name": "Ghost Bank", "account_code": None,
                           "book_balance": "1000", "_row": 3}])
    (l,) = _lines(db, OpeningBankLine, b)
    assert l.validation_status == V.INVALID.value


# --------------------------- fixed-asset validation -------------------------

def test_fixed_asset_valid_nbv(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, FA, [{"asset_code": "FA-1", "name": "CNC Machine", "category": "Plant",
                         "factory_code": None, "asset_account_code": "1530",
                         "accum_dep_account_code": "1590", "acquisition_date": "2020-04-01",
                         "original_cost": "100000", "accumulated_depreciation": "40000",
                         "salvage_value": "5000", "method": "SLM", "useful_life_months": "120",
                         "verification_status": "VERIFIED", "source_document": "FA",
                         "source_reference": None, "notes": None, "_row": 3}])
    (l,) = _lines(db, OpeningFixedAssetLine, b)
    assert l.validation_status == V.VALID.value
    assert l.net_book_value == Decimal("60000.00")


def test_fixed_asset_accum_exceeds_cost_rejected(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, FA, [{"asset_code": "FA-2", "asset_account_code": "1530",
                         "accum_dep_account_code": "1590", "original_cost": "100",
                         "accumulated_depreciation": "200", "_row": 3}])
    (l,) = _lines(db, OpeningFixedAssetLine, b)
    assert l.validation_status == V.INVALID.value


# ---------------------------- statutory validation --------------------------

def test_statutory_valid(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, STAT, [{"statutory_type": "TDS", "account_code": "2050",
                           "debit": None, "credit": "5000", "period_reference": "Q2-FY27",
                           "challan_reference": None, "source_document": "STAT",
                           "source_reference": None, "notes": None, "_row": 3}])
    (l,) = _lines(db, OpeningStatutoryLine, b)
    assert l.validation_status == V.VALID.value


# --------------------- inventory reconciliation vs P1-P4 --------------------

def _stock_layer(db, cid, value):
    db.add(StockLayer(company_id=cid, item_type="YARN", item_id=uuid.uuid4(),
                      qty_in=Decimal("1"), qty_remaining=Decimal("1"),
                      unit_cost=Decimal(str(value)), value_remaining=Decimal(str(value)),
                      received_at=datetime.now(timezone.utc)))


def test_inventory_control_matches_subledger(db, company, masters):
    # P1-P4 sub-ledger (StockLayer) totals 10000 with NO GL posted (opening stock is
    # GL-neutral). The financial GL inventory control must tie to it exactly.
    _stock_layer(db, company.id, "6000")
    _stock_layer(db, company.id, "4000")
    db.flush()
    b = _mk_batch(db, company.id)
    _import(db, b, GL, [_glrow("1060", debit="10000", row=3),   # Yarn Inventory control
                        _glrow("3010", credit="10000", row=4)])
    rec = fin.reconcile(db, company.id, b.id)
    assert rec["inventory"]["p1_p4_subledger_total"] == "10000.00"
    assert rec["inventory"]["gl_control_total"] == "10000.00"
    assert rec["inventory"]["reconciled"] is True
    assert rec["reconciled"] is True


def test_inventory_control_mismatch_detected(db, company, masters):
    _stock_layer(db, company.id, "10000")
    db.flush()
    b = _mk_batch(db, company.id)
    _import(db, b, GL, [_glrow("1060", debit="8000", row=3),   # 8000 vs 10000 sub-ledger
                        _glrow("3010", credit="8000", row=4)])
    rec = fin.reconcile(db, company.id, b.id)
    assert rec["inventory"]["reconciled"] is False
    assert rec["reconciled"] is False


# --------------------------- posting hard gate ------------------------------

def _balanced_ar_ap_batch(db, cid, maker, checker):
    """APPROVED, reconciled, balanced financial batch (AR 50k, AP 40k, bank 30k,
    equity 40k). No inventory lines, so inventory ties to the empty sub-ledger."""
    b = _mk_batch(db, cid, creator=maker)
    _import(db, b, GL, [_glrow("1040", debit="50000", row=3),
                        _glrow("1020", debit="30000", row=4),
                        _glrow("2010", credit="40000", row=5),
                        _glrow("3010", credit="40000", row=6)], actor=maker)
    _import(db, b, AR, [_arrow("C1", "30000", inv="A1", row=3),
                        _arrow("C2", "20000", inv="A2", row=4)], actor=maker)
    _import(db, b, AP, [_aprow("S1", "25000", inv="B1", row=3),
                        _aprow("S2", "15000", inv="B2", row=4)], actor=maker)
    fin.submit_batch(db, cid, b.id, submitted_by=maker)
    fin.approve_batch(db, cid, b.id, approved_by=checker)
    return b


def _approve_all_ca(db, cid, bid, reviewer=None):
    reviewer = reviewer or uuid.uuid4()
    for key, _title in REQUIRED_CA_DECISIONS:
        fin.set_ca_decision(db, cid, bid, key, status="APPROVED",
                            reviewer_name="CA Firm", reviewer_id=reviewer,
                            approved_treatment="per CA")


def test_posting_blocked_without_authorize(db, company, masters):
    maker, checker = uuid.uuid4(), uuid.uuid4()
    b = _balanced_ar_ap_batch(db, company.id, maker, checker)
    _approve_all_ca(db, company.id, b.id)
    # Everything satisfied EXCEPT the explicit authorisation flag.
    with pytest.raises(fin.PostingBlockedError):
        fin.post_financial(db, company.id, b.id, authorize=False, posted_by=checker)
    assert db.execute(select(func.count()).select_from(Voucher).where(
        Voucher.company_id == company.id)).scalar() == 0


def test_posting_blocked_until_ca_approved(db, company, masters):
    maker, checker = uuid.uuid4(), uuid.uuid4()
    b = _balanced_ar_ap_batch(db, company.id, maker, checker)
    # CA decisions still PENDING -> posting blocked even with authorize=True.
    st = fin.posting_state(db, company.id, b.id)
    assert st["state"] == FinancialPostingState.CA_APPROVAL_PENDING.value
    with pytest.raises(fin.PostingBlockedError):
        fin.post_financial(db, company.id, b.id, authorize=True, posted_by=checker)
    assert db.execute(select(func.count()).select_from(Voucher).where(
        Voucher.company_id == company.id)).scalar() == 0


def test_posting_blocked_when_unbalanced(db, company, masters):
    maker, checker = uuid.uuid4(), uuid.uuid4()
    b = _mk_batch(db, company.id, creator=maker)
    _import(db, b, GL, [_glrow("1020", debit="1000", row=3),
                        _glrow("3010", credit="999", row=4)], actor=maker)
    fin.submit_batch(db, company.id, b.id, submitted_by=maker)
    # submit requires all lines valid (they are) but TB is unbalanced; approve then
    # posting must be refused on reconciliation.
    fin.approve_batch(db, company.id, b.id, approved_by=checker)
    _approve_all_ca(db, company.id, b.id)
    with pytest.raises(fin.PostingBlockedError):
        fin.post_financial(db, company.id, b.id, authorize=True, posted_by=checker)


def test_authorised_post_emits_balanced_voucher_and_locks(db, company, masters):
    maker, checker = uuid.uuid4(), uuid.uuid4()
    b = _balanced_ar_ap_batch(db, company.id, maker, checker)
    _approve_all_ca(db, company.id, b.id)
    st = fin.posting_state(db, company.id, b.id)
    assert st["state"] == FinancialPostingState.READY_FOR_AUTHORISED_POSTING.value

    voucher, n = fin.post_financial(db, company.id, b.id, authorize=True, posted_by=checker)
    assert VoucherStatus(voucher.status) == VoucherStatus.POSTED
    assert voucher.posting_reference == f"FINOPEN:{b.id}"
    assert voucher.total_debit == voucher.total_credit == Decimal("80000.00")

    # AR/AP control lines are split into party-tagged journal lines.
    jlines = db.execute(select(JournalLine).where(JournalLine.voucher_id == voucher.id)).scalars().all()
    cust_lines = [j for j in jlines if j.party_type == "customer"]
    sup_lines = [j for j in jlines if j.party_type == "supplier"]
    assert len(cust_lines) == 2 and len(sup_lines) == 2
    assert sum((j.debit for j in cust_lines), Decimal("0")) == Decimal("50000.00")
    assert sum((j.credit for j in sup_lines), Decimal("0")) == Decimal("40000.00")

    # Lines committed, batch closed (cutover lock), and NO stock was created.
    for model in (OpeningGLLine, OpeningArLine, OpeningApLine):
        for l in _lines(db, model, b):
            assert l.validation_status == V.COMMITTED.value
            assert l.committed_at is not None
    batch = batch_svc.get_batch(db, company.id, b.id)
    assert batch.status == OpeningBatchStatus.CLOSED.value
    assert db.execute(select(func.count()).select_from(StockLayer).where(
        StockLayer.company_id == company.id)).scalar() == 0


def test_post_is_idempotent(db, company, masters):
    maker, checker = uuid.uuid4(), uuid.uuid4()
    b = _balanced_ar_ap_batch(db, company.id, maker, checker)
    _approve_all_ca(db, company.id, b.id)
    v1, n1 = fin.post_financial(db, company.id, b.id, authorize=True, posted_by=checker)
    # A second call (even without authorize) returns the same voucher, posts nothing.
    v2, n2 = fin.post_financial(db, company.id, b.id, authorize=False, posted_by=checker)
    assert v1.id == v2.id and n2 == 0
    assert db.execute(select(func.count()).select_from(Voucher).where(
        Voucher.company_id == company.id,
        Voucher.posting_reference == f"FINOPEN:{b.id}")).scalar() == 1


def test_reverse_posted_opening(db, company, masters):
    maker, checker = uuid.uuid4(), uuid.uuid4()
    b = _balanced_ar_ap_batch(db, company.id, maker, checker)
    _approve_all_ca(db, company.id, b.id)
    v, _ = fin.post_financial(db, company.id, b.id, authorize=True, posted_by=checker)
    rev = fin.reverse_financial(db, company.id, b.id, reason="correcting an opening error",
                                reversed_by=checker)
    assert rev.id != v.id
    db.refresh(v)
    assert VoucherStatus(v.status) == VoucherStatus.REVERSED


def test_reverse_without_posting_errors(db, company, masters):
    maker, checker = uuid.uuid4(), uuid.uuid4()
    b = _balanced_ar_ap_batch(db, company.id, maker, checker)
    with pytest.raises(fin.OpeningFinancialStateError):
        fin.reverse_financial(db, company.id, b.id, reason="nothing posted")


# ------------------------------ company isolation ---------------------------

def test_company_isolation(db, company, masters):
    other = Company(code="OTHR", name="Other Co")
    db.add(other)
    db.flush()
    b = _mk_batch(db, company.id)
    _import(db, b, GL, [_glrow("1020", debit="100", row=3), _glrow("3010", credit="100", row=4)])
    # The batch is invisible under a different company scope.
    with pytest.raises(batch_svc.OpeningBatchNotFound):
        fin.list_lines(db, other.id, b.id, GL)
    with pytest.raises(batch_svc.OpeningBatchNotFound):
        fin.reconcile(db, other.id, b.id)


# ------------------------------- API smoke ----------------------------------

def _login_perm(client, db, company):
    """Return auth headers for the seeded admin (holds every permission)."""
    resp = client.post("/api/auth/login", data={"username": "admin", "password": "admin123"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_api_templates_and_import_and_gate(client, db, company, masters):
    h = _login_perm(client, db, company)
    # Every financial template downloads.
    for seg in ("gl", "ar", "ap", "bank", "fixed-asset", "statutory"):
        r = client.get(f"/api/opening/templates/financial/{seg}", headers=h)
        assert r.status_code == 200, (seg, r.text)
        assert r.content[:2] == b"PK"   # xlsx zip magic

    # Create a GL_BALANCES batch.
    r = client.post("/api/opening/batches", headers=h,
                    json={"batch_type": "GL_BALANCES", "effective_date": ED.isoformat()})
    assert r.status_code == 201, r.text
    bid = r.json()["id"]

    # An unknown template segment is a 404.
    assert client.get("/api/opening/templates/financial/nope", headers=h).status_code == 404

    # Posting on an empty/undersupplied batch is hard-gated (409), never a silent post.
    r = client.post(f"/api/opening/batches/{bid}/financial/post", headers=h,
                    json={"authorize": True})
    assert r.status_code == 409, r.text
    # Reconcile + CA register endpoints answer.
    assert client.get(f"/api/opening/batches/{bid}/financial-reconcile", headers=h).status_code == 200
    r = client.get(f"/api/opening/batches/{bid}/ca-decisions", headers=h)
    assert r.status_code == 200
    assert len(r.json()["decisions"]) == len(REQUIRED_CA_DECISIONS)


def __import_errors(line):
    import json as _json
    return _json.loads(line.validation_errors) if line.validation_errors else []
