"""Stage 6F-P4 — vendor-custody opening API + Excel round-trip tests."""
from __future__ import annotations

import io

import openpyxl
from sqlalchemy import func, select

from app.core.security import hash_password
from app.models.accounting import Voucher
from app.models.core import Branch, Permission, Role, User
from app.models.masters import FabricType, Supplier, UnitOfMeasure
from app.models.opening_stock import OpeningImportFile

ED = "2026-11-15"


def _seed(db, cid):
    uom = db.execute(select(UnitOfMeasure).where(UnitOfMeasure.code == "KG")).scalars().first()
    if uom is None:
        uom = UnitOfMeasure(code="KG", name="Kilogram", dimension="MASS", base_factor=1); db.add(uom)
    fac = Branch(company_id=cid, code="FAC1", name="Factory 1"); db.add(fac); db.flush()
    db.add_all([
        Supplier(company_id=cid, code="JW1", name="Knit Vendor", is_job_worker=True),
        Supplier(company_id=cid, code="SUP9", name="Plain", is_job_worker=False),
        __import__("app.models.masters", fromlist=["YarnMaster"]).YarnMaster(
            company_id=cid, code="Y1", name="Cotton", is_active=True),
        FabricType(company_id=cid, code="SJ160", name="SJ160"),
    ])
    db.commit()


def _mk_user(db, cid, username, perms):
    role = Role(code=f"R_{username}", name=username)
    for code in perms:
        role.permissions.append(db.execute(select(Permission).where(Permission.code == code)).scalar_one())
    u = User(username=username, email=f"{username}@demo.local", full_name=username.title(),
             hashed_password=hash_password("passpass123"), is_active=True,
             is_superuser=False, company_id=cid)
    u.roles.append(role); db.add(u); db.flush()
    return u


