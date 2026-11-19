"""Stage 6F-P5 GST — portal import, ITC reconciliation, lifecycle, utilisation (SQLite).

Acceptance coverage: GSTR-2A/2B JSON import, Excel import, duplicate-import protection,
same invoice across multiple periods, an invoice missing from 2B that appears the next
month (carry-forward), tax-amount mismatch, supplier-GSTIN mismatch, credit/debit notes,
partial ITC reversal + partial reclaim, duplicate-reclaim prevention, correct cross-
utilisation + prohibited cross-utilisation rejection, electronic-ledger reconciliation,
reports (incl. Excel/PDF), company isolation, audit trail, and the portal-unavailable /
manual-import fallback.

All posting uses disposable test data; statutory posting stays disabled unless an
explicit authorise flag is supplied.
"""
from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.accounting import Voucher, VoucherStatus
from app.models.audit import AuditLog
from app.models.core import Company
from app.models.gst_itc import (
    GstImportSource,
    GstReturnType,
    ItcStatus,
    MatchType,
)
from app.services.gst_portal import books as books_svc
from app.services.gst_portal import connector as conn
from app.services.gst_portal import importer as imp
from app.services.gst_portal import itc as itc_svc
from app.services.gst_portal import ledger_recon as ledger_svc
from app.services.gst_portal import reconcile as reco_svc
from app.services.gst_portal import reports as reports_svc
from app.services.gst_portal import utilisation as util_svc

GSTIN = "27AAAPL1234C1ZV"
SUP_A = "29AABCS1681Q1Z9"
SUP_B = "24AAACB2902M1ZA"
FY = "2026-27"


@pytest.fixture()
def gstco(db, company):
    company.gstin = GSTIN
    company.state_code = company.state_code or "27"
    db.flush()
    return company


# ------------------------------- JSON builders ------------------------------

def _inv(ctin, inum, taxable, cgst=0, sgst=0, igst=0, idt="05-10-2026", itcavl="Y", rt=18):
    val = taxable + cgst + sgst + igst
    return {"ctin": ctin, "trdnm": "Supplier", "inv": [{
        "inum": inum, "idt": idt, "val": val, "pos": "27", "rev": "N", "itcavl": itcavl,
        "items": [{"num": 1, "rt": rt, "txval": taxable, "igst": igst, "cgst": cgst,
                   "sgst": sgst, "cess": 0}]}]}


def _note(ctin, ntnum, typ, taxable, cgst=0, sgst=0, igst=0, ntdt="20-10-2026"):
    return {"ctin": ctin, "trdnm": "Supplier", "nt": [{
        "ntnum": ntnum, "typ": typ, "ntdt": ntdt, "val": taxable + cgst + sgst + igst,
        "items": [{"rt": 18, "txval": taxable, "igst": igst, "cgst": cgst, "sgst": sgst,
                   "cess": 0}]}]}


def _2b(period, b2b=None, cdnr=None, gen="14-11-2026"):
    docdata = {}
    if b2b:
        docdata["b2b"] = b2b
    if cdnr:
        docdata["cdnr"] = cdnr
    return json.dumps({"data": {"gstin": GSTIN, "rtnprd": period, "gendt": gen,
                                "docdata": docdata}}).encode()


def _2a(period, b2b):
    # GSTR-2A top-level, itm_det item shape.
    groups = []
    for g in b2b:
        gg = {"ctin": g["ctin"], "inv": []}
        for i in g["inv"]:
            it = i["items"][0]
            gg["inv"].append({"inum": i["inum"], "idt": i["idt"], "val": i["val"], "pos": "27",
                              "rev": "N", "itms": [{"num": 1, "itm_det": {
                                  "rt": it["rt"], "txval": it["txval"], "iamt": it["igst"],
                                  "camt": it["cgst"], "samt": it["sgst"], "csamt": 0}}]})
        groups.append(gg)
    return json.dumps({"gstin": GSTIN, "fp": period, "b2b": groups}).encode()


