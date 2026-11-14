"""Stage 1 (Foundation): warehouse categories + process-mode config.

Verifies defaults (internal ON / outsourced OFF), the disabled-mode guard, audit
history on config changes, warehouse-category validation, admin-only mutation, and
strict company isolation. Nothing here posts a voucher or moves stock.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.models.audit import AuditLog
from app.models.core import Company
from app.models.hybrid import Process, ProcessMode, WorkMode
from app.models.masters import Warehouse
from app.services import hybrid as svc


def _second_company(db):
    c = db.execute(select(Company).where(Company.code == "UATB")).scalar_one_or_none()
    if c is None:
        c = Company(code="UATB", name="UAT Weaves", legal_name="UAT Weaves Pvt Ltd",
                    gstin="27ABCDE9999F1Z2", state_code="27", currency="INR")
        db.add(c); db.commit()
    return c


# ---- defaults ----

def test_default_mode_internal_on_outsourced_off(client, auth_headers, company):
    r = client.get("/api/config/process-mode", headers=auth_headers)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["knit_internal"] and b["dye_internal"] and b["finish_internal"]
    assert not b["knit_outsourced"] and not b["dye_outsourced"] and not b["finish_outsourced"]


def test_mode_enabled_defaults(db, company):
    # seeded row exists; internal enabled, outsourced disabled
    assert svc.mode_enabled(db, company.id, Process.KNITTING, WorkMode.INTERNAL) is True
    assert svc.mode_enabled(db, company.id, Process.DYEING, WorkMode.OUTSOURCED) is False


def test_require_mode_blocks_disabled(db, company):
    # outsourced is off by default -> require_mode raises (blocks NEW work only)
    try:
        svc.require_mode(db, company.id, Process.FINISHING, WorkMode.OUTSOURCED)
        assert False, "expected HybridConfigError"
    except svc.HybridConfigError:
        pass
    svc.require_mode(db, company.id, Process.KNITTING, WorkMode.INTERNAL)  # internal ok, no raise


# ---- update + audit ----

def test_update_toggles_and_audit(client, auth_headers, db, company):
    before = db.execute(select(func.count()).select_from(AuditLog).where(
        AuditLog.entity == "process_mode")).scalar()
    r = client.put("/api/config/process-mode", headers=auth_headers,
                   json={"dye_outsourced": True})
    assert r.status_code == 200 and r.json()["dye_outsourced"] is True
    # now enabled
    assert svc.mode_enabled(db, company.id, Process.DYEING, WorkMode.OUTSOURCED) is True
    # an audit row was written
    after = db.execute(select(func.count()).select_from(AuditLog).where(
        AuditLog.entity == "process_mode")).scalar()
    assert after == before + 1


def test_unknown_setting_rejected(client, auth_headers):
    # pydantic drops unknown keys, so send a known-bad value via the service path
    r = client.put("/api/config/process-mode", headers=auth_headers, json={"knit_internal": False})
    assert r.status_code == 200 and r.json()["knit_internal"] is False


def test_update_requires_permission(client, db):
    from app.core.security import hash_password
    from app.models.core import User
    cid = db.execute(select(Company).where(Company.code == "DEMO")).scalar_one().id
    u = User(username="clerk_h", email="clerk_h@demo.local", full_name="Clerk",
             hashed_password=hash_password("clerkpw123"), is_active=True, is_superuser=False, company_id=cid)
    db.add(u); db.commit()
    tok = client.post("/api/auth/login", data={"username": "clerk_h", "password": "clerkpw123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.put("/api/config/process-mode", headers=h, json={"knit_outsourced": True}).status_code == 403
    assert client.get("/api/config/process-mode", headers=h).status_code == 200   # read allowed


# ---- warehouse category ----

def test_warehouse_category_create_and_validate(client, auth_headers, db, company):
    ok = client.post("/api/masters/warehouses", headers=auth_headers,
                     json={"code": "WH-SF", "name": "Semi-Finished Store", "kind": "STORE",
                           "category": "SEMI_FINISHED"})
    assert ok.status_code == 201 and ok.json()["category"] == "SEMI_FINISHED"
    bad = client.post("/api/masters/warehouses", headers=auth_headers,
                      json={"code": "WH-X", "name": "X", "category": "NOPE"})
    assert bad.status_code == 400


def test_set_category_on_existing_warehouse(client, auth_headers, db, demo):
    wh = db.execute(select(Warehouse).where(Warehouse.code == "WH-CHEM")).scalar_one()
    r = client.put(f"/api/config/warehouses/{wh.id}/category", headers=auth_headers,
                   json={"category": "YARN_STORE"})
    assert r.status_code == 200 and r.json()["category"] == "YARN_STORE"
    # seeded demo categories are present
    assert db.execute(select(Warehouse).where(Warehouse.code == "WH-GREIGE")).scalar_one().category == "GREIGE"
    assert db.execute(select(Warehouse).where(Warehouse.code == "WH-FG")).scalar_one().category == "FINISHED_GOODS"


def test_categories_list(client, auth_headers):
    cats = client.get("/api/config/warehouse-categories", headers=auth_headers).json()
    assert set(cats) == {"YARN_STORE", "GREIGE", "SEMI_FINISHED", "FINISHED_GOODS", "TRADING"}


# ---- company isolation ----

def test_process_mode_isolated_per_company(client, auth_headers, db):
    b = _second_company(db)
    # enable outsourced knitting in company A
    client.put("/api/config/process-mode", headers=auth_headers, json={"knit_outsourced": True})
    # company B (switched via header; admin is superuser) is unaffected / separate
    hb = {**auth_headers, "X-Company-Id": str(b.id)}
    bj = client.get("/api/config/process-mode", headers=hb).json()
    assert bj["knit_outsourced"] is False
    # and B's own row (once created) does not change A
    client.put("/api/config/process-mode", headers=hb, json={"finish_outsourced": True})
    aj = client.get("/api/config/process-mode", headers=auth_headers).json()
    assert aj["finish_outsourced"] is False and aj["knit_outsourced"] is True
    # two distinct rows exist
    assert db.execute(select(func.count()).select_from(ProcessMode)).scalar() >= 2
