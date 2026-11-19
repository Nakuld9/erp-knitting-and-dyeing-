"""Financial opening lines + CA decision register (Stage 6F-P5, additive).

Adds seven new tables hung off the P1 ``opening_batches`` master for the
financial opening cutover: ``opening_gl_lines``, ``opening_ar_lines``,
``opening_ap_lines``, ``opening_bank_lines``, ``opening_fixed_asset_lines``,
``opening_statutory_lines`` and ``opening_ca_decisions``.

Purely additive: seven new tables + their indexes. Changes NO existing table —
not the ledger (vouchers/journal_lines), chart of accounts, bank, fixed-asset or
P1-P4 opening/stock tables. No backfill. Each line table has a batch-scoped
``UniqueConstraint(batch_id, dedup_key)`` (NULLs distinct, so invalid rows
persist for rejection reports); the CA register is unique on (batch_id,
decision_key).

Revision ID: 0034_opening_financial_lines
Revises: 0033_opening_custody_lines
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from alembic import op

from app.core.types import Money

revision = "0034_opening_financial_lines"
down_revision = "0033_opening_custody_lines"
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


def _base_cols():
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("import_file_id", sa.Uuid(), nullable=True),
        sa.Column("row_number", sa.Integer(), nullable=True),
        sa.Column("source_document", sa.String(length=120), nullable=True),
        sa.Column("source_reference", sa.String(length=200), nullable=True),
        sa.Column("disputed_unknown", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("validation_status", sa.String(length=12), nullable=False, server_default="PENDING"),
        sa.Column("validation_errors", sa.Text(), nullable=True),
        sa.Column("dedup_key", sa.String(length=200), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
    ]


def _fks(extra=()):
    base = [
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["batch_id"], ["opening_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["import_file_id"], ["opening_import_files.id"], ondelete="SET NULL"),
    ]
    return base + list(extra)


def _idx(table):
    op.create_index(f"ix_{table}_company", table, ["company_id"])
    op.create_index(f"ix_{table}_batch", table, ["batch_id"])
    op.create_index(f"ix_{table}_validation", table, ["validation_status"])


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "opening_gl_lines"):
        op.create_table(
            "opening_gl_lines", *_base_cols(),
            sa.Column("account_code", sa.String(length=20), nullable=False),
            sa.Column("account_id", sa.Uuid(), nullable=True),
            sa.Column("debit", Money(), nullable=True),
            sa.Column("credit", Money(), nullable=True),
            sa.Column("narration", sa.String(length=255), nullable=True),
            sa.Column("is_control_split", sa.Boolean(), nullable=False, server_default=sa.false()),
            *_audit_cols(), *_fks(),
            sa.UniqueConstraint("batch_id", "dedup_key", name="uq_opening_gl_batch_dedup"),
        )
        _idx("opening_gl_lines")
        op.create_index("ix_opening_gl_account", "opening_gl_lines", ["account_code"])

    if not _has_table(bind, "opening_ar_lines"):
        op.create_table(
            "opening_ar_lines", *_base_cols(),
            sa.Column("ar_kind", sa.String(length=20), nullable=False, server_default="INVOICE"),
            sa.Column("customer_id", sa.Uuid(), nullable=True),
            sa.Column("invoice_no", sa.String(length=40), nullable=True),
            sa.Column("invoice_date", sa.Date(), nullable=True),
            sa.Column("due_date", sa.Date(), nullable=True),
            sa.Column("original_amount", Money(), nullable=True),
            sa.Column("received_amount", Money(), nullable=True),
            sa.Column("outstanding_amount", Money(), nullable=True),
            sa.Column("tax_reference", sa.String(length=60), nullable=True),
            sa.Column("currency", sa.String(length=3), nullable=True),
            sa.Column("notes", sa.String(length=255), nullable=True),
            *_audit_cols(), *_fks((sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),)),
            sa.UniqueConstraint("batch_id", "dedup_key", name="uq_opening_ar_batch_dedup"),
        )
        _idx("opening_ar_lines")
        op.create_index("ix_opening_ar_customer", "opening_ar_lines", ["customer_id"])
        op.create_index("ix_opening_ar_invoice", "opening_ar_lines", ["invoice_no"])

    if not _has_table(bind, "opening_ap_lines"):
        op.create_table(
            "opening_ap_lines", *_base_cols(),
            sa.Column("ap_kind", sa.String(length=20), nullable=False, server_default="INVOICE"),
            sa.Column("supplier_id", sa.Uuid(), nullable=True),
            sa.Column("invoice_no", sa.String(length=40), nullable=True),
            sa.Column("invoice_date", sa.Date(), nullable=True),
            sa.Column("due_date", sa.Date(), nullable=True),
            sa.Column("original_amount", Money(), nullable=True),
            sa.Column("paid_amount", Money(), nullable=True),
            sa.Column("outstanding_amount", Money(), nullable=True),
            sa.Column("tds_amount", Money(), nullable=True),
            sa.Column("tax_reference", sa.String(length=60), nullable=True),
            sa.Column("notes", sa.String(length=255), nullable=True),
            *_audit_cols(), *_fks((sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"]),)),
            sa.UniqueConstraint("batch_id", "dedup_key", name="uq_opening_ap_batch_dedup"),
        )
        _idx("opening_ap_lines")
        op.create_index("ix_opening_ap_supplier", "opening_ap_lines", ["supplier_id"])
        op.create_index("ix_opening_ap_invoice", "opening_ap_lines", ["invoice_no"])

    if not _has_table(bind, "opening_bank_lines"):
        op.create_table(
            "opening_bank_lines", *_base_cols(),
            sa.Column("bank_account_id", sa.Uuid(), nullable=True),
            sa.Column("account_code", sa.String(length=20), nullable=True),
            sa.Column("account_id", sa.Uuid(), nullable=True),
            sa.Column("is_cash", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("book_balance", Money(), nullable=True),
            sa.Column("statement_balance", Money(), nullable=True),
            sa.Column("outstanding_cheques", Money(), nullable=True),
            sa.Column("deposits_in_transit", Money(), nullable=True),
            sa.Column("effective_date", sa.Date(), nullable=True),
            sa.Column("notes", sa.String(length=255), nullable=True),
            *_audit_cols(), *_fks((sa.ForeignKeyConstraint(["bank_account_id"], ["bank_accounts.id"]),)),
            sa.UniqueConstraint("batch_id", "dedup_key", name="uq_opening_bank_batch_dedup"),
        )
        _idx("opening_bank_lines")

    if not _has_table(bind, "opening_fixed_asset_lines"):
        op.create_table(
            "opening_fixed_asset_lines", *_base_cols(),
            sa.Column("asset_code", sa.String(length=40), nullable=False),
            sa.Column("name", sa.String(length=160), nullable=True),
            sa.Column("category", sa.String(length=60), nullable=True),
            sa.Column("factory_id", sa.Uuid(), nullable=True),
            sa.Column("asset_account_code", sa.String(length=20), nullable=True),
            sa.Column("accum_dep_account_code", sa.String(length=20), nullable=True),
            sa.Column("acquisition_date", sa.Date(), nullable=True),
            sa.Column("original_cost", Money(), nullable=True),
            sa.Column("accumulated_depreciation", Money(), nullable=True),
            sa.Column("net_book_value", Money(), nullable=True),
            sa.Column("salvage_value", Money(), nullable=True),
            sa.Column("method", sa.String(length=3), nullable=True),
            sa.Column("useful_life_months", sa.Integer(), nullable=True),
            sa.Column("verification_status", sa.String(length=30), nullable=True),
            sa.Column("fixed_asset_id", sa.Uuid(), nullable=True),
            sa.Column("notes", sa.String(length=255), nullable=True),
            *_audit_cols(), *_fks((sa.ForeignKeyConstraint(["factory_id"], ["branches.id"]),)),
            sa.UniqueConstraint("batch_id", "dedup_key", name="uq_opening_fa_batch_dedup"),
        )
        _idx("opening_fixed_asset_lines")
        op.create_index("ix_opening_fa_asset", "opening_fixed_asset_lines", ["asset_code"])

    if not _has_table(bind, "opening_statutory_lines"):
        op.create_table(
            "opening_statutory_lines", *_base_cols(),
            sa.Column("statutory_type", sa.String(length=20), nullable=True),
            sa.Column("account_code", sa.String(length=20), nullable=False),
            sa.Column("account_id", sa.Uuid(), nullable=True),
            sa.Column("debit", Money(), nullable=True),
            sa.Column("credit", Money(), nullable=True),
            sa.Column("period_reference", sa.String(length=40), nullable=True),
            sa.Column("challan_reference", sa.String(length=60), nullable=True),
            sa.Column("ca_approved", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("notes", sa.String(length=255), nullable=True),
            *_audit_cols(), *_fks(),
            sa.UniqueConstraint("batch_id", "dedup_key", name="uq_opening_stat_batch_dedup"),
        )
        _idx("opening_statutory_lines")
        op.create_index("ix_opening_stat_account", "opening_statutory_lines", ["account_code"])

    if not _has_table(bind, "opening_ca_decisions"):
        op.create_table(
            "opening_ca_decisions",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("batch_id", sa.Uuid(), nullable=False),
            sa.Column("decision_key", sa.String(length=60), nullable=False),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("proposed_options", sa.Text(), nullable=True),
            sa.Column("approved_treatment", sa.Text(), nullable=True),
            sa.Column("supporting_reference", sa.String(length=200), nullable=True),
            sa.Column("reviewer_name", sa.String(length=120), nullable=True),
            sa.Column("reviewer_id", sa.Uuid(), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(length=10), nullable=False, server_default="PENDING"),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.true()),
            *_audit_cols(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["batch_id"], ["opening_batches.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("batch_id", "decision_key", name="uq_opening_ca_batch_key"),
        )
        op.create_index("ix_opening_ca_company", "opening_ca_decisions", ["company_id"])
        op.create_index("ix_opening_ca_batch", "opening_ca_decisions", ["batch_id"])
        op.create_index("ix_opening_ca_key", "opening_ca_decisions", ["decision_key"])
        op.create_index("ix_opening_ca_status", "opening_ca_decisions", ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    for tbl in ("opening_ca_decisions", "opening_statutory_lines", "opening_fixed_asset_lines",
                "opening_bank_lines", "opening_ap_lines", "opening_ar_lines", "opening_gl_lines"):
        if _has_table(bind, tbl):
            op.drop_table(tbl)
