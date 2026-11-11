"""Legacy/Unassigned factory flag on branches (additive schema only).

Adds ``branches.is_legacy_unassigned`` (default false). This is schema only — it
creates no data and assigns nothing. The actual historical backfill is a separate,
deliberate step (``scripts/backfill_legacy_factory.py``), never run silently by a
migration.

Revision ID: 0004_branch_legacy_flag
Revises: 0003_factory_ownership_columns
Create Date: 2026-11-11
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_branch_legacy_flag"
down_revision = "0003_factory_ownership_columns"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "branches", "is_legacy_unassigned"):
        with op.batch_alter_table("branches") as batch:
            batch.add_column(
                sa.Column("is_legacy_unassigned", sa.Boolean(), nullable=False,
                          server_default=sa.false())
            )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "branches", "is_legacy_unassigned"):
        with op.batch_alter_table("branches") as batch:
            batch.drop_column("is_legacy_unassigned")
