"""Opening-balance batch foundation (Stage 6F-P1, additive).

Adds the single ``opening_batches`` table: the foundation batch master for
recording a company's opening position (yarn, manufactured rolls, trading
fabric, vendor custody, WIP, customer / supplier balances, GL balances, fixed
assets) under a controlled DRAFT -> SUBMITTED -> APPROVED -> CLOSED lifecycle
with maker-checker approval and an audit trail.

This migration is purely additive: it creates ONE new table and its indexes and
changes NO existing table. The batch master stores NO opening quantities, values
or line items, and there is NO POSTED state — P1 is the foundation only. Batch
numbers are unique per company; an optional idempotency key is unique per
company via a partial unique index. No data backfill.

Revision ID: 0029_opening_batches
Revises: 0028_yarn_sales
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from alembic import op

revision = "0029_opening_batches"
down_revision = "0028_yarn_sales"
branch_labels = None
depends_on = None


def _has_table(bind, name):
    return sa.inspect(bind).has_table(name)


def _audit_cols():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
    ]


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "opening_batches"):
        op.create_table(
            "opening_batches",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("factory_id", sa.Uuid(), nullable=True),
            sa.Column("batch_number", sa.String(length=40), nullable=False),
            sa.Column("batch_type", sa.String(length=24), nullable=False),
            sa.Column("effective_date", sa.Date(), nullable=False),
            sa.Column("description", sa.String(length=300), nullable=True),
            sa.Column("source_document", sa.String(length=120), nullable=True),
            sa.Column("status", sa.String(length=12), nullable=False, server_default="DRAFT"),
            sa.Column("idempotency_key", sa.String(length=80), nullable=True),
            sa.Column("submitted_by", sa.Uuid(), nullable=True),
            sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("approved_by", sa.Uuid(), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("closed_by", sa.Uuid(), nullable=True),
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancelled_by", sa.Uuid(), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancel_reason", sa.String(length=300), nullable=True),
            *_audit_cols(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["factory_id"], ["branches.id"]),
            sa.UniqueConstraint("company_id", "batch_number",
                                name="uq_opening_batch_company_number"),
        )
        op.create_index("ix_opening_batch_company_id", "opening_batches", ["company_id"])
        op.create_index("ix_opening_batch_factory_id", "opening_batches", ["factory_id"])
        op.create_index("ix_opening_batch_number", "opening_batches", ["batch_number"])
        op.create_index("ix_opening_batch_type", "opening_batches", ["batch_type"])
        op.create_index("ix_opening_batch_status", "opening_batches", ["status"])
        op.create_index("ix_opening_batch_effective_date", "opening_batches", ["effective_date"])
        # Idempotency key unique per company, but only where supplied (multiple
        # NULL keys are allowed). Partial unique index, same pattern as the
        # yarn-invoice partial index.
        op.create_index(
            "uq_opening_batch_company_idem", "opening_batches",
            ["company_id", "idempotency_key"], unique=True,
            sqlite_where=sa.text("idempotency_key IS NOT NULL"),
            postgresql_where=sa.text("idempotency_key IS NOT NULL"))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "opening_batches"):
        op.drop_table("opening_batches")   # cascades its indexes