def _imp(db, cid, period, content, rtype="GSTR2B", source="MANUAL_JSON", actor=None):
    return imp.import_return(db, cid, gstin=GSTIN, financial_year=FY, return_period=period,
                             return_type=rtype, content=content, file_name=f"{rtype}_{period}.json",
                             source=source, actor=actor or uuid.uuid4())


def _cap(db, cid, sup, inum, taxable, cgst=0, sgst=0, igst=0, period="102026",
         idate=date(2026, 10, 5), doc_type="INV"):
    return books_svc.capture_purchase(
        db, cid, gstin=GSTIN, supplier_gstin=sup, supplier_invoice_no=inum, invoice_date=idate,
        booked_period=period, taxable_value=taxable, cgst=cgst, sgst=sgst, igst=igst,
        doc_type=doc_type)


# --------------------------------- imports ----------------------------------

def test_import_2b_json(db, gstco):
    r = _imp(db, gstco.id, "102026", _2b("102026", b2b=[_inv(SUP_A, "INV/1", 10000, 900, 900)]))
    assert r["return_type"] == "GSTR2B" and r["doc_count"] == 1
    from app.models.gst_itc import GstPortalDocument
    (doc,) = db.execute(select(GstPortalDocument).where(
        GstPortalDocument.company_id == gstco.id)).scalars().all()
    assert doc.supplier_gstin == SUP_A and doc.cgst == Decimal("900.00") and doc.itc_available is True


def test_import_2a_json(db, gstco):
    r = _imp(db, gstco.id, "102026", _2a("102026", [_inv(SUP_A, "INV/1", 10000, 900, 900)]),
             rtype="GSTR2A")
    assert r["doc_count"] == 1


def test_import_excel(db, gstco):
    # Build the canonical template, fill one row via openpyxl, import it.
    import io
    from openpyxl import load_workbook
    tpl = imp.build_gst_doc_template("GSTR2B")
    wb = load_workbook(io.BytesIO(tpl))
    ws = wb.active
    cols = imp.GST_DOC_COLUMNS
    row = {"category": "B2B", "doc_type": "INV", "supplier_gstin": SUP_A, "supplier_name": "S",
           "doc_number": "INV/1", "doc_date": "2026-10-05", "taxable_value": 10000, "igst": 0,
           "cgst": 900, "sgst": 900, "cess": 0, "total_value": 11800, "rate": 18,
           "place_of_supply": "27", "reverse_charge": "false", "itc_available": "true",
           "is_amendment": "false", "original_doc_number": "", "original_doc_date": ""}
    ws.append([row.get(c) for c in cols])
    buf = io.BytesIO()
    wb.save(buf)
    r = _imp(db, gstco.id, "102026", buf.getvalue(), source=GstImportSource.MANUAL_EXCEL.value)
    assert r["doc_count"] == 1


def test_duplicate_import_rejected(db, gstco):
    content = _2b("102026", b2b=[_inv(SUP_A, "INV/1", 10000, 900, 900)])
    _imp(db, gstco.id, "102026", content)
    with pytest.raises(imp.DuplicateImport):
        _imp(db, gstco.id, "102026", content)


def test_wrong_period_file_rejected(db, gstco):
    # file says 112026 but we request 102026
    with pytest.raises(imp.GstPortalError):
        _imp(db, gstco.id, "102026", _2b("112026", b2b=[_inv(SUP_A, "INV/1", 10000, 900, 900)]))


def test_reimport_supersedes(db, gstco):
    from app.models.gst_itc import GstImportStatus, GstReturnImport
    _imp(db, gstco.id, "102026", _2b("102026", b2b=[_inv(SUP_A, "INV/1", 10000, 900, 900)]))
    _imp(db, gstco.id, "102026", _2b("102026", b2b=[_inv(SUP_A, "INV/1", 10000, 900, 900),
                                                    _inv(SUP_B, "B/2", 5000, 450, 450)]))
    rows = db.execute(select(GstReturnImport).where(
        GstReturnImport.company_id == gstco.id,
        GstReturnImport.return_type == "GSTR2B").order_by(
        GstReturnImport.imported_at)).scalars().all()
    assert rows[0].status == GstImportStatus.SUPERSEDED.value
    assert rows[1].status == GstImportStatus.IMPORTED.value


