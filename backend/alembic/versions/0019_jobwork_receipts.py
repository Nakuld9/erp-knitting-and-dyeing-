"""Stage 3D: job-work receipts & reconciliation (additive).

Adds, additively and reversibly:
* reconciliation running totals on ``job_work_issues`` (resolved/accepted/
  returned/rejected/loss/shortage) and widens ``status`` to hold
  PARTIALLY_RECEIVED / CLOSED;
* ``job_work_receipts`` — one receipt event per partial/full receipt.

Nothing existing is altered in meaning and no historical data is backfilled.
Guarded; safe on the control DB and each company DB, on SQLite and PostgreSQL.

Revision ID: 0019_jobwork_receipts
Revises: 0018_jobwork_custody
Create Date: 2026-11-15
"""
import sqlalchemy as sa
from alembic import op

from app.core.types import Quantity

revision = "0019_jobwork_receipts"
down_revision = "0018_jobwork_custody"
branch_labels = None
depends_on = None

_ISSUE_TOTALS = ("resolved_input_qty", "accepted_output_qty", "returned_qty",
                 "rejected_qty", "process_loss_qty", "shortage_qty")


def _insp(bind):
    return sa.inspect(bind)


def _has_table(bind, name):
    return _insp(bind).has_table(name)


def _has_column(bind, table, col):
    insp = _insp(bind)
    return insp.has_table(table) and col in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    for col in _ISSUE_TOTALS:
        if not _has_column(bind, "job_work_issues", col):
            op.add_column("job_work_issues", sa.Column(col, Quantity(), nullable=False,
                                                       server_default="0"))
    # widen status to hold the longer lifecycle values (PostgreSQL enforces length)
    if not is_sqlite:
        op.alter_column("job_work_issues", "status", type_=sa.String(length=18),
                        existing_type=sa.String(length=12), existing_nullable=False)

    if not _has_table(bind, "job_work_receipts"):
        op.create_table(
            "job_work_receipts",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("factory_id", sa.Uuid(), nullable=True),
            sa.Column("job_work_issue_id", sa.Uuid(), nullable=False),
            sa.Column("job_work_id", sa.Uuid(), nullable=True),
            sa.Column("job_worker_id", sa.Uuid(), nullable=False),
            sa.Column("process", sa.String(length=12), nullable=False),
            sa.Column("receipt_date", sa.Date(), nullable=False),
            sa.Column("challan_no", sa.String(length=40), nullable=True),
            sa.Column("input_resolved_qty", Quantity(), nullable=True),
            sa.Column("accepted_input_qty", Quantity(), nullable=True),
            sa.Column("returned_qty", Quantity(), nullable=True),
            sa.Column("rejected_qty", Quantity(), nullable=True),
            sa.Column("rework_qty", Quantity(), nullable=True),
            sa.Column("process_loss_qty", Quantity(), nullable=True),
            sa.Column("shortage_qty", Quantity(), nullable=True),
            sa.Column("accepted_output_qty", Quantity(), nullable=True),
            sa.Column("output_weight_kg", Quantity(), nullable=True),
            sa.Column("output_length_m", Quantity(), nullable=True),
            sa.Column("uom", sa.String(length=10), nullable=False, server_default="KG"),
            sa.Column("inspection_result", sa.String(length=10), nullable=True),
            sa.Column("source_roll_id", sa.Uuid(), nullable=True),
            sa.Column("output_roll_id", sa.Uuid(), nullable=True),
            sa.Column("output_warehouse_id", sa.Uuid(), nullable=True),
            sa.Column("return_warehouse_id", sa.Uuid(), nullable=True),
            sa.Column("is_excess", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("authorized_by", sa.Uuid(), nullable=True),
            sa.Column("posting_reference", sa.String(length=120), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["factory_id"], ["branches.id"]),
            sa.ForeignKeyConstraint(["job_work_issue_id"], ["job_work_issues.id"]),
            sa.ForeignKeyConstraint(["job_work_id"], ["job_works.id"]),
            sa.ForeignKeyConstraint(["job_worker_id"], ["suppliers.id"]),
            sa.ForeignKeyConstraint(["source_roll_id"], ["fabric_rolls.id"]),
            sa.ForeignKeyConstraint(["output_roll_id"], ["fabric_rolls.id"]),
            sa.ForeignKeyConstraint(["output_warehouse_id"], ["warehouses.id"]),
            sa.ForeignKeyConstraint(["return_warehouse_id"], ["warehouses.id"]),
        )
        for col in ("company_id", "factory_id", "job_work_issue_id", "job_work_id", "job_worker_id",
                    "process", "receipt_date", "challan_no", "source_roll_id", "output_roll_id",
                    "posting_reference"):
            op.create_index(f"ix_jwreceipt_{col}", "job_work_receipts", [col])


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if _has_table(bind, "job_work_receipts"):
        op.drop_table("job_work_receipts")
    if not is_sqlite:
        if _has_column(bind, "job_work_issues", "status"):
            op.alter_column("job_work_issues", "status", type_=sa.String(length=12),
                            existing_type=sa.String(length=18), existing_nullable=False)
        for col in _ISSUE_TOTALS:
            if _has_column(bind, "job_work_issues", col):
                op.drop_column("job_work_issues", col)
    else:
        with op.batch_alter_table("job_work_issues") as batch:
            for col in _ISSUE_TOTALS:
                if _has_column(bind, "job_work_issues", col):
                    batch.drop_column(col)
