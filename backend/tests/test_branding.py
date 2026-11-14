"""Company branding API: upload/validate/retrieve, contact upsert, company switch
and strict company isolation (one company can never read another's logo)."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.core import Company

# Minimal valid image byte sequences (>= 12 bytes; correct magic bytes).
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
WEBP = b"RIFF\x24\x00\x00\x00WEBP" + b"\x00" * 16
NOT_IMAGE = b"%PDF-1.4 not an image" + b"\x00" * 16


def _second_company(db):
    c = db.execute(select(Company).where(Company.code == "UATB")).scalar_one_or_none()
    if c is None:
        c = Company(code="UATB", name="UAT Weaves", legal_name="UAT Weaves Private Limited",
                    gstin="27ABCDE9999F1Z2", state_code="27", currency="INR")
        db.add(c)
        db.commit()
    return c


# ---- retrieval / default state ----

def test_branding_defaults_to_company_identity(client, auth_headers):
    r = client.get("/api/me/branding", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    # legal name comes from core.Company (seeded DEMO), not a hardcoded label
    assert body["legal_name"] == "Demo Textile Mills Private Limited"
    assert body["gstin"] == "03ABCDE1234F1Z5"
    assert body["has_logo"] is False
    assert body["logo_data_url"] is None


# ---- upload + validation ----

def test_upload_logo_then_retrieve(client, auth_headers):
    up = client.post("/api/me/branding/logo", headers=auth_headers,
                     files={"file": ("logo.png", PNG, "image/png")})
    assert up.status_code == 201, up.text
    assert up.json()["has_logo"] is True and up.json()["logo_mime_type"] == "image/png"

    # JSON branding now carries a data URL for the sidebar <img>
    b = client.get("/api/me/branding", headers=auth_headers).json()
    assert b["has_logo"] is True
    assert b["logo_data_url"].startswith("data:image/png;base64,")

    # raw bytes endpoint streams the exact bytes with the right content-type
    raw = client.get("/api/me/branding/logo", headers=auth_headers)
    assert raw.status_code == 200
    assert raw.headers["content-type"].startswith("image/png")
    assert raw.content == PNG


def test_upload_accepts_jpeg_and_webp(client, auth_headers):
    assert client.post("/api/me/branding/logo", headers=auth_headers,
                       files={"file": ("l.jpg", JPEG, "image/jpeg")}).status_code == 201
    assert client.post("/api/me/branding/logo", headers=auth_headers,
                       files={"file": ("l.webp", WEBP, "image/webp")}).status_code == 201


def test_reject_non_image(client, auth_headers):
    r = client.post("/api/me/branding/logo", headers=auth_headers,
                    files={"file": ("x.png", NOT_IMAGE, "image/png")})
    assert r.status_code == 400
    assert "not a valid" in r.text.lower()


def test_reject_mismatched_content_type(client, auth_headers):
    # PNG bytes declared as webp -> rejected (magic-byte mismatch)
    r = client.post("/api/me/branding/logo", headers=auth_headers,
                    files={"file": ("x.webp", PNG, "image/webp")})
    assert r.status_code == 400
    assert "does not match" in r.text.lower()


def test_reject_oversize(client, auth_headers):
    big = PNG + b"\x00" * (512 * 1024 + 1)
    r = client.post("/api/me/branding/logo", headers=auth_headers,
                    files={"file": ("big.png", big, "image/png")})
    assert r.status_code == 400
    assert "too large" in r.text.lower()


def test_no_logo_returns_404(client, auth_headers):
    assert client.get("/api/me/branding/logo", headers=auth_headers).status_code == 404


# ---- contact upsert + delete ----

def test_update_contact_fields(client, auth_headers):
    r = client.put("/api/me/branding", headers=auth_headers,
                   json={"cin": "U17110PB1990PLC000000", "phone": "+91 161 000 0000",
                         "email": "accounts@demomill.example", "website": "https://demomill.example"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cin"] == "U17110PB1990PLC000000"
    assert body["email"] == "accounts@demomill.example"


def test_delete_logo_keeps_contact(client, auth_headers):
    client.put("/api/me/branding", headers=auth_headers, json={"phone": "123"})
    client.post("/api/me/branding/logo", headers=auth_headers,
                files={"file": ("l.png", PNG, "image/png")})
    d = client.delete("/api/me/branding/logo", headers=auth_headers)
    assert d.status_code == 200 and d.json()["has_logo"] is False
    b = client.get("/api/me/branding", headers=auth_headers).json()
    assert b["has_logo"] is False and b["phone"] == "123"  # contact preserved


# ---- company switch + isolation ----

def test_company_switch_shows_correct_identity(client, db, auth_headers):
    b = _second_company(db)
    # default (company A / DEMO)
    a_ident = client.get("/api/me/branding", headers=auth_headers).json()
    assert a_ident["legal_name"] == "Demo Textile Mills Private Limited"
    # switch to company B via header (admin is superuser -> allowed)
    hb = {**auth_headers, "X-Company-Id": str(b.id)}
    b_ident = client.get("/api/me/branding", headers=hb).json()
    assert b_ident["legal_name"] == "UAT Weaves Private Limited"
    assert b_ident["gstin"] == "27ABCDE9999F1Z2"


def test_logo_is_isolated_per_company(client, db, auth_headers):
    b = _second_company(db)
    # upload a logo for company A only
    client.post("/api/me/branding/logo", headers=auth_headers,
                files={"file": ("a.png", PNG, "image/png")})
    # company B must NOT see A's logo
    hb = {**auth_headers, "X-Company-Id": str(b.id)}
    bj = client.get("/api/me/branding", headers=hb).json()
    assert bj["has_logo"] is False and bj["logo_data_url"] is None
    assert client.get("/api/me/branding/logo", headers=hb).status_code == 404
    # company A still has its own logo
    aj = client.get("/api/me/branding", headers=auth_headers).json()
    assert aj["has_logo"] is True


def test_upload_requires_admin_permission(client, db):
    # a non-admin user (no admin.manage) cannot upload
    from app.core.security import hash_password
    from app.models.core import User
    u = User(username="clerk1", email="clerk1@demo.local", full_name="Clerk",
             hashed_password=hash_password("clerkpw123"), is_active=True, is_superuser=False,
             company_id=db.execute(select(Company).where(Company.code == "DEMO")).scalar_one().id)
    db.add(u); db.commit()
    tok = client.post("/api/auth/login", data={"username": "clerk1", "password": "clerkpw123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    r = client.post("/api/me/branding/logo", headers=h,
                    files={"file": ("l.png", PNG, "image/png")})
    assert r.status_code == 403
    # but a plain read is allowed for a member
    assert client.get("/api/me/branding", headers=h).status_code == 200
