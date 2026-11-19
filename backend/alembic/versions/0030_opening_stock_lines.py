"""Opening-stock lines + import files (Stage 6F-P2, additive).

Adds two new tables that hang off the P1 ``opening_batches`` master:

* ``opening_import_files`` — uploaded workbook metadata + original bytes + hash;
* ``opening_stock_lines`` — typed opening lines (yarn lot / trading aggregate)
  with per-row validation outcome, deduplication key and the id of the FIFO
  stock layer created at approval.

Purely additive: two new tables and their indexes; NO existing table (inventory,
yarn lots, trading items, rolls, vouchers) is altered and no data is backfilled.
Batch-scoped deduplication of valid lines is enforced by a unique constraint on
(batch_id, dedup_key) — invalid rows keep dedup_key NULL (NULLs are distinct), so
they persist for the rejection report without colliding. The same physical file
cannot be imported into one batch twice (batch_id, file_hash).

Revision ID: 0030_opening_stock_lines
Revises: 0029_opening_batches
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from alembic import op

from app.core.types import Money, Quantity

revision = "0030_opening_stock_lines"
down_revision = "0029_opening_batches"
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

    if not _has_table(bind, "opening_import_files"):
        op.create_table(
            "opening_import_files",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("batch_id", sa.Uuid(), nullable=False),
            sa.Column("line_type", sa.String(length=12), nullable=False),
            sa.Column("file_name", sa.String(length=255), nullable=False),
            sa.Column("file_hash", sa.String(length=64), nullable=False),
            sa.Column("content", sa.LargeBinary(), nullable=False),
            sa.Column("template_version", sa.String(length=20), nullable=True),
            sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("valid_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("invalid_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("uploaded_by", sa.Uuid(), nullable=True),
            sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
            *_audit_cols(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["batch_id"], ["opening_batches.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("batch_id", "file_hash", name="uq_opening_import_batch_hash"),
        )
        op.create_index("ix_opening_import_company_id", "opening_import_files", ["company_id"])
        op.create_index("ix_opening_import_batch_id", "opening_import_files", ["batch_id"])
        op.create_index("ix_opening_import_line_type", "opening_import_files", ["line_type"])
        op.create_index("ix_opening_import_hash", "opening_import_files", ["file_hash"])

    if not _has_table(bind, "opening_stock_lines"):
        op.create_table(
            "opening_stock_lines",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("batch_id", sa.Uuid(), nullable=False),
            sa.Column("import_file_id", sa.Uuid(), nullable=True),
            sa.Column("line_type", sa.String(length=12), nullable=False),
            sa.Column("row_number", sa.Integer(), nullable=True),
            sa.Column("factory_id", sa.Uuid(), nullable=True),
            sa.Column("warehouse_id", sa.Uuid(), nullable=True),
            sa.Column("item_type", sa.String(length=12), nullable=False),
            sa.Column("item_id", sa.Uuid(), nullable=True),
            sa.Column("uom_id", sa.Uuid(), nullable=True),
            sa.Column("quantity", Quantity(), nullable=True),
            sa.Column("weight_kg", Quantity(), nullable=True),
            sa.Column("unit_cost", Money(), nullable=True),
            sa.Column("total_value", Money(), nullable=True),
            sa.Column("effective_date", sa.Date(), nullable=True),
            sa.Column("source_document", sa.String(length=120), nullable=True),
            sa.Column("source_reference", sa.String(length=200), nullable=True),
            sa.Column("yarn_lot_id", sa.Uuid(), nullable=True),
            sa.Column("internal_lot_no", sa.String(length=40), nullable=True),
            sa.Column("supplier_id", sa.Uuid(), nullable=True),
            sa.Column("supplier_lot_no", sa.String(length=40), nullable=True),
            sa.Column("lot_policy", sa.String(length=20), nullable=True),
            sa.Column("legacy_unknown", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("validation_status", sa.String(length=12), nullable=False, server_default="PENDING"),
            sa.Column("validation_errors", sa.Text(), nullable=True),
            sa.Column("dedup_key", sa.String(length=200), nullable=True),
            sa.Column("stock_layer_id", sa.Uuid(), nullable=True),
            sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
            *_audit_cols(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["batch_id"], ["opening_batches.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["import_file_id"], ["opening_import_files.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["factory_id"], ["branches.id"]),
            sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
            sa.ForeignKeyConstraint(["uom_id"], ["units_of_measure.id"]),
            sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"]),
            # Batch-scoped dedup of VALID lines; invalid rows keep dedup_key NULL
            # (NULLs are distinct in SQLite and PostgreSQL, so they never collide).
            sa.UniqueConstraint("batch_id", "dedup_key", name="uq_opening_line_batch_dedup"),
        )
        op.create_index("ix_opening_line_company_id", "opening_stock_lines", ["company_id"])
        op.create_index("ix_opening_line_batch_id", "opening_stock_lines", ["batch_id"])
        op.create_index("ix_opening_line_import_file", "opening_stock_lines", ["import_file_id"])
        op.create_index("ix_opening_line_type", "opening_stock_lines", ["line_type"])
        op.create_index("ix_opening_line_item_type", "opening_stock_lines", ["item_type"])
        op.create_index("ix_opening_line_item_id", "opening_stock_lines", ["item_id"])
        op.create_index("ix_opening_line_factory_id", "opening_stock_lines", ["factory_id"])
        op.create_index("ix_opening_line_yarn_lot", "opening_stock_lines", ["yarn_lot_id"])
        op.create_index("ix_opening_line_validation", "opening_stock_lines", ["validation_status"])


def downgrade() -> None:
    bind = op.get_bind()
    # Drop the child (lines) before the parent (import files); each cascades its indexes.
    if _has_table(bind, "opening_stock_lines"):
        op.drop_table("opening_stock_lines")
    if _has_table(bind, "opening_import_files"):
        op.drop_table("opening_import_files")