# ------------------------------ reconciliation ------------------------------

def test_exact_match_2b(db, gstco):
    _cap(db, gstco.id, SUP_A, "INV/1", 10000, cgst=900, sgst=900)
    _imp(db, gstco.id, "102026", _2b("102026", b2b=[_inv(SUP_A, "INV/1", 10000, 900, 900)]))
    res = reco_svc.reconcile_period(db, gstco.id, gstin=GSTIN, return_type="GSTR2B",
                                    return_period="102026")
    assert res["counts"].get(MatchType.EXACT.value) == 1
    rows, _ = itc_svc.register(db, gstco.id)
    assert rows[0].status == ItcStatus.IN_2B_MATCHED.value


def test_invoice_format_difference(db, gstco):
    _cap(db, gstco.id, SUP_A, "INV-0001", 10000, cgst=900, sgst=900)
    _imp(db, gstco.id, "102026", _2b("102026", b2b=[_inv(SUP_A, "INV/0001", 10000, 900, 900)]))
    res = reco_svc.reconcile_period(db, gstco.id, gstin=GSTIN, return_type="GSTR2B",
                                    return_period="102026")
    assert res["counts"].get(MatchType.INVOICE_FORMAT_DIFF.value) == 1


def test_tax_amount_mismatch(db, gstco):
    _cap(db, gstco.id, SUP_A, "INV/1", 10000, cgst=900, sgst=900)
    # portal shows a different (higher) tax
    _imp(db, gstco.id, "102026", _2b("102026", b2b=[_inv(SUP_A, "INV/1", 10000, 1200, 1200)]))
    res = reco_svc.reconcile_period(db, gstco.id, gstin=GSTIN, return_type="GSTR2B",
                                    return_period="102026")
    assert res["counts"].get(MatchType.HEAD_DIFF.value) == 1
    rows, _ = itc_svc.register(db, gstco.id)
    assert rows[0].status == ItcStatus.MISMATCH_UNDER_REVIEW.value


def test_supplier_gstin_mismatch_is_missing_both_sides(db, gstco):
    # Books says SUP_A; portal has the same invoice number under SUP_B.
    _cap(db, gstco.id, SUP_A, "INV/1", 10000, cgst=900, sgst=900)
    _imp(db, gstco.id, "102026", _2b("102026", b2b=[_inv(SUP_B, "INV/1", 10000, 900, 900)]))
    res = reco_svc.reconcile_period(db, gstco.id, gstin=GSTIN, return_type="GSTR2B",
                                    return_period="102026")
    assert res["counts"].get(MatchType.MISSING_IN_2B.value) == 1       # books row, wrong gstin
    assert res["counts"].get(MatchType.MISSING_IN_BOOKS.value) == 1    # portal row, wrong gstin


def test_missing_in_books(db, gstco):
    _imp(db, gstco.id, "102026", _2b("102026", b2b=[_inv(SUP_A, "INV/9", 2000, 180, 180)]))
    res = reco_svc.reconcile_period(db, gstco.id, gstin=GSTIN, return_type="GSTR2B",
                                    return_period="102026")
    assert res["counts"].get(MatchType.MISSING_IN_BOOKS.value) == 1


