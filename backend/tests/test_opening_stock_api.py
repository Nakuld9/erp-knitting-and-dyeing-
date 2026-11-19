"""Stage 6F-P2 — opening-stock API + Excel round-trip tests.

Downloads the real templates, fills them with openpyxl, uploads them through the
import endpoints and asserts validation, dedup, rejections, re-import rejection,
permissions, factory authorization, the reconciliation preview and the full
submit/approve stock-commit path.
"""
from __future__ import annotations

import io

import openpyxl
from sqlalchemy import func, select

from app.core.security import hash_password
from app.models.accounting import Voucher
from app.models.core import Branch, Permission, Role, User
from app.models.hybrid import WarehouseCategory
from app.models.masters import Supplier, UnitOfMeasure, Warehouse, YarnMaster
from app.models.opening_stock import OpeningImportFile
from app.models.trading import TradingFabricItem

ED = "2026-11-15"


# ------------------------------- fixtures -----------------------------------

def _seed_masters(db, cid):
    uom = db.execute(select(UnitOfMeasure).where(UnitOfMeasure.code == "KG")).scalars().first()
    if uom is None:
        uom = UnitOfMeasure(code="KG", name="Kilogram", dimension="MASS", base_factor=1)
        db.add(uom)
    fac = Branch(company_id=cid, code="FAC1", name="Factory 1")
    db.add(fac); db.flush()
    db.add_all([
        Warehouse(company_id=cid, code="YWH", name="Yarn Store", kind="STORE",
                  category=WarehouseCategory.YARN_STORE.value, branch_id=fac.id),
        Warehouse(company_id=cid, code="TWH", name="Trading WH", kind="STORE",
                  category=WarehouseCategory.TRADING.value, branch_id=fac.id),
        YarnMaster(company_id=cid, code="Y1", name="Cotton 30s", uom_id=uom.id, is_active=True),
        Supplier(company_id=cid, code="SUP1", name="Supplier One"),
        TradingFabricItem(company_id=cid, code="T1", name="Trading Jersey"),
    ])
    db.commit()


def _mk_user(db, cid, username, perms, superuser=False):
    role = Role(code=f"R_{username}", name=username)
    for code in perms:
        role.permissions.append(db.execute(
            select(Permission).where(Permission.code == code)).scalar_one())
    u = User(username=username, email=f"{username}@demo.local", full_name=username.title(),
             hashed_password=hash_password("passpass123"), is_active=True,
             is_superuser=superuser, company_id=cid)
    u.roles.append(role)
    db.add(u); db.flush()
    return u


