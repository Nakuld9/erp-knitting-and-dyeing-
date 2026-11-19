"""Opening vendor-custody & vendor-held-WIP lines (Stage 6F-P4, additive).

Adds ``opening_custody_lines`` — the typed opening lines for company-owned goods
(yarn lot or manufactured fabric roll) physically held by an outside job worker
on the cutover date, under a P1 ``opening_batches`` row of type VENDOR_CUSTODY /
WIP.

Purely additive: one new table + its indexes. Changes NO existing table — not
``job_work_issues``, ``fabric_rolls``, ``yarn_lots``, the stock layers or the
genealogy edges. No backfill, no renumbering, no destructive change. Batch-scoped
dedup of valid lines via a unique constraint on (batch_id, dedup_key); invalid /
duplicate rows keep dedup_key NULL (NULLs distinct), so they persist for the
rejection report.

Revision ID: 0033_opening_custody_lines
Revises: 0032_opening_roll_lines
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from alembic import op

from app.core.types import Money, Quantity

revision = "0033_opening_custody_lines"
down_revision = "0032_opening_roll_lines"
branch_labels = None
depends_on = None


def _has_table(bind, name):
    return sa.inspect(bind).has_table(name)


def _audit_cols():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "opening_custody_lines"):
        return
    op.create_table(
        "opening_custody_lines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("import_file_id", sa.Uuid(), nullable=True),
        sa.Column("row_number", sa.Integer(), nullable=True),
        sa.Column("line_kind", sa.String(length=20), nullable=False),
        sa.Column("custody_type", sa.String(length=12), nullable=False),
        sa.Column("registration_mode", sa.String(length=20), nullable=True),
        sa.Column("factory_id", sa.Uuid(), nullable=True),
        sa.Column("job_worker_id", sa.Uuid(), nullable=True),
        sa.Column("process", sa.String(length=12), nullable=True),
        sa.Column("source_jobwork_ref", sa.String(length=60), nullable=True),
        sa.Column("prior_vendor_id", sa.Uuid(), nullable=True),
        sa.Column("source_document", sa.String(length=120), nullable=True),
        sa.Column("source_reference", sa.String(length=200), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("erp_roll_no", sa.String(length=40), nullable=True),
        sa.Column("legacy_label", sa.String(length=80), nullable=True),
        sa.Column("roll_kind", sa.String(length=10), nullable=True),
        sa.Column("origin_stream", sa.String(length=10), nullable=True),
        sa.Column("fabric_roll_id", sa.Uuid(), nullable=True),
        sa.Column("fabric_type_id", sa.Uuid(), nullable=True),
        sa.Column("structure", sa.String(length=120), nullable=True),
        sa.Column("gsm", Quantity(), nullable=True),
        sa.Column("width_cm", Quantity(), nullable=True),
        sa.Column("colour", sa.String(length=60), nullable=True),
        sa.Column("shade_id", sa.Uuid(), nullable=True),
        sa.Column("dye_lot", sa.String(length=40), nullable=True),
        sa.Column("yarn_id", sa.Uuid(), nullable=True),
        sa.Column("internal_lot_no", sa.String(length=40), nullable=True),
        sa.Column("yarn_lot_id", sa.Uuid(), nullable=True),
        sa.Column("quantity", Quantity(), nullable=True),
        sa.Column("net_weight_kg", Quantity(), nullable=True),
        sa.Column("uom_id", sa.Uuid(), nullable=True),
        sa.Column("length_m", Quantity(), nullable=True),
        sa.Column("unit_cost", Money(), nullable=True),
        sa.Column("total_value", Money(), nullable=True),
        sa.Column("valuation_source", sa.String(length=120), nullable=True),
        sa.Column("disputed_unknown", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("validation_status", sa.String(length=12), nullable=False, server_default="PENDING"),
        sa.Column("validation_errors", sa.Text(), nullable=True),
        sa.Column("dedup_key", sa.String(length=200), nullable=True),
        sa.Column("custody_warehouse_id", sa.Uuid(), nullable=True),
        sa.Column("stock_layer_id", sa.Uuid(), nullable=True),
        sa.Column("job_work_issue_id", sa.Uuid(), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        *_audit_cols(),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["batch_id"], ["opening_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["import_file_id"], ["opening_import_files.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["factory_id"], ["branches.id"]),
        sa.ForeignKeyConstraint(["job_worker_id"], ["suppliers.id"]),
        sa.ForeignKeyConstraint(["prior_vendor_id"], ["suppliers.id"]),
        sa.ForeignKeyConstraint(["fabric_type_id"], ["fabric_types.id"]),
        sa.ForeignKeyConstraint(["shade_id"], ["shade_codes.id"]),
        sa.ForeignKeyConstraint(["yarn_id"], ["yarn_masters.id"]),
        sa.ForeignKeyConstraint(["uom_id"], ["units_of_measure.id"]),
        sa.UniqueConstraint("batch_id", "dedup_key", name="uq_opening_custody_batch_dedup"),
    )
    op.create_index("ix_opening_custody_company_id", "opening_custody_lines", ["company_id"])
    op.create_index("ix_opening_custody_batch_id", "opening_custody_lines", ["batch_id"])
    op.create_index("ix_opening_custody_import_file", "opening_custody_lines", ["import_file_id"])
    op.create_index("ix_opening_custody_line_kind", "opening_custody_lines", ["line_kind"])
    op.create_index("ix_opening_custody_type", "opening_custody_lines", ["custody_type"])
    op.create_index("ix_opening_custody_vendor", "opening_custody_lines", ["job_worker_id"])
    op.create_index("ix_opening_custody_factory", "opening_custody_lines", ["factory_id"])
    op.create_index("ix_opening_custody_erp_no", "opening_custody_lines", ["erp_roll_no"])
    op.create_index("ix_opening_custody_legacy", "opening_custody_lines", ["legacy_label"])
    op.create_index("ix_opening_custody_fabric_roll", "opening_custody_lines", ["fabric_roll_id"])
    op.create_index("ix_opening_custody_yarn", "opening_custody_lines", ["yarn_id"])
    op.create_index("ix_opening_custody_yarn_lot", "opening_custody_lines", ["yarn_lot_id"])
    op.create_index("ix_opening_custody_validation", "opening_custody_lines", ["validation_status"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "opening_custody_lines"):
        op.drop_table("opening_custody_lines")   # cascades its indexes