def test_duplicate_books_invoice(db, gstco):
    _cap(db, gstco.id, SUP_A, "INV/1", 10000, cgst=900, sgst=900)
    # a second books row with same supplier+number (captured under a different doc_type key
    # won't collide, so force same key by same doc_type via direct model add)
    from app.models.gst_itc import ItcInvoiceStatus
    from app.services.gst_portal.common import make_books_key
    dup = ItcInvoiceStatus(company_id=gstco.id, gstin=GSTIN,
                           books_key=make_books_key(SUP_A, "INV/1", "INV") + "#dup",
                           supplier_gstin=SUP_A, supplier_invoice_no="INV/1",
                           invoice_date=date(2026, 10, 5), doc_type="INV", booked_period="102026",
                           taxable_value=Decimal("10000"), cgst=Decimal("900"), sgst=Decimal("900"),
                           igst=Decimal("0"), cess=Decimal("0"),
                           status=ItcStatus.BOOKED_NOT_IN_2B.value,
                           reversed_total=Decimal("0"), reclaimed_total=Decimal("0"))
    db.add(dup)
    db.flush()
    _imp(db, gstco.id, "102026", _2b("102026", b2b=[_inv(SUP_A, "INV/1", 10000, 900, 900)]))
    res = reco_svc.reconcile_period(db, gstco.id, gstin=GSTIN, return_type="GSTR2B",
                                    return_period="102026")
    assert res["counts"].get(MatchType.DUPLICATE.value) == 1


def test_credit_note_classified(db, gstco):
    _cap(db, gstco.id, SUP_A, "CN/3", 1000, cgst=90, sgst=90, doc_type="CN")
    _imp(db, gstco.id, "102026", _2b("102026", cdnr=[_note(SUP_A, "CN/3", "C", 1000, 90, 90)]))
    res = reco_svc.reconcile_period(db, gstco.id, gstin=GSTIN, return_type="GSTR2B",
                                    return_period="102026")
    assert res["counts"].get(MatchType.CREDIT_NOTE.value) == 1


def test_amendment_flagged_for_review(db, gstco):
    # Books has INV/1; the portal carries it only as a b2ba (amendment) document.
    _cap(db, gstco.id, SUP_A, "INV/1", 10000, cgst=900, sgst=900)
    payload = {"data": {"gstin": GSTIN, "rtnprd": "102026", "gendt": "14-11-2026", "docdata": {
        "b2ba": [{"ctin": SUP_A, "trdnm": "S", "inv": [
            {"inum": "INV/1", "idt": "05-10-2026", "val": 11800, "oinum": "INV/0",
             "items": [{"rt": 18, "txval": 10000, "cgst": 900, "sgst": 900, "igst": 0,
                        "cess": 0}]}]}]}}}
    _imp(db, gstco.id, "102026", json.dumps(payload).encode())
    res = reco_svc.reconcile_period(db, gstco.id, gstin=GSTIN, return_type="GSTR2B",
                                    return_period="102026")
    assert res["counts"].get(MatchType.AMENDMENT.value) == 1
    rows, _ = itc_svc.register(db, gstco.id)
    assert rows[0].status == ItcStatus.MISMATCH_UNDER_REVIEW.value


# ------------------- multi-period / carry-forward workflow ------------------

def test_missing_in_2b_then_next_month_carryforward(db, gstco):
    # Booked in Oct; Oct 2B does NOT contain it; Nov 2B does -> carry-forward match.
    _cap(db, gstco.id, SUP_A, "INV/77", 10000, cgst=900, sgst=900, period="102026")
    _imp(db, gstco.id, "102026", _2b("102026", b2b=[_inv(SUP_B, "OTHER", 100, 9, 9)]))
    r1 = reco_svc.reconcile_period(db, gstco.id, gstin=GSTIN, return_type="GSTR2B",
                                   return_period="102026")
    assert r1["counts"].get(MatchType.MISSING_IN_2B.value) == 1
    rows, _ = itc_svc.register(db, gstco.id)
    assert rows[0].status == ItcStatus.BOOKED_NOT_IN_2B.value
    # Defer it explicitly (recording the reason), excluded from claimable.
    itc_svc.defer_itc(db, gstco.id, rows[0].id, reason="not reflected in Oct 2B", period="102026")
    avail_oct = itc_svc.claimable_summary(db, gstco.id, "102026")
    assert avail_oct["amounts"]["available"]["cgst"] == "0.00"

    # November 2B now carries the same invoice.
    _imp(db, gstco.id, "112026", _2b("112026", b2b=[_inv(SUP_A, "INV/77", 10000, 900, 900,
                                                         idt="05-10-2026")]))
    r2 = reco_svc.reconcile_period(db, gstco.id, gstin=GSTIN, return_type="GSTR2B",
                                   return_period="112026")
    assert r2["counts"].get(MatchType.EXACT.value) == 1
    inv = db.get(type(rows[0]), rows[0].id)
    assert inv.status == ItcStatus.ELIGIBLE_FOR_REVIEW.value
    assert inv.matched_period == "112026"
    # Full lifecycle history preserved (booked -> deferred -> eligible).
    _, hist = itc_svc.history(db, gstco.id, inv.id)
    seq = [h.to_status for h in hist]
    assert ItcStatus.ITC_DEFERRED.value in seq and ItcStatus.ELIGIBLE_FOR_REVIEW.value in seq