def _login(client, username):
    tok = client.post("/api/auth/login",
                      data={"username": username, "password": "passpass123"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def _fill(tpl, rows):
    wb = openpyxl.load_workbook(io.BytesIO(tpl)); ws = wb.active
    headers = [c.value for c in ws[2]]
    for r, row in enumerate(rows, start=3):
        for cidx, h in enumerate(headers, start=1):
            if h in row and row[h] is not None:
                ws.cell(row=r, column=cidx, value=row[h])
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def _mk_batch(client, hdr, btype="VENDOR_CUSTODY"):
    return client.post("/api/opening/batches", headers=hdr,
                       json={"batch_type": btype, "effective_date": ED}).json()["id"]


def _upload(client, hdr, bid, content, filename="custody.xlsx"):
    return client.post(f"/api/opening/batches/{bid}/vendor-custody/import", headers=hdr,
                       files={"file": (filename, content,
                                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})


def _yrow(**over):
    base = {"line_kind": "VENDOR_CUSTODY", "custody_type": "YARN", "vendor_code": "JW1",
            "process": "KNITTING", "factory_code": "FAC1", "yarn_code": "Y1",
            "internal_lot_no": "LOT-C1", "net_weight_kg": 50, "uom_code": "KG",
            "unit_cost": 250, "total_value": 12500, "valuation_source": "vendor confirmation",
            "effective_date": ED, "source_document": "OPEN-C"}
    base.update(over); return base


def test_template_download(client, db, company, auth_headers):
    _seed(db, company.id)
    r = client.get("/api/opening/templates/vendor-custody", headers=auth_headers)
    assert r.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb.active["A1"].value == "OPENING_TEMPLATE|VERSION=1|TYPE=VENDOR_CUSTODY"


def test_import_valid_and_lines(client, db, company, auth_headers):
    _seed(db, company.id)
    tpl = client.get("/api/opening/templates/vendor-custody", headers=auth_headers).content
    bid = _mk_batch(client, auth_headers)
    r = _upload(client, auth_headers, bid, _fill(tpl, [_yrow()]))
    assert r.status_code == 201 and r.json()["valid"] == 1
    lines = client.get(f"/api/opening/batches/{bid}/custody-lines", headers=auth_headers).json()
    assert lines["total"] == 1 and lines["items"][0]["custody_type"] == "YARN"


def test_import_rejections(client, db, company, auth_headers):
    _seed(db, company.id)
    tpl = client.get("/api/opening/templates/vendor-custody", headers=auth_headers).content
    bid = _mk_batch(client, auth_headers)
    content = _fill(tpl, [
        _yrow(internal_lot_no="A"),
        _yrow(internal_lot_no="B", vendor_code="SUP9"),       # not a job worker
        _yrow(internal_lot_no="C", net_weight_kg=-1),         # bad qty
        _yrow(internal_lot_no="D", valuation_source=None),    # missing valuation source
    ])
    r = _upload(client, auth_headers, bid, content)
    assert r.json()["valid"] == 1 and r.json()["invalid"] == 3
    rej = client.get(f"/api/opening/batches/{bid}/custody-rejections", headers=auth_headers).json()
    assert len(rej["rejections"]) == 3


def test_wrong_template_type_rejected(client, db, company, auth_headers):
    _seed(db, company.id)
    yarn_tpl = client.get("/api/opening/templates/yarn", headers=auth_headers).content
    bid = _mk_batch(client, auth_headers)
    assert _upload(client, auth_headers, bid, yarn_tpl).status_code == 422


def test_reimport_same_file_rejected(client, db, company, auth_headers):
    _seed(db, company.id)
    tpl = client.get("/api/opening/templates/vendor-custody", headers=auth_headers).content
    content = _fill(tpl, [_yrow()])
    bid = _mk_batch(client, auth_headers)
    assert _upload(client, auth_headers, bid, content).status_code == 201
    assert _upload(client, auth_headers, bid, content).status_code == 409


def test_original_file_retained(client, db, company, auth_headers):
    _seed(db, company.id)
    tpl = client.get("/api/opening/templates/vendor-custody", headers=auth_headers).content
    content = _fill(tpl, [_yrow()])
    bid = _mk_batch(client, auth_headers)
    _upload(client, auth_headers, bid, content, filename="cust_jan.xlsx")
    f = db.execute(select(OpeningImportFile)).scalars().one()
    assert f.file_name == "cust_jan.xlsx" and f.content == content and len(f.file_hash) == 64


def test_preview_and_full_approval(client, db, company, auth_headers):
    _seed(db, company.id)
    maker = _mk_user(db, company.id, "cm", ["opening.manage", "opening.read", "factory.access_all"])
    checker = _mk_user(db, company.id, "cc", ["opening.manage", "opening.read", "factory.access_all"])
    db.commit()
    mh, ch = _login(client, "cm"), _login(client, "cc")
    tpl = client.get("/api/opening/templates/vendor-custody", headers=mh).content
    bid = _mk_batch(client, mh)
    _upload(client, mh, bid, _fill(tpl, [_yrow()]))
    prev = client.get(f"/api/opening/batches/{bid}/custody-preview", headers=mh).json()
    assert prev["line_count"] == 1 and prev["accounting_posting"] == "BLOCKED_PENDING_CA_APPROVAL"
    assert prev["proposed_total_value"] == "12500.00" and prev["by_vendor"]
    before_v = db.execute(select(func.count()).select_from(Voucher)).scalar()
    assert client.post(f"/api/opening/batches/{bid}/submit", headers=mh).status_code == 200
    ap = client.post(f"/api/opening/batches/{bid}/approve", headers=ch)
    assert ap.status_code == 200 and ap.json()["lines_committed"] == 1 and ap.json()["status"] == "APPROVED"
    assert db.execute(select(func.count()).select_from(Voucher)).scalar() == before_v


def test_self_approval_blocked(client, db, company, auth_headers):
    _seed(db, company.id)
    tpl = client.get("/api/opening/templates/vendor-custody", headers=auth_headers).content
    bid = _mk_batch(client, auth_headers)
    _upload(client, auth_headers, bid, _fill(tpl, [_yrow()]))
    client.post(f"/api/opening/batches/{bid}/submit", headers=auth_headers)
    assert client.post(f"/api/opening/batches/{bid}/approve", headers=auth_headers).status_code == 409


def test_import_requires_manage(client, db, company, auth_headers):
    _seed(db, company.id)
    _mk_user(db, company.id, "creader", ["opening.read"]); db.commit()
    rh = _login(client, "creader")
    tpl = client.get("/api/opening/templates/vendor-custody", headers=rh).content
    bid = _mk_batch(client, auth_headers)
    assert _upload(client, rh, bid, _fill(tpl, [_yrow()])).status_code == 403


def test_cross_company_batch_not_found(client, db, company, auth_headers):
    _seed(db, company.id)
    from app.models.core import Company
    other = Company(code="COC", name="Other"); db.add(other); db.flush()
    _mk_user(db, other.id, "cother", ["opening.manage", "opening.read"]); db.commit()
    oh = _login(client, "cother")
    bid = _mk_batch(client, auth_headers)
    tpl = client.get("/api/opening/templates/vendor-custody", headers=oh).content
    assert _upload(client, oh, bid, _fill(tpl, [_yrow()])).status_code == 404


def test_wrong_batch_type_rejected(client, db, company, auth_headers):
    _seed(db, company.id)
    tpl = client.get("/api/opening/templates/vendor-custody", headers=auth_headers).content
    bid = _mk_batch(client, auth_headers, btype="YARN")  # not a custody batch
    assert _upload(client, auth_headers, bid, _fill(tpl, [_yrow()])).status_code == 409


def test_route_surface(client, auth_headers):
    schema = client.get("/openapi.json").json()
    for p in ["/api/opening/templates/vendor-custody",
              "/api/opening/batches/{batch_id}/vendor-custody/import",
              "/api/opening/batches/{batch_id}/custody-lines",
              "/api/opening/batches/{batch_id}/custody-preview",
              "/api/opening/batches/{batch_id}/custody-rejections"]:
        assert p in schema["paths"], p
