"""Company branding: per-legal-company logo and letterhead identity.

This table is **company-scoped** — it lives in each company's operational
database (the same schema the per-company Alembic migrations create everywhere),
so a company's logo and letterhead contact details travel with that company's own
database and are included in its backup/restore. Nothing here lives in a shared
filesystem; the logo is stored as raw bytes in a BYTEA/BLOB column.

One row per company (``company_id`` unique). Identity fields that already exist on
``core.Company`` (legal_name, gstin, state_code, address) are NOT duplicated here;
this table adds the logo and the extra letterhead contact fields.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, Uuid, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AuditMixin, Base


class CompanyBranding(AuditMixin, Base):
    """Per-company logo (BYTEA) + letterhead contact details.

    ``logo_bytes`` is the raw image (PNG/JPEG/WebP), stored in the company's own
    database. Retrieval and upload are always scoped to the authenticated active
    company, so one company can never read another's logo.
    """

    __tablename__ = "company_branding"
    __table_args__ = (
        UniqueConstraint("company_id", name="uq_company_branding_company"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), unique=True, index=True)

    # Extra letterhead contact fields (legal_name/gstin/state_code/address live on
    # core.Company and are reused, not duplicated).
    cin: Mapped[str | None] = mapped_column(String(30), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    email: Mapped[str | None] = mapped_column(String(120), nullable=True)
    website: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Logo stored as raw bytes (BYTEA on PostgreSQL, BLOB on SQLite). Never Base64
    # in a text column; never a filesystem path.
    logo_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    logo_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    logo_mime_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    logo_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    logo_uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