def test_same_invoice_multiple_periods_no_double(db, gstco):
    # Invoice matched in Oct 2B; re-running reconcile for Nov 2B (also present) must not
    # create a second ITC register row or lose history.
    inv = _cap(db, gstco.id, SUP_A, "INV/5", 10000, cgst=900, sgst=900, period="102026")
    _imp(db, gstco.id, "102026", _2b("102026", b2b=[_inv(SUP_A, "INV/5", 10000, 900, 900)]))
    reco_svc.reconcile_period(db, gstco.id, gstin=GSTIN, return_type="GSTR2B", return_period="102026")
    itc_svc.claim_itc(db, gstco.id, inv.id, period="102026")
    _imp(db, gstco.id, "112026", _2b("112026", b2b=[_inv(SUP_A, "INV/5", 10000, 900, 900)]))
    reco_svc.reconcile_period(db, gstco.id, gstin=GSTIN, return_type="GSTR2B", return_period="112026")
    count = db.execute(select(func.count()).select_from(type(inv)).where(
        type(inv).company_id == gstco.id)).scalar()
    assert count == 1
    db.refresh(inv)
    assert inv.status == ItcStatus.ITC_CLAIMED.value   # claimed state not downgraded


# ----------------------------- ITC reversal/reclaim -------------------------

def _matched_invoice(db, cid):
    inv = _cap(db, cid, SUP_A, "INV/1", 10000, cgst=900, sgst=900)
    _imp(db, cid, "102026", _2b("102026", b2b=[_inv(SUP_A, "INV/1", 10000, 900, 900)]))
    reco_svc.reconcile_period(db, cid, gstin=GSTIN, return_type="GSTR2B", return_period="102026")
    itc_svc.claim_itc(db, cid, inv.id, period="102026")
    return inv


def test_partial_reversal_then_partial_reclaim(db, gstco):
    inv = _matched_invoice(db, gstco.id)
    # reverse half of each head
    itc_svc.reverse_itc(db, gstco.id, inv.id, tax_period="112026", reference="REV-1",
                        cgst=450, sgst=450, reason="supplier not paid in 180 days")
    db.refresh(inv)
    assert inv.status == ItcStatus.ITC_REVERSED.value
    assert inv.reversed_total == Decimal("900.00")
    # partial reclaim of 300 (of 900 reversed) -> still reversed (partial)
    itc_svc.reclaim_itc(db, gstco.id, inv.id, tax_period="122026", reference="RCL-1",
                        cgst=150, sgst=150, reason="paid part")
    db.refresh(inv)
    assert inv.reclaimed_total == Decimal("300.00")
    assert inv.status == ItcStatus.ITC_REVERSED.value   # 600 still outstanding
    # reclaim the remaining 600 -> fully reclaimed
    itc_svc.reclaim_itc(db, gstco.id, inv.id, tax_period="012027", reference="RCL-2",
                        cgst=300, sgst=300, reason="paid rest")
    db.refresh(inv)
    assert inv.status == ItcStatus.ITC_RECLAIMED.value


def test_reclaim_cannot_exceed_reversed(db, gstco):
    inv = _matched_invoice(db, gstco.id)
    itc_svc.reverse_itc(db, gstco.id, inv.id, tax_period="112026", reference="REV-9",
                        cgst=450, sgst=450)
    with pytest.raises(itc_svc.GstItcStateError):
        itc_svc.reclaim_itc(db, gstco.id, inv.id, tax_period="122026", reference="RCL-9",
                            cgst=600, sgst=450)   # 600 > 450 reversed on cgst


