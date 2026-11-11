"""Factory-to-factory stock transfer with goods-in-transit (additive).

Adds ``factory_stock_transfers``. Additive and non-destructive.

Revision ID: 0007_factory_stock_transfers
Revises: 0006_intercompany_mappings
Create Date: 2026-11-11
"""
import sqlalchemy as sa
from alembic import op

revision = "0007_factory_stock_transfers"
down_revision = "0006_intercompany_mappings"
branch_labels = None
depends_on = None


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "factory_stock_transfers"):
        op.create_table(
            "factory_stock_transfers",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("number", sa.String(length=40), nullable=False),
            sa.Column("transfer_date", sa.Date(), nullable=False),
            sa.Column("from_branch_id", sa.Uuid(), nullable=False),
            sa.Column("to_branch_id", sa.Uuid(), nullable=False),
            sa.Column("from_warehouse_id", sa.Uuid(), nullable=False),
            sa.Column("to_warehouse_id", sa.Uuid(), nullable=False),
            sa.Column("item_type", sa.String(length=12), nullable=False),
            sa.Column("item_id", sa.Uuid(), nullable=False),
            sa.Column("requested_qty", sa.Numeric(18, 4), nullable=True),
            sa.Column("status", sa.String(length=12), nullable=False, server_default="REQUESTED"),
            sa.Column("dispatched_qty", sa.Numeric(18, 4), nullable=True),
            sa.Column("dispatched_cost", sa.Numeric(18, 4), nullable=True),
            sa.Column("unit_cost", sa.Numeric(18, 4), nullable=True),
            sa.Column("received_qty", sa.Numeric(18, 4), nullable=True),
            sa.Column("shortage_qty", sa.Numeric(18, 4), nullable=True),
            sa.Column("rejected_qty", sa.Numeric(18, 4), nullable=True),
            sa.Column("needs_reconciliation", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("requested_by", sa.Uuid(), nullable=True),
            sa.Column("authorized_by", sa.Uuid(), nullable=True),
            sa.Column("dispatched_by", sa.Uuid(), nullable=True),
            sa.Column("received_by", sa.Uuid(), nullable=True),
            sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("remarks", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["from_branch_id"], ["branches.id"]),
            sa.ForeignKeyConstraint(["to_branch_id"], ["branches.id"]),
            sa.ForeignKeyConstraint(["from_warehouse_id"], ["warehouses.id"]),
            sa.ForeignKeyConstraint(["to_warehouse_id"], ["warehouses.id"]),
        )
        op.create_index("ix_fst_company_id", "factory_stock_transfers", ["company_id"])
        op.create_index("ix_fst_status", "factory_stock_transfers", ["status"])
        op.create_index("ix_fst_item_id", "factory_stock_transfers", ["item_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "factory_stock_transfers"):
        op.drop_table("factory_stock_transfers")
