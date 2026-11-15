"""Stage 3C: job-work inventory custody (additive).

Adds, additively and reversibly:
* ``suppliers.is_job_worker`` — authorise a vendor as a job worker (no separate
  job-worker master).
* ``warehouses.vendor_id`` — marks a warehouse as a vendor's custody location.
* ``job_work_issues`` — material issued into a job worker's custody (the internal
  issue challan + vendor-wise outstanding position).

Nothing existing is altered or removed; no historical data is backfilled. Guarded
so it is safe on the control DB and each company DB, on SQLite and PostgreSQL.

Revision ID: 0018_jobwork_custody
Revises: 0017_roll_genealogy
Create Date: 2026-11-15
"""
import sqlalchemy as sa
from alembic import op

from app.core.types import Quantity

revision = "0018_jobwork_custody"
down_revision = "0017_roll_genealogy"
branch_labels = None
depends_on = None


def _insp(bind):
    return sa.inspect(bind)


def _has_table(bind, name):
    return _insp(bind).has_table(name)


def _has_column(bind, table, col):
    insp = _insp(bind)
    return insp.has_table(table) and col in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column(bind, "suppliers", "is_job_worker"):
        op.add_column("suppliers", sa.Column("is_job_worker", sa.Boolean(), nullable=False,
                                             server_default=sa.false()))

    if not _has_column(bind, "warehouses", "vendor_id"):
        op.add_column("warehouses", sa.Column("vendor_id", sa.Uuid(), nullable=True))
        op.create_index("ix_warehouses_vendor_id", "warehouses", ["vendor_id"])
        # Add the FK only where ALTER ADD CONSTRAINT is supported (PostgreSQL).
        # SQLite test DBs are built from the model via create_all, which already
        # carries the FK; alembic on SQLite cannot ALTER-add a constraint.
        if bind.dialect.name != "sqlite":
            op.create_foreign_key("fk_warehouses_vendor", "warehouses", "suppliers",
                                  ["vendor_id"], ["id"])

    if not _has_table(bind, "job_work_issues"):
        op.create_table(
            "job_work_issues",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("factory_id", sa.Uuid(), nullable=True),
            sa.Column("job_work_id", sa.Uuid(), nullable=True),
            sa.Column("job_worker_id", sa.Uuid(), nullable=False),
            sa.Column("process", sa.String(length=12), nullable=False),
            sa.Column("source_warehouse_id", sa.Uuid(), nullable=True),
            sa.Column("custody_warehouse_id", sa.Uuid(), nullable=False),
            sa.Column("item_type", sa.String(length=12), nullable=False),
            sa.Column("item_id", sa.Uuid(), nullable=False),
            sa.Column("fabric_roll_id", sa.Uuid(), nullable=True),
            sa.Column("qty", Quantity(), nullable=True),
            sa.Column("uom", sa.String(length=10), nullable=False, server_default="KG"),
            sa.Column("weight_kg", Quantity(), nullable=True),
            sa.Column("length_m", Quantity(), nullable=True),
            sa.Column("issue_date", sa.Date(), nullable=False),
            sa.Column("challan_no", sa.String(length=40), nullable=False),
            sa.Column("status", sa.String(length=12), nullable=False, server_default="OUTSTANDING"),
            sa.Column("posting_reference", sa.String(length=120), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["factory_id"], ["branches.id"]),
            sa.ForeignKeyConstraint(["job_work_id"], ["job_works.id"]),
            sa.ForeignKeyConstraint(["job_worker_id"], ["suppliers.id"]),
            sa.ForeignKeyConstraint(["source_warehouse_id"], ["warehouses.id"]),
            sa.ForeignKeyConstraint(["custody_warehouse_id"], ["warehouses.id"]),
            sa.ForeignKeyConstraint(["fabric_roll_id"], ["fabric_rolls.id"]),
        )
        op.create_index("ix_jwissue_company", "job_work_issues", ["company_id"])
        op.create_index("ix_jwissue_factory", "job_work_issues", ["factory_id"])
        op.create_index("ix_jwissue_job_work", "job_work_issues", ["job_work_id"])
        op.create_index("ix_jwissue_worker", "job_work_issues", ["job_worker_id"])
        op.create_index("ix_jwissue_process", "job_work_issues", ["process"])
        op.create_index("ix_jwissue_custody_wh", "job_work_issues", ["custody_warehouse_id"])
        op.create_index("ix_jwissue_item", "job_work_issues", ["item_id"])
        op.create_index("ix_jwissue_roll", "job_work_issues", ["fabric_roll_id"])
        op.create_index("ix_jwissue_challan", "job_work_issues", ["challan_no"])
        op.create_index("ix_jwissue_status", "job_work_issues", ["status"])
        op.create_index("ix_jwissue_issue_date", "job_work_issues", ["issue_date"])
        op.create_index("ix_jwissue_posting_ref", "job_work_issues", ["posting_reference"])


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if _has_table(bind, "job_work_issues"):
        op.drop_table("job_work_issues")
    if _has_column(bind, "warehouses", "vendor_id"):
        if "ix_warehouses_vendor_id" in {ix["name"] for ix in _insp(bind).get_indexes("warehouses")}:
            op.drop_index("ix_warehouses_vendor_id", table_name="warehouses")
        if is_sqlite:
            # Drop the index first (above) so batch recreation doesn't reference the
            # column being removed.
            with op.batch_alter_table("warehouses") as batch:
                batch.drop_column("vendor_id")
        else:
            # PostgreSQL drops the column's dependent FK automatically.
            op.drop_column("warehouses", "vendor_id")
    if _has_column(bind, "suppliers", "is_job_worker"):
        if is_sqlite:
            with op.batch_alter_table("suppliers") as batch:
                batch.drop_column("is_job_worker")
        else:
            op.drop_column("suppliers", "is_job_worker")