def test_duplicate_reclaim_reference_prevented(db, gstco):
    inv = _matched_invoice(db, gstco.id)
    itc_svc.reverse_itc(db, gstco.id, inv.id, tax_period="112026", reference="REV-D",
                        cgst=900, sgst=900)
    itc_svc.reclaim_itc(db, gstco.id, inv.id, tax_period="122026", reference="RCL-D",
                        cgst=450, sgst=450)
    with pytest.raises(itc_svc.GstItcStateError):
        itc_svc.reclaim_itc(db, gstco.id, inv.id, tax_period="122026", reference="RCL-D",
                            cgst=450, sgst=450)   # same reference again


def test_reversal_exceeding_itc_rejected(db, gstco):
    inv = _matched_invoice(db, gstco.id)
    with pytest.raises(itc_svc.GstItcStateError):
        itc_svc.reverse_itc(db, gstco.id, inv.id, tax_period="112026", reference="REV-X",
                            cgst=2000)   # > 900 available


def test_reversal_posting_requires_authorisation(db, gstco):
    inv = _matched_invoice(db, gstco.id)
    with pytest.raises(itc_svc.GstItcStateError):
        itc_svc.reverse_itc(db, gstco.id, inv.id, tax_period="112026", reference="REV-P",
                            cgst=100, post=True, authorize=False)


def test_authorised_reversal_posts_voucher(db, gstco):
    inv = _matched_invoice(db, gstco.id)
    rr = itc_svc.reverse_itc(db, gstco.id, inv.id, tax_period="112026", reference="REV-OK",
                             cgst=450, sgst=450, post=True, authorize=True)
    assert rr.voucher_id is not None
    v = db.get(Voucher, rr.voucher_id)
    assert VoucherStatus(v.status) == VoucherStatus.POSTED
    assert v.posting_reference == "GADJ:ITCREV:REV-OK"


# ---------------------------- cross-utilisation -----------------------------

def _approved_policy(db, cid, rules=None):
    pol = util_svc.create_policy(db, cid, rules=rules, title="FY26-27 ITC policy")
    util_svc.approve_policy(db, cid, pol.id, approver_name="CA Firm LLP")
    return pol


def test_correct_cross_utilisation_proposal(db, gstco):
    _approved_policy(db, gstco.id)
    res = util_svc.propose_cross_adjustment(
        db, gstco.id, tax_period="102026",
        available={"IGST": 1000, "CGST": 500, "SGST": 500},
        liability={"IGST": 300, "CGST": 900, "SGST": 700})
    entries = res["entries"]
    # IGST credit used against IGST then CGST; no CGST<->SGST cross anywhere.
    assert {"credit_head": "IGST", "liability_head": "CGST", "amount": "700.00"} in entries
    for e in entries:
        assert {e["credit_head"], e["liability_head"]} != {"CGST", "SGST"}


def test_prohibited_cross_utilisation_rejected(db, gstco):
    _approved_policy(db, gstco.id)
    with pytest.raises(util_svc.ProhibitedUtilisation):
        util_svc.validate_utilisation(
            [{"credit_head": "CGST", "liability_head": "SGST", "amount": "100"}],
            util_svc.DEFAULT_RULES)


def test_utilisation_requires_approved_policy(db, gstco):
    with pytest.raises(util_svc.GstUtilisationError):
        util_svc.propose_cross_adjustment(db, gstco.id, tax_period="102026",
                                          available={"IGST": 100}, liability={"CGST": 100})


