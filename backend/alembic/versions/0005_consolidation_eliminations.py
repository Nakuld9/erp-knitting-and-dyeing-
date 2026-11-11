"""Consolidation eliminations table (additive, report-only).

Adds ``consolidation_eliminations`` — report-only group adjustments that are
never posted to any company ledger. Additive and non-destructive.

Revision ID: 0005_consolidation_eliminations
Revises: 0004_branch_legacy_flag
Create Date: 2026-11-11
"""
import sqlalchemy as sa
from alembic import op

revision = "0005_consolidation_eliminations"
down_revision = "0004_branch_legacy_flag"
branch_labels = None
depends_on = None


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "consolidation_eliminations"):
        op.create_table(
            "consolidation_eliminations",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("organization_id", sa.Uuid(), nullable=False),
            sa.Column("account_code", sa.String(length=40), nullable=False),
            sa.Column("description", sa.String(length=300), nullable=True),
            sa.Column("debit", sa.Numeric(18, 4), nullable=True),
            sa.Column("credit", sa.Numeric(18, 4), nullable=True),
            sa.Column("counterparty_ref", sa.String(length=80), nullable=True),
            sa.Column("is_approved", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("approved_by", sa.Uuid(), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_consolidation_eliminations_organization_id",
                        "consolidation_eliminations", ["organization_id"])
        op.create_index("ix_consolidation_eliminations_account_code",
                        "consolidation_eliminations", ["account_code"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "consolidation_eliminations"):
        op.drop_table("consolidation_eliminations")