def _login(client, username):
    tok = client.post("/api/auth/login",
                      data={"username": username, "password": "passpass123"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def _fill(template_bytes: bytes, rows: list[dict]) -> bytes:
    """Load a downloaded template and append data rows keyed by header name."""
    wb = openpyxl.load_workbook(io.BytesIO(template_bytes))
    ws = wb.active
    headers = [c.value for c in ws[2]]
    for r, row in enumerate(rows, start=3):
        for cidx, h in enumerate(headers, start=1):
            if h in row and row[h] is not None:
                ws.cell(row=r, column=cidx, value=row[h])
    buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()


def _mk_batch(client, hdr, btype):
    return client.post("/api/opening/batches", headers=hdr,
                       json={"batch_type": btype, "effective_date": ED}).json()["id"]


def _upload(client, hdr, bid, kind, content, filename="x.xlsx"):
    return client.post(f"/api/opening/batches/{bid}/{kind}/import", headers=hdr,
                       files={"file": (filename, content,
                                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})


# ------------------------------- templates ----------------------------------

def test_templates_download(client, db, company, auth_headers):
    _seed_masters(db, company.id)
    for kind in ("yarn", "trading"):
        r = client.get(f"/api/opening/templates/{kind}", headers=auth_headers)
        assert r.status_code == 200
        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        assert wb.active["A1"].value.startswith("OPENING_TEMPLATE")


def test_template_download_requires_read(client, db, company, auth_headers):
    _mk_user(db, company.id, "ob_norole", ["master.read"]); db.commit()
    nh = _login(client, "ob_norole")
    assert client.get("/api/opening/templates/yarn", headers=nh).status_code == 403


# ------------------------------- yarn import --------------------------------

def _yarn_rows(**over):
    base = {"yarn_code": "Y1", "internal_lot_no": "LOT-1", "warehouse_code": "YWH",
            "factory_code": "FAC1", "quantity": 100, "uom_code": "KG", "unit_cost": 250,
            "total_value": 25000, "effective_date": ED, "source_document": "GRN-1"}
    base.update(over)
    return base


def test_yarn_import_valid(client, db, company, auth_headers):
    _seed_masters(db, company.id)
    tpl = client.get("/api/opening/templates/yarn", headers=auth_headers).content
    content = _fill(tpl, [_yarn_rows()])
    bid = _mk_batch(client, auth_headers, "YARN")
    r = _upload(client, auth_headers, bid, "yarn", content)
    assert r.status_code == 201, r.text
    assert r.json()["valid"] == 1 and r.json()["invalid"] == 0
    lines = client.get(f"/api/opening/batches/{bid}/lines", headers=auth_headers).json()
    assert lines["total"] == 1 and lines["items"][0]["validation_status"] == "VALID"


def test_yarn_import_invalid_rows_reported(client, db, company, auth_headers):
    _seed_masters(db, company.id)
    tpl = client.get("/api/opening/templates/yarn", headers=auth_headers).content
    content = _fill(tpl, [
        _yarn_rows(internal_lot_no="A"),
        _yarn_rows(internal_lot_no="B", warehouse_code="TWH"),   # wrong category
        _yarn_rows(internal_lot_no="C", quantity=-3),            # bad qty
        _yarn_rows(internal_lot_no="D", yarn_code="NOPE"),       # unknown SKU
    ])
    bid = _mk_batch(client, auth_headers, "YARN")
    r = _upload(client, auth_headers, bid, "yarn", content)
    assert r.json()["valid"] == 1 and r.json()["invalid"] == 3
    rej = client.get(f"/api/opening/batches/{bid}/rejections", headers=auth_headers).json()
    assert len(rej["rejections"]) == 3
    assert all(item["errors"] for item in rej["rejections"])


def test_duplicate_rows_detected(client, db, company, auth_headers):
    _seed_masters(db, company.id)
    tpl = client.get("/api/opening/templates/yarn", headers=auth_headers).content
    content = _fill(tpl, [_yarn_rows(), _yarn_rows()])  # identical lot+wh
    bid = _mk_batch(client, auth_headers, "YARN")
    r = _upload(client, auth_headers, bid, "yarn", content)
    assert r.json()["valid"] == 1 and r.json()["invalid"] == 1


def test_reimport_same_file_rejected(client, db, company, auth_headers):
    _seed_masters(db, company.id)
    tpl = client.get("/api/opening/templates/yarn", headers=auth_headers).content
    content = _fill(tpl, [_yarn_rows()])
    bid = _mk_batch(client, auth_headers, "YARN")
    assert _upload(client, auth_headers, bid, "yarn", content).status_code == 201
    dup = _upload(client, auth_headers, bid, "yarn", content)
    assert dup.status_code == 409, dup.text


def test_original_file_retained(client, db, company, auth_headers):
    _seed_masters(db, company.id)
    tpl = client.get("/api/opening/templates/yarn", headers=auth_headers).content
    content = _fill(tpl, [_yarn_rows()])
    bid = _mk_batch(client, auth_headers, "YARN")
    _upload(client, auth_headers, bid, "yarn", content, filename="opening_jan.xlsx")
    f = db.execute(select(OpeningImportFile)).scalars().one()
    assert f.file_name == "opening_jan.xlsx" and f.content == content and len(f.file_hash) == 64


def test_wrong_template_type_rejected(client, db, company, auth_headers):
    _seed_masters(db, company.id)
    trading_tpl = client.get("/api/opening/templates/trading", headers=auth_headers).content
    bid = _mk_batch(client, auth_headers, "YARN")
    # Uploading a trading template to the yarn endpoint must fail template validation.
    r = _upload(client, auth_headers, bid, "yarn", trading_tpl)
    assert r.status_code == 422, r.text


def test_missing_headers_rejected(client, db, company, auth_headers):
    _seed_masters(db, company.id)
    wb = openpyxl.Workbook(); ws = wb.active
    ws["A1"] = "OPENING_TEMPLATE|VERSION=1|TYPE=YARN"
    ws.cell(row=2, column=1, value="yarn_code")  # only one header
    buf = io.BytesIO(); wb.save(buf)
    bid = _mk_batch(client, auth_headers, "YARN")
    r = _upload(client, auth_headers, bid, "yarn", buf.getvalue())
    assert r.status_code == 422, r.text


def test_import_wrong_batch_type_rejected(client, db, company, auth_headers):
    _seed_masters(db, company.id)
    tpl = client.get("/api/opening/templates/yarn", headers=auth_headers).content
    content = _fill(tpl, [_yarn_rows()])
    bid = _mk_batch(client, auth_headers, "TRADING_FABRIC")  # wrong type for yarn import
    r = _upload(client, auth_headers, bid, "yarn", content)
    assert r.status_code == 409, r.text


# ------------------------------ trading import ------------------------------

def _trade_rows(**over):
    base = {"trading_sku_code": "T1", "warehouse_code": "TWH", "factory_code": "FAC1",
            "quantity": 50, "uom_code": "KG", "unit_cost": 400, "total_value": 20000,
            "effective_date": ED, "source_document": "INV-1"}
    base.update(over)
    return base


def test_trading_import_valid(client, db, company, auth_headers):
    _seed_masters(db, company.id)
    tpl = client.get("/api/opening/templates/trading", headers=auth_headers).content
    content = _fill(tpl, [_trade_rows()])
    bid = _mk_batch(client, auth_headers, "TRADING_FABRIC")
    r = _upload(client, auth_headers, bid, "trading", content)
    assert r.status_code == 201 and r.json()["valid"] == 1


# --------------------------- preview + lifecycle ----------------------------

def test_preview_and_full_approval_commits_stock(client, db, company, auth_headers):
    _seed_masters(db, company.id)
    # Cutover operators are authorised company-wide (factory.access_all).
    maker = _mk_user(db, company.id, "ob_m", ["opening.manage", "opening.read", "factory.access_all"])
    checker = _mk_user(db, company.id, "ob_c", ["opening.manage", "opening.read", "factory.access_all"])
    db.commit()
    mh, ch = _login(client, "ob_m"), _login(client, "ob_c")
    tpl = client.get("/api/opening/templates/yarn", headers=mh).content
    content = _fill(tpl, [_yarn_rows()])
    bid = _mk_batch(client, mh, "YARN")
    _upload(client, mh, bid, "yarn", content)

    prev = client.get(f"/api/opening/batches/{bid}/preview", headers=mh).json()
    assert prev["line_count"] == 1 and prev["accounting_posting"] == "BLOCKED_PENDING_CA_APPROVAL"
    assert prev["proposed_total_value"] == "25000.00"

    before_v = db.execute(select(func.count()).select_from(Voucher)).scalar()
    assert client.post(f"/api/opening/batches/{bid}/submit", headers=mh).status_code == 200
    ap = client.post(f"/api/opening/batches/{bid}/approve", headers=ch)
    assert ap.status_code == 200 and ap.json()["status"] == "APPROVED"
    assert ap.json()["lines_committed"] == 1
    # No accounting voucher was posted by the opening flow.
    assert db.execute(select(func.count()).select_from(Voucher)).scalar() == before_v
    line = client.get(f"/api/opening/batches/{bid}/lines", headers=mh).json()["items"][0]
    assert line["validation_status"] == "COMMITTED" and line["stock_layer_id"]


def test_self_approval_blocked_api(client, db, company, auth_headers):
    _seed_masters(db, company.id)
    tpl = client.get("/api/opening/templates/yarn", headers=auth_headers).content
    bid = _mk_batch(client, auth_headers, "YARN")
    _upload(client, auth_headers, bid, "yarn", _fill(tpl, [_yarn_rows()]))
    client.post(f"/api/opening/batches/{bid}/submit", headers=auth_headers)
    # Same admin user cannot approve their own submission.
    assert client.post(f"/api/opening/batches/{bid}/approve", headers=auth_headers).status_code == 409


def test_submit_blocked_with_invalid_lines_api(client, db, company, auth_headers):
    _seed_masters(db, company.id)
    tpl = client.get("/api/opening/templates/yarn", headers=auth_headers).content
    content = _fill(tpl, [_yarn_rows(warehouse_code="TWH")])  # invalid
    bid = _mk_batch(client, auth_headers, "YARN")
    _upload(client, auth_headers, bid, "yarn", content)
    assert client.post(f"/api/opening/batches/{bid}/submit", headers=auth_headers).status_code == 409


# ------------------------------ permissions ---------------------------------

def test_import_requires_manage(client, db, company, auth_headers):
    _seed_masters(db, company.id)
    _mk_user(db, company.id, "ob_reader2", ["opening.read"]); db.commit()
    rh = _login(client, "ob_reader2")
    tpl = client.get("/api/opening/templates/yarn", headers=rh).content
    bid_admin = _mk_batch(client, auth_headers, "YARN")
    r = _upload(client, rh, bid_admin, "yarn", _fill(tpl, [_yarn_rows()]))
    assert r.status_code == 403, r.text


def test_factory_authorization_enforced_on_import(client, db, company, auth_headers):
    _seed_masters(db, company.id)
    # Has opening.manage but NOT factory.access_all / any factory membership.
    _mk_user(db, company.id, "ob_nofac", ["opening.manage", "opening.read"]); db.commit()
    nh = _login(client, "ob_nofac")
    bid = _mk_batch(client, nh, "YARN")
    tpl = client.get("/api/opening/templates/yarn", headers=nh).content
    content = _fill(tpl, [_yarn_rows(factory_code="FAC1")])
    r = _upload(client, nh, bid, "yarn", content)
    assert r.status_code == 403, r.text
    # Nothing was persisted.
    assert db.execute(select(func.count()).select_from(OpeningImportFile)).scalar() == 0


def test_cross_company_batch_not_found(client, db, company, auth_headers):
    _seed_masters(db, company.id)
    # A second company + its own user; it must not reach the first company's batch.
    from app.models.core import Company
    other = Company(code="OC2", name="Other Co 2"); db.add(other); db.flush()
    _mk_user(db, other.id, "other_u", ["opening.manage", "opening.read"]); db.commit()
    oh = _login(client, "other_u")
    bid = _mk_batch(client, auth_headers, "YARN")  # owned by the first company
    tpl = client.get("/api/opening/templates/yarn", headers=oh).content
    r = _upload(client, oh, bid, "yarn", _fill(tpl, [_yarn_rows()]))
    assert r.status_code == 404, r.text


def test_route_surface(client, auth_headers):
    schema = client.get("/openapi.json").json()
    for p in ["/api/opening/templates/yarn", "/api/opening/templates/trading",
              "/api/opening/batches/{batch_id}/yarn/import",
              "/api/opening/batches/{batch_id}/trading/import",
              "/api/opening/batches/{batch_id}/lines",
              "/api/opening/batches/{batch_id}/preview",
              "/api/opening/batches/{batch_id}/validate",
              "/api/opening/batches/{batch_id}/rejections"]:
        assert p in schema["paths"], p