def test_utilisation_posting_gate(db, gstco):
    _approved_policy(db, gstco.id)
    res = util_svc.propose_cross_adjustment(
        db, gstco.id, tax_period="112026", available={"IGST": 1000},
        liability={"CGST": 400, "SGST": 300})
    adj_id = uuid.UUID(res["cross_adjustment_id"])
    # not CA-approved yet, and no authorise -> blocked
    with pytest.raises(util_svc.UtilisationPostingBlocked):
        util_svc.post_cross_adjustment(db, gstco.id, adj_id, authorize=True)
    util_svc.approve_cross_adjustment(db, gstco.id, adj_id, approver_name="CA Firm")
    with pytest.raises(util_svc.UtilisationPostingBlocked):
        util_svc.post_cross_adjustment(db, gstco.id, adj_id, authorize=False)
    voucher, n = util_svc.post_cross_adjustment(db, gstco.id, adj_id, authorize=True)
    assert n == 2 and VoucherStatus(voucher.status) == VoucherStatus.POSTED
    assert voucher.total_debit == voucher.total_credit == Decimal("700.00")


# --------------------------- ledger reconciliation --------------------------

def test_ledger_reconciliation(db, gstco):
    ledger_json = json.dumps({"period": "102026", "as_of": "2026-10-31", "balances": [
        {"head": "IGST", "opening": 0, "credit": 5000, "debit": 0, "closing": 5000},
        {"head": "CGST", "opening": 0, "credit": 2000, "debit": 0, "closing": 2000}]}).encode()
    imp.import_return(db, gstco.id, gstin=GSTIN, financial_year=FY, return_period="102026",
                      return_type=GstReturnType.ECL_CREDIT.value, content=ledger_json,
                      file_name="ecl.json", source=GstImportSource.MANUAL_JSON.value)
    rec = ledger_svc.ledger_reconciliation(db, gstco.id, ledger_type="CREDIT", period="102026")
    heads = {r["head"]: r for r in rec["rows"]}
    assert heads["IGST"]["portal_closing"] == "5000.00"
    assert heads["IGST"]["erp_account"] == "1123"
    # ERP side has no postings in this fresh company -> 0, difference 5000, not reconciled.
    assert heads["IGST"]["reconciled"] is False


# -------------------------------- reports -----------------------------------

def test_reports_json_excel_pdf(db, gstco):
    _cap(db, gstco.id, SUP_A, "INV/1", 10000, cgst=900, sgst=900)
    _imp(db, gstco.id, "102026", _2b("102026", b2b=[_inv(SUP_A, "INV/1", 10000, 900, 900),
                                                    _inv(SUP_B, "X/2", 5000, 450, 450)]))
    reco_svc.reconcile_period(db, gstco.id, gstin=GSTIN, return_type="GSTR2B", return_period="102026")
    rep = reports_svc.build_report(db, gstco.id, "2b_vs_books", "102026")
    assert rep["rows"]
    xls = reports_svc.to_excel(rep)
    pdf = reports_svc.to_pdf(rep)
    assert xls[:2] == b"PK" and pdf[:4] == b"%PDF"
    # available/missing reports build
    assert "rows" in reports_svc.build_report(db, gstco.id, "supplier_missing", "102026")
    assert "rows" in reports_svc.build_report(db, gstco.id, "itc_available", "102026")


# ------------------------------- audit & isolation --------------------------

def test_audit_trail_recorded(db, gstco):
    _imp(db, gstco.id, "102026", _2b("102026", b2b=[_inv(SUP_A, "INV/1", 10000, 900, 900)]))
    db.flush()
    n = db.execute(select(func.count()).select_from(AuditLog).where(
        AuditLog.action == "GST_RETURN_IMPORTED")).scalar()
    assert n == 1


def test_company_isolation(db, gstco):
    other = Company(code="OTHR", name="Other Co", gstin="19AAAAA0000A1Z5")
    db.add(other)
    db.flush()
    _cap(db, gstco.id, SUP_A, "INV/1", 10000, cgst=900, sgst=900)
    _imp(db, gstco.id, "102026", _2b("102026", b2b=[_inv(SUP_A, "INV/1", 10000, 900, 900)]))
    # Other company sees no imports, and reconcile finds nothing to work with.
    assert imp.list_imports(db, other.id) == []
    with pytest.raises(reco_svc.GstItcError):
        reco_svc.reconcile_period(db, other.id, gstin="19AAAAA0000A1Z5", return_type="GSTR2B",
                                  return_period="102026")


