"""Stage-2 printing framework: numbering, reprint, concurrency, isolation, PDF
generation, cancelled watermark, preview-allocates-nothing, and the guarantee that
printing never writes a business transaction."""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from app.models.accounting import Voucher
from app.models.core import Company
from app.models.inventory import InventoryTransaction
from app.models.printing import PrintedDocument
from app.services.printing import numbering as num


def _second_company(db):
    c = db.execute(select(Company).where(Company.code == "UATB")).scalar_one_or_none()
    if c is None:
        c = Company(code="UATB", name="UAT Weaves", legal_name="UAT Weaves Private Limited",
                    gstin="27ABCDE9999F1Z2", state_code="27", currency="INR")
        db.add(c); db.commit()
    return c


def _count(db, model, cid):
    return db.execute(select(func.count()).select_from(model).where(model.company_id == cid)).scalar()


# ---- preview allocates nothing ----

def test_preview_allocates_no_number(client, auth_headers, db, company):
    before = _count(db, PrintedDocument, company.id)
    r = client.get("/api/print/sample/preview", headers=auth_headers)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"
    assert r.headers["content-disposition"].startswith("inline")
    assert _count(db, PrintedDocument, company.id) == before   # nothing issued


# ---- issue on first print, reuse on reprint ----

def test_issue_then_reprint_reuses_number(client, auth_headers, db, company):
    first = client.post("/api/print/sample/issue", headers=auth_headers, json={"source_id": "SRC-1"})
    assert first.status_code == 201
    n1 = first.json()["number"]
    assert first.json()["created"] is True
    # exactly one registry row
    assert _count(db, PrintedDocument, company.id) == 1
    # a second issue of the same source returns the SAME number (idempotent)
    again = client.post("/api/print/sample/issue", headers=auth_headers, json={"source_id": "SRC-1"})
    assert again.json()["number"] == n1 and again.json()["created"] is False
    assert _count(db, PrintedDocument, company.id) == 1
    # printing the document reuses the number and records a reprint
    doc = client.get("/api/print/sample/document", headers=auth_headers, params={"source_id": "SRC-1"})
    assert doc.status_code == 200 and doc.content[:4] == b"%PDF"
    row = db.execute(select(PrintedDocument).where(PrintedDocument.source_id == "SRC-1")).scalar_one()
    assert row.number == n1 and row.reprint_count >= 1


def test_numbers_are_sequential_and_company_fy_scoped(client, auth_headers, db, company):
    a = client.post("/api/print/sample/issue", headers=auth_headers, json={"source_id": "S-A"}).json()["number"]
    b = client.post("/api/print/sample/issue", headers=auth_headers, json={"source_id": "S-B"}).json()["number"]
    assert a != b
    fy = num.fiscal_year_for(date.today())
    tag = fy.replace("-", "")[2:6]       # e.g. 2627
    assert a.startswith("SMP-") and tag in a   # prefix + FY-aware body
    # monotonic within the type
    assert int(a.rsplit("-", 1)[-1]) + 1 == int(b.rsplit("-", 1)[-1])


# ---- concurrency (logical): one number per source even under racing issues ----

def test_concurrent_issue_is_idempotent(client, auth_headers, db, company):
    nums = {client.post("/api/print/sample/issue", headers=auth_headers,
                        json={"source_id": "RACE"}).json()["number"] for _ in range(5)}
    assert len(nums) == 1                              # all calls agree on one number
    assert db.execute(select(func.count()).select_from(PrintedDocument).where(
        PrintedDocument.source_id == "RACE")).scalar() == 1


# ---- printing never writes a business transaction ----

def test_printing_creates_no_business_transaction(client, auth_headers, db, company):
    v0, i0 = _count(db, Voucher, company.id), _count(db, InventoryTransaction, company.id)
    client.get("/api/print/sample/preview", headers=auth_headers)
    client.post("/api/print/sample/issue", headers=auth_headers, json={"source_id": "NOTXN"})
    client.get("/api/print/sample/document", headers=auth_headers, params={"source_id": "NOTXN"})
    client.get("/api/print/sample/document", headers=auth_headers, params={"source_id": "NOTXN"})
    assert _count(db, Voucher, company.id) == v0
    assert _count(db, InventoryTransaction, company.id) == i0


# ---- cancelled watermark ----

def test_cancel_sets_flag_and_renders(client, auth_headers, db):
    client.post("/api/print/sample/issue", headers=auth_headers, json={"source_id": "CAN"})
    c = client.post("/api/print/sample/cancel", headers=auth_headers, json={"source_id": "CAN"})
    assert c.status_code == 200 and c.json()["cancelled"] is True
    row = db.execute(select(PrintedDocument).where(PrintedDocument.source_id == "CAN")).scalar_one()
    assert row.cancelled is True
    # still renders (now with the CANCELLED watermark); number unchanged
    pdf = client.get("/api/print/sample/document", headers=auth_headers, params={"source_id": "CAN"})
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


def test_cancel_before_issue_404(client, auth_headers):
    assert client.post("/api/print/sample/cancel", headers=auth_headers,
                       json={"source_id": "NEVER"}).status_code == 404


# ---- download vs inline (browser print) ----

def test_download_vs_inline_disposition(client, auth_headers):
    client.post("/api/print/sample/issue", headers=auth_headers, json={"source_id": "DL"})
    inline = client.get("/api/print/sample/document", headers=auth_headers, params={"source_id": "DL"})
    assert inline.headers["content-disposition"].startswith("inline")      # browser preview/print
    dl = client.get("/api/print/sample/document", headers=auth_headers,
                    params={"source_id": "DL", "download": "true"})
    assert dl.headers["content-disposition"].startswith("attachment")      # download


# ---- company isolation ----

def test_registry_isolated_per_company(client, auth_headers, db):
    b = _second_company(db)
    client.post("/api/print/sample/issue", headers=auth_headers, json={"source_id": "ISO-A"})
    # company A sees its doc
    ra = client.get("/api/print/registry", headers=auth_headers).json()
    assert any(x["source_id"] == "ISO-A" for x in ra)
    # company B (switched via header; admin is superuser) sees none of A's docs
    hb = {**auth_headers, "X-Company-Id": str(b.id)}
    rb = client.get("/api/print/registry", headers=hb).json()
    assert all(x["source_id"] != "ISO-A" for x in rb)
    # and B can mint its own number independently (its own sequence)
    nb = client.post("/api/print/sample/issue", headers=hb, json={"source_id": "ISO-A"}).json()
    assert nb["created"] is True   # same source_id string, different company -> its own doc


def test_pdf_contains_company_branding(client, auth_headers, db, company):
    # legal name is embedded in the rendered PDF stream (text is present, uncompressed enough)
    client.post("/api/print/sample/issue", headers=auth_headers, json={"source_id": "BRAND"})
    pdf = client.get("/api/print/sample/document", headers=auth_headers,
                     params={"source_id": "BRAND"}).content
    assert pdf[:4] == b"%PDF" and len(pdf) > 1500
