"""Stage 6F-P3 — manufactured-roll opening API + Excel round-trip tests."""
from __future__ import annotations

import io

import openpyxl
from sqlalchemy import func, select

from app.core.security import hash_password
from app.models.accounting import Voucher
from app.models.core import Branch, Permission, Role, User
from app.models.hybrid import WarehouseCategory
from app.models.manufacturing import FabricRoll, RollKind
from app.models.masters import FabricType, UnitOfMeasure, Warehouse
from app.models.opening_stock import OpeningImportFile

ED = "2026-11-15"


def _seed(db, cid):
    uom = db.execute(select(UnitOfMeasure).where(UnitOfMeasure.code == "KG")).scalars().first()
    if uom is None:
        uom = UnitOfMeasure(code="KG", name="Kilogram", dimension="MASS", base_factor=1)
        db.add(uom)
    fac = Branch(company_id=cid, code="FAC1", name="Factory 1")
    db.add(fac); db.flush()
    db.add_all([
        Warehouse(company_id=cid, code="GWH", name="Greige", kind="GREIGE",
                  category=WarehouseCategory.GREIGE.value, branch_id=fac.id),
        Warehouse(company_id=cid, code="SWH", name="Semi", kind="STORE",
                  category=WarehouseCategory.SEMI_FINISHED.value, branch_id=fac.id),
        FabricType(company_id=cid, code="SJ160", name="Single Jersey 160"),
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


def _fill(template_bytes, rows):
    wb = openpyxl.load_workbook(io.BytesIO(template_bytes))
    ws = wb.active
    headers = [c.value for c in ws[2]]
    for r, row in enumerate(rows, start=3):
        for cidx, h in enumerate(headers, start=1):
            if h in row and row[h] is not None:
                ws.cell(row=r, column=cidx, value=row[h])
    buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()


def _mk_batch(client, hdr):
    return client.post("/api/opening/batches", headers=hdr,
                       json={"batch_type": "MANUFACTURED_ROLLS", "effective_date": ED}).json()["id"]


def _upload(client, hdr, bid, content, filename="rolls.xlsx"):
    return client.post(f"/api/opening/batches/{bid}/manufactured-rolls/import", headers=hdr,
                       files={"file": (filename, content,
                                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})


def _rrow(**over):
    base = {"legacy_label": "HW-1", "roll_kind": "GREIGE", "fabric_sku_or_structure": "SJ160",
            "gsm": 160, "width_cm": 180, "net_weight_kg": 42.5, "uom_code": "KG",
            "warehouse_code": "GWH", "factory_code": "FAC1", "quality_status": "OK",
            "unit_cost": 300, "total_value": 12750, "effective_date": ED,
            "source_document": "OPEN-1", "physical_verification_status": "VERIFIED"}
    base.update(over)
    return base


def test_template_download(client, db, company, auth_headers):
    _seed(db, company.id)
    r = client.get("/api/opening/templates/manufactured-rolls", headers=auth_headers)
    assert r.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb.active["A1"].value == "OPENING_TEMPLATE|VERSION=1|TYPE=MANUFACTURED_ROLLS"


def test_import_valid_and_lines(client, db, company, auth_headers):
    _seed(db, company.id)
    tpl = client.get("/api/opening/templates/manufactured-rolls", headers=auth_headers).content
    bid = _mk_batch(client, auth_headers)
    r = _upload(client, auth_headers, bid, _fill(tpl, [_rrow()]))
    assert r.status_code == 201 and r.json()["valid"] == 1
    lines = client.get(f"/api/opening/batches/{bid}/roll-lines", headers=auth_headers).json()
    assert lines["total"] == 1 and lines["items"][0]["registration_mode"] == "MINT_NEW"


def test_import_rejections_reported(client, db, company, auth_headers):
    _seed(db, company.id)
    tpl = client.get("/api/opening/templates/manufactured-rolls", headers=auth_headers).content
    bid = _mk_batch(client, auth_headers)
    content = _fill(tpl, [
        _rrow(legacy_label="A"),
        _rrow(legacy_label="B", warehouse_code="SWH"),       # strict stage
        _rrow(legacy_label="C", net_weight_kg=-1),           # bad weight
        _rrow(legacy_label="D", fabric_sku_or_structure=None),  # missing sku
    ])
    r = _upload(client, auth_headers, bid, content)
    assert r.json()["valid"] == 1 and r.json()["invalid"] == 3
    rej = client.get(f"/api/opening/batches/{bid}/roll-rejections", headers=auth_headers).json()
    assert len(rej["rejections"]) == 3 and all(x["errors"] for x in rej["rejections"])


def test_wrong_template_type_rejected(client, db, company, auth_headers):
    _seed(db, company.id)
    yarn_tpl = client.get("/api/opening/templates/yarn", headers=auth_headers).content
    bid = _mk_batch(client, auth_headers)
    assert _upload(client, auth_headers, bid, yarn_tpl).status_code == 422


def test_reimport_same_file_rejected(client, db, company, auth_headers):
    _seed(db, company.id)
    tpl = client.get("/api/opening/templates/manufactured-rolls", headers=auth_headers).content
    content = _fill(tpl, [_rrow()])
    bid = _mk_batch(client, auth_headers)
    assert _upload(client, auth_headers, bid, content).status_code == 201
    assert _upload(client, auth_headers, bid, content).status_code == 409


def test_original_file_retained(client, db, company, auth_headers):
    _seed(db, company.id)
    tpl = client.get("/api/opening/templates/manufactured-rolls", headers=auth_headers).content
    content = _fill(tpl, [_rrow()])
    bid = _mk_batch(client, auth_headers)
    _upload(client, auth_headers, bid, content, filename="rolls_jan.xlsx")
    f = db.execute(select(OpeningImportFile)).scalars().one()
    assert f.file_name == "rolls_jan.xlsx" and f.content == content and len(f.file_hash) == 64


def test_preview_and_full_approval(client, db, company, auth_headers):
    _seed(db, company.id)
    maker = _mk_user(db, company.id, "rm", ["opening.manage", "opening.read", "factory.access_all"])
    checker = _mk_user(db, company.id, "rc", ["opening.manage", "opening.read", "factory.access_all"])
    db.commit()
    mh, ch = _login(client, "rm"), _login(client, "rc")
    tpl = client.get("/api/opening/templates/manufactured-rolls", headers=mh).content
    bid = _mk_batch(client, mh)
    _upload(client, mh, bid, _fill(tpl, [_rrow()]))

    prev = client.get(f"/api/opening/batches/{bid}/roll-preview", headers=mh).json()
    assert prev["line_count"] == 1 and prev["accounting_posting"] == "BLOCKED_PENDING_CA_APPROVAL"
    assert prev["proposed_total_value"] == "12750.00"

    before_v = db.execute(select(func.count()).select_from(Voucher)).scalar()
    assert client.post(f"/api/opening/batches/{bid}/submit", headers=mh).status_code == 200
    ap = client.post(f"/api/opening/batches/{bid}/approve", headers=ch)
    assert ap.status_code == 200 and ap.json()["status"] == "APPROVED"
    assert ap.json()["lines_committed"] == 1
    assert db.execute(select(func.count()).select_from(Voucher)).scalar() == before_v
    roll = db.execute(select(FabricRoll).where(FabricRoll.legacy_label == "HW-1")).scalar_one()
    assert roll.roll_no.startswith("GR-") and roll.kind in (RollKind.GREIGE, "GREIGE")


def test_self_approval_blocked_api(client, db, company, auth_headers):
    _seed(db, company.id)
    tpl = client.get("/api/opening/templates/manufactured-rolls", headers=auth_headers).content
    bid = _mk_batch(client, auth_headers)
    _upload(client, auth_headers, bid, _fill(tpl, [_rrow()]))
    client.post(f"/api/opening/batches/{bid}/submit", headers=auth_headers)
    assert client.post(f"/api/opening/batches/{bid}/approve", headers=auth_headers).status_code == 409


def test_import_requires_manage(client, db, company, auth_headers):
    _seed(db, company.id)
    _mk_user(db, company.id, "rreader", ["opening.read"]); db.commit()
    rh = _login(client, "rreader")
    tpl = client.get("/api/opening/templates/manufactured-rolls", headers=rh).content
    bid = _mk_batch(client, auth_headers)
    assert _upload(client, rh, bid, _fill(tpl, [_rrow()])).status_code == 403


def test_cross_company_batch_not_found(client, db, company, auth_headers):
    _seed(db, company.id)
    from app.models.core import Company
    other = Company(code="ROC", name="Other Roll Co"); db.add(other); db.flush()
    _mk_user(db, other.id, "rother", ["opening.manage", "opening.read"]); db.commit()
    oh = _login(client, "rother")
    bid = _mk_batch(client, auth_headers)
    tpl = client.get("/api/opening/templates/manufactured-rolls", headers=oh).content
    assert _upload(client, oh, bid, _fill(tpl, [_rrow()])).status_code == 404


def test_route_surface(client, auth_headers):
    schema = client.get("/openapi.json").json()
    for p in ["/api/opening/templates/manufactured-rolls",
              "/api/opening/batches/{batch_id}/manufactured-rolls/import",
              "/api/opening/batches/{batch_id}/roll-lines",
              "/api/opening/batches/{batch_id}/roll-preview",
              "/api/opening/batches/{batch_id}/roll-rejections"]:
        assert p in schema["paths"], p
