"""Company branding service: validated logo storage + letterhead contact.

All reads/writes are scoped to a single ``company_id`` (the authenticated active
company), so one company can never read or overwrite another's branding. The logo
is stored as raw bytes; callers pass the already-read file bytes.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.branding import CompanyBranding

# Accepted logo formats and size cap.
ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp"}
MAX_LOGO_BYTES = 512 * 1024  # 512 KB


class BrandingError(ValueError):
    """Invalid branding input (bad image type, too large, empty)."""


def _sniff_mime(data: bytes) -> str | None:
    """Best-effort magic-byte sniff so a wrong/forged content-type is caught.

    Returns the detected image MIME, or None if it is not a recognised image.
    """
    if len(data) < 12:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def get_branding(db: Session, company_id: uuid.UUID) -> CompanyBranding | None:
    return db.execute(
        select(CompanyBranding).where(CompanyBranding.company_id == company_id)
    ).scalar_one_or_none()


def _get_or_create(db: Session, company_id: uuid.UUID,
                   created_by: uuid.UUID | None = None) -> CompanyBranding:
    row = get_branding(db, company_id)
    if row is None:
        row = CompanyBranding(company_id=company_id, created_by=created_by)
        db.add(row)
        db.flush()
    return row


def update_contact(db: Session, company_id: uuid.UUID, *,
                   cin: str | None = None, phone: str | None = None,
                   email: str | None = None, website: str | None = None,
                   updated_by: uuid.UUID | None = None) -> CompanyBranding:
    """Upsert the letterhead contact fields (does not touch the logo)."""
    row = _get_or_create(db, company_id, created_by=updated_by)
    if cin is not None:
        row.cin = cin.strip() or None
    if phone is not None:
        row.phone = phone.strip() or None
    if email is not None:
        row.email = email.strip() or None
    if website is not None:
        row.website = website.strip() or None
    row.updated_by = updated_by
    db.flush()
    return row


def set_logo(db: Session, company_id: uuid.UUID, *, data: bytes, filename: str | None,
             content_type: str | None, updated_by: uuid.UUID | None = None) -> CompanyBranding:
    """Validate and store a logo (PNG/JPEG/WebP, <= 512 KB) for one company.

    The declared content-type must be allowed AND match the file's magic bytes,
    so a mislabelled or non-image upload is rejected.
    """
    if not data:
        raise BrandingError("Empty file")
    if len(data) > MAX_LOGO_BYTES:
        raise BrandingError(
            f"Logo too large ({len(data)} bytes); maximum is {MAX_LOGO_BYTES} bytes (512 KB)")
    sniffed = _sniff_mime(data)
    if sniffed is None:
        raise BrandingError("File is not a valid PNG, JPEG or WebP image")
    declared = (content_type or "").split(";")[0].strip().lower()
    # JPEG is served as image/jpeg; accept image/jpg as a synonym.
    if declared == "image/jpg":
        declared = "image/jpeg"
    if declared and declared not in ALLOWED_MIME:
        raise BrandingError(f"Unsupported image type '{declared}' (allowed: PNG, JPEG, WebP)")
    if declared and declared != sniffed:
        raise BrandingError(
            f"Declared type '{declared}' does not match the file contents ('{sniffed}')")

    row = _get_or_create(db, company_id, created_by=updated_by)
    row.logo_bytes = data
    row.logo_filename = (filename or "logo")[:255]
    row.logo_mime_type = sniffed
    row.logo_size_bytes = len(data)
    row.logo_uploaded_at = datetime.now(timezone.utc)
    row.updated_by = updated_by
    db.flush()
    return row


def clear_logo(db: Session, company_id: uuid.UUID,
               updated_by: uuid.UUID | None = None) -> CompanyBranding | None:
    """Remove the logo bytes/metadata, keeping the contact fields."""
    row = get_branding(db, company_id)
    if row is None:
        return None
    row.logo_bytes = None
    row.logo_filename = None
    row.logo_mime_type = None
    row.logo_size_bytes = None
    row.logo_uploaded_at = None
    row.updated_by = updated_by
    db.flush()
    return row
