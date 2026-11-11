"""Per-company database registry (control plane, additive).

Adds ``company_databases`` to the control/default database. Additive and
non-destructive; no company is routed anywhere until a row is added here.

Revision ID: 0008_company_databases
Revises: 0007_factory_stock_transfers
Create Date: 2026-11-11
"""
import sqlalchemy as sa
from alembic import op

revision = "0008_company_databases"
down_revision = "0007_factory_stock_transfers"
branch_labels = None
depends_on = None


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "company_databases"):
        op.create_table(
            "company_databases",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("dsn", sa.String(length=500), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("provisioned_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("company_id", name="uq_company_databases_company"),
        )
        op.create_index("ix_company_databases_company_id", "company_databases", ["company_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "company_databases"):
        op.drop_table("company_databases")
