"""Factory ownership columns on factory-owned operational tables (additive).

Adds a NULLABLE ``factory_id`` (FK -> branches.id) to the factory-owned tables.
This is step 1 of the staged ownership rollout: columns only, no backfill, no
NOT NULL, no enforcement. Existing rows get NULL and behave exactly as before.
Backfilling these from an owner-confirmed mapping is a separate, approval-gated
migration (see docs/MULTI_COMPANY_ARCHITECTURE.md section 6).

Revision ID: 0003_factory_ownership_columns
Revises: 0002_multicompany_foundation
Create Date: 2026-11-11
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_factory_ownership_columns"
down_revision = "0002_multicompany_foundation"
branch_labels = None
depends_on = None

# table -> index name
TABLES = {
    "machines": "ix_machines_factory_id",
    "knitting_production": "ix_knitting_production_factory_id",
    "fabric_rolls": "ix_fabric_rolls_factory_id",
    "dye_batches": "ix_dye_batches_factory_id",
    "finishing_transactions": "ix_finishing_transactions_factory_id",
    "quality_inspections": "ix_quality_inspections_factory_id",
    "dispensing_transactions": "ix_dispensing_transactions_factory_id",
}


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    for table, index_name in TABLES.items():
        if _has_column(bind, table, "factory_id"):
            continue
        # Batch mode keeps this portable (native ALTER on PostgreSQL, copy-and-move
        # on SQLite). FK declared inline so SQLite carries it through a recreate.
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column(
                    "factory_id",
                    sa.Uuid(),
                    sa.ForeignKey("branches.id", name=f"fk_{table}_factory_id"),
                    nullable=True,
                )
            )
        op.create_index(index_name, table, ["factory_id"])


def downgrade() -> None:
    bind = op.get_bind()
    for table, index_name in TABLES.items():
        if not _has_column(bind, table, "factory_id"):
            continue
        try:
            op.drop_index(index_name, table_name=table)
        except Exception:
            pass  # batch recreate drops it on SQLite
        with op.batch_alter_table(table) as batch:
            batch.drop_column("factory_id")
