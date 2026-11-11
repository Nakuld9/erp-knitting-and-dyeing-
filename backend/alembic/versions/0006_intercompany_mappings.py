"""Inter-company account mappings (additive, configuration only).

Adds ``intercompany_account_mappings`` — configurable, accountant-approved
due-from/due-to control accounts per counterparty. Additive; no posting, no data.

Revision ID: 0006_intercompany_mappings
Revises: 0005_consolidation_eliminations
Create Date: 2026-11-11
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_intercompany_mappings"
down_revision = "0005_consolidation_eliminations"
branch_labels = None
depends_on = None


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "intercompany_account_mappings"):
        op.create_table(
            "intercompany_account_mappings",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("counterparty_company_id", sa.Uuid(), nullable=False),
            sa.Column("due_from_account_id", sa.Uuid(), nullable=False),
            sa.Column("due_to_account_id", sa.Uuid(), nullable=False),
            sa.Column("is_approved", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("approved_by", sa.Uuid(), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["counterparty_company_id"], ["companies.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["due_from_account_id"], ["chart_of_accounts.id"]),
            sa.ForeignKeyConstraint(["due_to_account_id"], ["chart_of_accounts.id"]),
            sa.UniqueConstraint("company_id", "counterparty_company_id",
                                name="uq_ica_company_counterparty"),
        )
        op.create_index("ix_ica_company_id", "intercompany_account_mappings", ["company_id"])
        op.create_index("ix_ica_counterparty_company_id",
                        "intercompany_account_mappings", ["counterparty_company_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "intercompany_account_mappings"):
        op.drop_table("intercompany_account_mappings")
