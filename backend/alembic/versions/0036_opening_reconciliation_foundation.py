"""Opening reconciliation foundation (Stage 6F-P6, additive).

Adds two company-scoped tables for the opening-cutover reconciliation layer:
``opening_reconciliation_runs`` and ``opening_discrepancies``. Purely additive — no
existing table is changed, no backfill. Reconciliation is read/compute-only, so
these tables are the ONLY new writable records a run produces (besides audit).

Revision ID: 0036_opening_reconciliation_foundation
Revises: 0035_gst_itc_module
Create Date: 2026-11-20
"""
import sqlalchemy as sa
from alembic import op

from app.core.types import Money

revision = "0036_opening_reconciliation_foundation"
down_revision = "0035_gst_itc_module"
branch_labels = None
depends_on = None

_TABLES = ("opening_discrepancies", "opening_reconciliation_runs")


def _has_table(bind, name):
    return sa.inspect(bind).has_table(name)


def _audit():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
    ]


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "opening_reconciliation_runs"):
        op.create_table(
            "opening_reconciliation_runs",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("factory_id", sa.Uuid(), nullable=True),
            sa.Column("recon_type", sa.String(length=20), nullable=False),
            sa.Column("effective_date", sa.Date(), nullable=True),
            sa.Column("run_number", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="NOT_STARTED"),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("initiated_by", sa.Uuid(), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("error_detail", sa.Text(), nullable=True),
            sa.Column("discrepancy_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("incomplete", sa.Boolean(), nullable=False, server_default=sa.false()),
            *_audit(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["factory_id"], ["branches.id"]),
            sa.UniqueConstraint("company_id", "recon_type", "run_number",
                                name="uq_openrecon_run_number"),
        )
        op.create_index("ix_openrecon_company", "opening_reconciliation_runs", ["company_id"])
        op.create_index("ix_openrecon_factory", "opening_reconciliation_runs", ["factory_id"])
        op.create_index("ix_openrecon_type", "opening_reconciliation_runs", ["recon_type"])
        op.create_index("ix_openrecon_status", "opening_reconciliation_runs", ["status"])

    if not _has_table(bind, "opening_discrepancies"):
        op.create_table(
            "opening_discrepancies",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("factory_id", sa.Uuid(), nullable=True),
            sa.Column("run_id", sa.Uuid(), nullable=False),
            sa.Column("recon_category", sa.String(length=24), nullable=False),
            sa.Column("account_code", sa.String(length=20), nullable=True),
            sa.Column("inventory_category", sa.String(length=60), nullable=True),
            sa.Column("source_batch_id", sa.Uuid(), nullable=True),
            sa.Column("source_reference", sa.String(length=200), nullable=True),
            sa.Column("discrepancy_key", sa.String(length=200), nullable=False),
            sa.Column("expected_value", Money(), nullable=True),
            sa.Column("actual_value", Money(), nullable=True),
            sa.Column("difference", Money(), nullable=True),
            sa.Column("currency", sa.String(length=3), nullable=False, server_default="INR"),
            sa.Column("qty_difference", sa.Numeric(20, 6), nullable=True),
            sa.Column("explanation", sa.Text(), nullable=True),
            sa.Column("responsible_user", sa.Uuid(), nullable=True),
            sa.Column("resolution_status", sa.String(length=16), nullable=False,
                      server_default="UNRESOLVED"),
            sa.Column("resolution_notes", sa.Text(), nullable=True),
            sa.Column("supporting_reference", sa.String(length=200), nullable=True),
            sa.Column("resolved_by", sa.Uuid(), nullable=True),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            *_audit(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["factory_id"], ["branches.id"]),
            sa.ForeignKeyConstraint(["run_id"], ["opening_reconciliation_runs.id"],
                                    ondelete="CASCADE"),
            sa.UniqueConstraint("run_id", "discrepancy_key", name="uq_opendisc_run_key"),
        )
        op.create_index("ix_opendisc_company", "opening_discrepancies", ["company_id"])
        op.create_index("ix_opendisc_factory", "opening_discrepancies", ["factory_id"])
        op.create_index("ix_opendisc_run", "opening_discrepancies", ["run_id"])
        op.create_index("ix_opendisc_category", "opening_discrepancies", ["recon_category"])
        op.create_index("ix_opendisc_key", "opening_discrepancies", ["discrepancy_key"])
        op.create_index("ix_opendisc_resolution", "opening_discrepancies", ["resolution_status"])


def downgrade() -> None:
    bind = op.get_bind()
    for tbl in _TABLES:
        if _has_table(bind, tbl):
            op.drop_table(tbl)
