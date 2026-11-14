"""Printed-document registry (additive).

Adds ``printed_documents``: the company-scoped registry that reserves a stable,
printable number for a business document and records its print/reprint history.
Nothing existing is altered. Guarded and reversible; runs on the control database
and each company database (identical schema).

Revision ID: 0015_printed_documents
Revises: 0014_company_branding
Create Date: 2026-11-14
"""
import sqlalchemy as sa
from alembic import op

revision = "0015_printed_documents"
down_revision = "0014_company_branding"
branch_labels = None
depends_on = None


def _has_table(bind, name):
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "printed_documents"):
        return
    op.create_table(
        "printed_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("doc_type", sa.String(length=40), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("source_id", sa.String(length=80), nullable=False),
        sa.Column("fiscal_year", sa.String(length=9), nullable=False),
        sa.Column("number", sa.String(length=40), nullable=False),
        sa.Column("cancelled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reprint_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_printed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.UniqueConstraint("company_id", "doc_type", "source_type", "source_id",
                            name="uq_printed_doc_source"),
        sa.UniqueConstraint("company_id", "doc_type", "number", name="uq_printed_doc_number"),
    )
    op.create_index("ix_printed_documents_company_id", "printed_documents", ["company_id"])
    op.create_index("ix_printed_documents_number", "printed_documents", ["number"])
    op.create_index("ix_printed_documents_doc_type", "printed_documents", ["doc_type"])


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "printed_documents"):
        return
    op.drop_index("ix_printed_documents_doc_type", table_name="printed_documents")
    op.drop_index("ix_printed_documents_number", table_name="printed_documents")
    op.drop_index("ix_printed_documents_company_id", table_name="printed_documents")
    op.drop_table("printed_documents")