def test_cross_company_gstin_rejected(db, gstco):
    with pytest.raises(imp.GstPortalError):
        imp.resolve_company_gstin(db, gstco.id, "09ZZZZZ9999Z1Z9")   # not this company's GSTIN


# ------------------------ portal unavailable / fallback ---------------------

def test_portal_unavailable_fallback(db, gstco):
    # Manual mode (default): a live download refuses honestly; it never fabricates.
    with pytest.raises(conn.ConnectorUnavailable):
        imp.download_and_import(db, gstco.id, gstin=GSTIN, financial_year=FY,
                                return_period="102026", return_type="GSTR2B")
    status = conn.connector_status()
    assert status["live"] is False and "GSP" in status["gsp_dependency"]


# ================================= API ======================================

@pytest.fixture()
def api(client, db, company, auth_headers):
    company.gstin = GSTIN
    company.state_code = company.state_code or "27"
    db.flush()
    return client, auth_headers


def test_api_connector_status_and_template(api):
    client, h = api
    r = client.get("/api/gst/connector/status", headers=h)
    assert r.status_code == 200 and r.json()["live"] is False
    r = client.get("/api/gst/templates/gst-import", params={"return_type": "GSTR2B"}, headers=h)
    assert r.status_code == 200 and r.content[:2] == b"PK"


def test_api_upload_reconcile_register(api):
    client, h = api
    content = _2b("102026", b2b=[_inv(SUP_A, "INV/1", 10000, 900, 900)])
    r = client.post("/api/gst/imports/upload",
                    params={"financial_year": FY, "return_period": "102026",
                            "return_type": "GSTR2B", "source": "MANUAL_JSON"},
                    files={"file": ("2b.json", content, "application/json")}, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["doc_count"] == 1
    # duplicate upload rejected
    r2 = client.post("/api/gst/imports/upload",
                     params={"financial_year": FY, "return_period": "102026",
                             "return_type": "GSTR2B", "source": "MANUAL_JSON"},
                     files={"file": ("2b.json", content, "application/json")}, headers=h)
    assert r2.status_code == 409
    # capture a books invoice, then reconcile
    r = client.post("/api/gst/itc/capture", headers=h, json={
        "supplier_gstin": SUP_A, "supplier_invoice_no": "INV/1", "invoice_date": "2026-10-05",
        "booked_period": "102026", "taxable_value": "10000", "cgst": "900", "sgst": "900"})
    assert r.status_code == 201, r.text
    r = client.post("/api/gst/reconcile", headers=h,
                    json={"return_type": "GSTR2B", "return_period": "102026"})
    assert r.status_code == 200, r.text
    assert r.json()["counts"].get("EXACT") == 1
    r = client.get("/api/gst/itc/register", headers=h)
    assert r.status_code == 200 and r.json()["total"] == 1
    assert r.json()["items"][0]["status"] == ItcStatus.IN_2B_MATCHED.value


def test_api_download_fallback_409(api):
    client, h = api
    r = client.post("/api/gst/imports/download", headers=h,
                    json={"financial_year": FY, "return_period": "102026", "return_type": "GSTR2B"})
    assert r.status_code == 409
    assert "manual" in r.json()["detail"].lower() or "portal" in r.json()["detail"].lower()


def test_api_report_excel_export(api):
    client, h = api
    content = _2b("102026", b2b=[_inv(SUP_A, "INV/1", 10000, 900, 900)])
    client.post("/api/gst/imports/upload",
                params={"financial_year": FY, "return_period": "102026",
                        "return_type": "GSTR2B", "source": "MANUAL_JSON"},
                files={"file": ("2b.json", content, "application/json")}, headers=h)
    client.post("/api/gst/reconcile", headers=h,
                json={"return_type": "GSTR2B", "return_period": "102026"})
    r = client.get("/api/gst/reports/2b_vs_books",
                   params={"period": "102026", "format": "excel"}, headers=h)
    assert r.status_code == 200 and r.content[:2] == b"PK"
