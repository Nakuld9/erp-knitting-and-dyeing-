"""Company branding: per-company logo (BYTEA) + letterhead contact (additive).

Adds a single additive, company-scoped table ``company_branding`` holding one row
per company: the logo as raw bytes (BYTEA on PostgreSQL, BLOB on SQLite) plus its
filename / MIME type / size / upload date, and the extra letterhead contact fields
(cin, phone, email, website). Nothing existing is altered or removed; identity
fields already on ``companies`` (legal_name, gstin, state_code, address) are reused
and not duplicated.

Guarded so it is safe to run against an already-migrated database and against both
the control database and each company database (identical schema).

Revision ID: 0014_company_branding
Revises: 0013_grey_cost_and_dyeing
Create Date: 2026-11-14
"""
import sqlalchemy as sa
from alembic import op

revision = "0014_company_branding"
down_revision = "0013_grey_cost_and_dyeing"
branch_labels = None
depends_on = None


def _has_table(bind, name):
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "company_branding"):
        return
    op.create_table(
        "company_branding",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("cin", sa.String(length=30), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("email", sa.String(length=120), nullable=True),
        sa.Column("website", sa.String(length=120), nullable=True),
        sa.Column("logo_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("logo_filename", sa.String(length=255), nullable=True),
        sa.Column("logo_mime_type", sa.String(length=40), nullable=True),
        sa.Column("logo_size_bytes", sa.Integer(), nullable=True),
        sa.Column("logo_uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("company_id", name="uq_company_branding_company"),
    )
    op.create_index("ix_company_branding_company_id", "company_branding", ["company_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "company_branding"):
        return
    op.drop_index("ix_company_branding_company_id", table_name="company_branding")
    op.drop_table("company_branding")
