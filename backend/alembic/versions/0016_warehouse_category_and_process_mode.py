"""Hybrid foundation: warehouse category + process-mode config (additive).

Adds, additively and reversibly:
* ``warehouses.category`` — a nullable inventory-stage label (YARN_STORE / GREIGE /
  SEMI_FINISHED / FINISHED_GOODS / TRADING). The legacy ``warehouses.kind`` string is
  left untouched; existing rows are NOT backfilled.
* ``process_modes`` — per-company hybrid-manufacturing configuration (3 processes x
  internal/outsourced toggles). One row per company.

Nothing existing is altered or removed. Guarded so it is safe to run against an
already-migrated database and against the control database and each company
database.

Revision ID: 0016_warehouse_category_and_process_mode
Revises: 0015_printed_documents
Create Date: 2026-11-14
"""
import sqlalchemy as sa
from alembic import op

revision = "0016_hybrid_foundation"
down_revision = "0015_printed_documents"
branch_labels = None
depends_on = None


def _has_table(bind, name):
    return sa.inspect(bind).has_table(name)


def _has_column(bind, table, col):
    insp = sa.inspect(bind)
    return insp.has_table(table) and col in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column(bind, "warehouses", "category"):
        op.add_column("warehouses", sa.Column("category", sa.String(length=20), nullable=True))

    if not _has_table(bind, "process_modes"):
        op.create_table(
            "process_modes",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("knit_internal", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("knit_outsourced", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("dye_internal", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("dye_outsourced", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("finish_internal", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("finish_outsourced", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.UniqueConstraint("company_id", name="uq_process_mode_company"),
        )
        op.create_index("ix_process_modes_company_id", "process_modes", ["company_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "process_modes"):
        op.drop_index("ix_process_modes_company_id", table_name="process_modes")
        op.drop_table("process_modes")
    if _has_column(bind, "warehouses", "category"):
        op.drop_column("warehouses", "category")
