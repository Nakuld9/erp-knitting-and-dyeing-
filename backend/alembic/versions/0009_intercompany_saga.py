"""Inter-company saga/outbox + mapping account codes (additive).

Adds account-CODE columns to intercompany_account_mappings (for cross-database
posting) and the intercompany_txns saga/outbox table (central plane). Additive.

Revision ID: 0009_intercompany_saga
Revises: 0008_company_databases
Create Date: 2026-11-12
"""
import sqlalchemy as sa
from alembic import op

revision = "0009_intercompany_saga"
down_revision = "0008_company_databases"
branch_labels = None
depends_on = None


def _has_table(bind, name):
    return sa.inspect(bind).has_table(name)


def _has_column(bind, table, col):
    insp = sa.inspect(bind)
    return insp.has_table(table) and any(c["name"] == col for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    for col in ("due_from_account_code", "due_to_account_code"):
        if not _has_column(bind, "intercompany_account_mappings", col):
            op.add_column("intercompany_account_mappings",
                          sa.Column(col, sa.String(length=20), nullable=True))
    if not _has_table(bind, "intercompany_txns"):
        op.create_table(
            "intercompany_txns",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("idempotency_key", sa.String(length=80), nullable=False),
            sa.Column("seller_company_id", sa.Uuid(), nullable=False),
            sa.Column("buyer_company_id", sa.Uuid(), nullable=False),
            sa.Column("taxable", sa.Numeric(18, 4), nullable=True),
            sa.Column("tax_code_id", sa.Uuid(), nullable=True),
            sa.Column("voucher_date", sa.String(length=10), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
            sa.Column("seller_voucher_number", sa.String(length=40), nullable=True),
            sa.Column("buyer_voucher_number", sa.String(length=40), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_error", sa.String(length=500), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["seller_company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["buyer_company_id"], ["companies.id"]),
            sa.UniqueConstraint("idempotency_key", name="uq_intercompany_txn_idem"),
        )
        op.create_index("ix_intercompany_txns_status", "intercompany_txns", ["status"])
        op.create_index("ix_intercompany_txns_idempotency_key", "intercompany_txns", ["idempotency_key"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "intercompany_txns"):
        op.drop_table("intercompany_txns")
    for col in ("due_to_account_code", "due_from_account_code"):
        if _has_column(bind, "intercompany_account_mappings", col):
            with op.batch_alter_table("intercompany_account_mappings") as batch:
                batch.drop_column(col)
