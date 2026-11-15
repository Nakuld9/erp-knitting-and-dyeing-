"""Stage 4B-1 — trading item-master API (CRUD + permission enforcement)."""
from __future__ import annotations

from sqlalchemy import select

from app.core.security import hash_password
from app.models.core import Permission, Role, User


def _reader_token(client, db, demo):
    """A user with master.read but NOT master.write."""
    cid = demo["company_id"]
    perm = db.execute(select(Permission).where(Permission.code == "master.read")).scalar_one()
    role = Role(code="TRADEVIEW", name="Trade Viewer"); role.permissions.append(perm)
    u = User(username="tradeview", email="tv@demo.local", full_name="Trade Viewer",
             hashed_password=hash_password("viewpw123"), is_active=True, is_superuser=False, company_id=cid)
    u.roles.append(role); db.add(u); db.commit()
    return client.post("/api/auth/login", data={"username": "tradeview", "password": "viewpw123"}).json()["access_token"]


def test_api_crud_lifecycle(client, auth_headers, db, demo):
    # create
    r = client.post("/api/trading/items", headers=auth_headers,
                    json={"code": "API-1", "name": "Imported Interlock", "gsm": "200", "colour": "Red"})
    assert r.status_code == 201, r.text
    item_id = r.json()["id"]
    assert r.json()["code"] == "API-1" and r.json()["is_active"] is True

    # duplicate code -> 422
    dup = client.post("/api/trading/items", headers=auth_headers, json={"code": "API-1", "name": "x"})
    assert dup.status_code == 422

    # get
    g = client.get(f"/api/trading/items/{item_id}", headers=auth_headers)
    assert g.status_code == 200 and g.json()["colour"] == "Red"

    # list + search
    assert any(i["code"] == "API-1" for i in client.get("/api/trading/items", headers=auth_headers).json())
    assert len(client.get("/api/trading/items?search=Interlock", headers=auth_headers).json()) == 1

    # update
    up = client.patch(f"/api/trading/items/{item_id}", headers=auth_headers, json={"colour": "Blue"})
    assert up.status_code == 200 and up.json()["colour"] == "Blue"

    # deactivate
    da = client.post(f"/api/trading/items/{item_id}/active", headers=auth_headers, json={"is_active": False})
    assert da.status_code == 200 and da.json()["is_active"] is False
    assert len(client.get("/api/trading/items?active_only=true", headers=auth_headers).json()) == 0


def test_api_patch_cannot_change_activation(client, auth_headers, db, demo):
    r = client.post("/api/trading/items", headers=auth_headers, json={"code": "API-ACT", "name": "Act"})
    item_id = r.json()["id"]
    assert r.json()["is_active"] is True
    # PATCH with an is_active field must NOT deactivate (the schema ignores it and
    # the service excludes it from editable fields).
    up = client.patch(f"/api/trading/items/{item_id}", headers=auth_headers,
                      json={"colour": "Green", "is_active": False})
    assert up.status_code == 200
    assert up.json()["is_active"] is True and up.json()["colour"] == "Green"
    # only the dedicated endpoint deactivates
    da = client.post(f"/api/trading/items/{item_id}/active", headers=auth_headers, json={"is_active": False})
    assert da.status_code == 200 and da.json()["is_active"] is False


def test_api_get_missing_404(client, auth_headers, db, demo):
    import uuid
    r = client.get(f"/api/trading/items/{uuid.uuid4()}", headers=auth_headers)
    assert r.status_code == 404


def test_api_write_requires_permission(client, auth_headers, db, demo):
    tok = _reader_token(client, db, demo)
    h = {"Authorization": f"Bearer {tok}"}
    # reader can list
    assert client.get("/api/trading/items", headers=h).status_code == 200
    # but cannot create
    r = client.post("/api/trading/items", headers=h, json={"code": "NO", "name": "x"})
    assert r.status_code == 403


def test_api_create_requires_auth(client, db, demo):
    r = client.post("/api/trading/items", json={"code": "NOAUTH", "name": "x"})
    assert r.status_code in (401, 403)
