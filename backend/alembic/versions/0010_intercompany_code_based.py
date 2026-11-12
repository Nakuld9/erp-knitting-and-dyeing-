"""Make inter-company mapping account-ids optional; codes are authoritative.

Under database-per-company the chart of accounts lives in each company's own
database, so the central mapping cannot hold a foreign key to it. This makes the
account-id columns nullable and drops their FK on PostgreSQL (where the COA is
elsewhere). On SQLite (single-database installs / tests) the FK is still
satisfiable and is left in place; the columns are made nullable.

Revision ID: 0010_intercompany_code_based
Revises: 0009_intercompany_saga
Create Date: 2026-11-12
"""
import sqlalchemy as sa
from alembic import op

revision = "0010_intercompany_code_based"
down_revision = "0009_intercompany_saga"
branch_labels = None
depends_on = None

TABLE = "intercompany_account_mappings"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.alter_column(TABLE, "due_from_account_id", existing_type=sa.Uuid(), nullable=True)
        op.alter_column(TABLE, "due_to_account_id", existing_type=sa.Uuid(), nullable=True)
        op.execute(f'ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {TABLE}_due_from_account_id_fkey')
        op.execute(f'ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {TABLE}_due_to_account_id_fkey')
    else:
        with op.batch_alter_table(TABLE) as batch:
            batch.alter_column("due_from_account_id", existing_type=sa.Uuid(), nullable=True)
            batch.alter_column("due_to_account_id", existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    bind = op.get_bind()
    # best-effort reverse (dev rollback; assumes no NULL ids present)
    if bind.dialect.name == "postgresql":
        op.alter_column(TABLE, "due_from_account_id", existing_type=sa.Uuid(), nullable=False)
        op.alter_column(TABLE, "due_to_account_id", existing_type=sa.Uuid(), nullable=False)
    else:
        with op.batch_alter_table(TABLE) as batch:
            batch.alter_column("due_from_account_id", existing_type=sa.Uuid(), nullable=False)
            batch.alter_column("due_to_account_id", existing_type=sa.Uuid(), nullable=False)
