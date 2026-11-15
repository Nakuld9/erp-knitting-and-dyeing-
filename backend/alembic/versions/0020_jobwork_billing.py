"""Stage 3E: job-work vendor bill matching (additive).

Adds ``job_work_bills`` and ``job_work_bill_lines``. Nothing existing is altered
and no historical data is backfilled. Guarded; safe on the control DB and each
company DB, on SQLite and PostgreSQL.

Revision ID: 0020_jobwork_billing
Revises: 0019_jobwork_receipts
Create Date: 2026-11-15
"""
import sqlalchemy as sa
from alembic import op

from app.core.types import Money, Quantity

revision = "0020_jobwork_billing"
down_revision = "0019_jobwork_receipts"
branch_labels = None
depends_on = None


def _insp(bind):
    return sa.inspect(bind)


def _has_table(bind, name):
    return _insp(bind).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "job_work_bills"):
        op.create_table(
            "job_work_bills",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("factory_id", sa.Uuid(), nullable=True),
            sa.Column("supplier_id", sa.Uuid(), nullable=False),
            sa.Column("bill_type", sa.String(length=12), nullable=False, server_default="INVOICE"),
            sa.Column("process", sa.String(length=12), nullable=True),
            sa.Column("invoice_no", sa.String(length=50), nullable=False),
            sa.Column("invoice_date", sa.Date(), nullable=False),
            sa.Column("tax_code_id", sa.Uuid(), nullable=True),
            sa.Column("service_charge", Money(), nullable=True),
            sa.Column("additional_charges", Money(), nullable=True),
            sa.Column("taxable_amount", Money(), nullable=True),
            sa.Column("cgst", Money(), nullable=True),
            sa.Column("sgst", Money(), nullable=True),
            sa.Column("igst", Money(), nullable=True),
            sa.Column("tds_rate", Quantity(), nullable=True),
            sa.Column("tds_amount", Money(), nullable=True),
            sa.Column("total_amount", Money(), nullable=True),
            sa.Column("status", sa.String(length=12), nullable=False, server_default="DRAFT"),
            sa.Column("payment_status", sa.String(length=10), nullable=False, server_default="UNPAID"),
            sa.Column("posting_reference", sa.String(length=120), nullable=True),
            sa.Column("remarks", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["factory_id"], ["branches.id"]),
            sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"]),
            sa.ForeignKeyConstraint(["tax_code_id"], ["tax_codes.id"]),
            sa.UniqueConstraint("company_id", "supplier_id", "bill_type", "invoice_no",
                                name="uq_jwbill_supplier_invoice"),
        )
        for col in ("company_id", "factory_id", "supplier_id", "bill_type", "invoice_no",
                    "invoice_date", "status", "posting_reference"):
            op.create_index(f"ix_jwbill_{col}", "job_work_bills", [col])

    if not _has_table(bind, "job_work_bill_lines"):
        op.create_table(
            "job_work_bill_lines",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("bill_id", sa.Uuid(), nullable=False),
            sa.Column("job_work_id", sa.Uuid(), nullable=True),
            sa.Column("job_work_issue_id", sa.Uuid(), nullable=True),
            sa.Column("fabric_roll_id", sa.Uuid(), nullable=True),
            sa.Column("lot_no", sa.String(length=40), nullable=True),
            sa.Column("process", sa.String(length=12), nullable=True),
            sa.Column("qty", Quantity(), nullable=True),
            sa.Column("uom", sa.String(length=10), nullable=False, server_default="KG"),
            sa.Column("weight_kg", Quantity(), nullable=True),
            sa.Column("length_m", Quantity(), nullable=True),
            sa.Column("rate", Money(), nullable=True),
            sa.Column("rate_basis", sa.String(length=10), nullable=False, server_default="PER_KG"),
            sa.Column("amount", Money(), nullable=True),
            sa.Column("is_disputed", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("disputed_amount", Money(), nullable=True),
            sa.Column("narration", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["bill_id"], ["job_work_bills.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["job_work_id"], ["job_works.id"]),
            sa.ForeignKeyConstraint(["job_work_issue_id"], ["job_work_issues.id"]),
            sa.ForeignKeyConstraint(["fabric_roll_id"], ["fabric_rolls.id"]),
        )
        for col in ("company_id", "bill_id", "job_work_id", "job_work_issue_id",
                    "fabric_roll_id", "lot_no"):
            op.create_index(f"ix_jwline_{col}", "job_work_bill_lines", [col])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "job_work_bill_lines"):
        op.drop_table("job_work_bill_lines")
    if _has_table(bind, "job_work_bills"):
        op.drop_table("job_work_bills")
