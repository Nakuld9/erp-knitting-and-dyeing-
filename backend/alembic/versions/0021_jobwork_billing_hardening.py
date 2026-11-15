"""Stage 3F: harden job-work billing controls (additive).

Adds, additively and reversibly:
* ``job_work_issues`` agreed-charge snapshot columns (frozen billing baseline).
* ``job_work_bills`` columns: TDS section/source, advance flag, credit/debit-note
  original-bill linkage, over-allocation authorisation audit, and approver audit.
* ``jobwork_tds_rates`` (company-authorised TDS sections) and
  ``jobwork_billing_policies`` (CA-approval switches for ambiguous postings).

Nothing existing is altered in meaning; no historical bills, vouchers or links are
backfilled. Guarded; safe on the control DB and each company DB, SQLite + PostgreSQL.

Revision ID: 0021_jobwork_billing_hardening
Revises: 0020_jobwork_billing
Create Date: 2026-11-15
"""
import sqlalchemy as sa
from alembic import op

from app.core.types import Money, Quantity

revision = "0021_jobwork_billing_hardening"
down_revision = "0020_jobwork_billing"
branch_labels = None
depends_on = None

_ISSUE_COLS = [
    ("agreed_charge_snapshot", Money()),
    ("agreed_rate_snapshot", Money()),
    ("agreed_basis_snapshot", sa.String(length=10)),
    ("agreed_qty_snapshot", Quantity()),
    ("agreed_uom_snapshot", sa.String(length=10)),
]
_BILL_COLS = [
    ("tds_section_code", sa.String(length=20), None),
    ("tds_source", sa.String(length=20), None),
    ("is_advance", sa.Boolean(), sa.false()),
    ("original_bill_id", sa.Uuid(), None),
    ("is_over_allocated", sa.Boolean(), sa.false()),
    ("over_allocation_reason", sa.String(length=255), None),
    ("over_allocated_by", sa.Uuid(), None),
    ("over_allocated_at", sa.DateTime(timezone=True), None),
    ("approved_by", sa.Uuid(), None),
    ("approved_at", sa.DateTime(timezone=True), None),
]


def _insp(bind):
    return sa.inspect(bind)


def _has_table(bind, name):
    return _insp(bind).has_table(name)


def _has_column(bind, table, col):
    insp = _insp(bind)
    return insp.has_table(table) and col in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    for col, typ in _ISSUE_COLS:
        if not _has_column(bind, "job_work_issues", col):
            op.add_column("job_work_issues", sa.Column(col, typ, nullable=True))

    for col, typ, default in _BILL_COLS:
        if not _has_column(bind, "job_work_bills", col):
            kw = {"nullable": True}
            if default is not None:
                kw = {"nullable": False, "server_default": default}
            op.add_column("job_work_bills", sa.Column(col, typ, **kw))
    if not _has_column(bind, "job_work_bills", "original_bill_id"):
        pass  # (added above; FK carried by the model for create_all)

    if not _has_table(bind, "jobwork_tds_rates"):
        op.create_table(
            "jobwork_tds_rates",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("section_code", sa.String(length=20), nullable=False),
            sa.Column("rate", Quantity(), nullable=True),
            sa.Column("description", sa.String(length=120), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.UniqueConstraint("company_id", "section_code", name="uq_jwtds_company_section"),
        )
        op.create_index("ix_jwtds_company", "jobwork_tds_rates", ["company_id"])
        op.create_index("ix_jwtds_section", "jobwork_tds_rates", ["section_code"])

    if not _has_table(bind, "jobwork_billing_policies"):
        op.create_table(
            "jobwork_billing_policies",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("credit_note_posting_approved", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("debit_note_posting_approved", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("advance_posting_approved", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.UniqueConstraint("company_id", name="uq_jwpolicy_company"),
        )
        op.create_index("ix_jwpolicy_company", "jobwork_billing_policies", ["company_id"])


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if _has_table(bind, "jobwork_billing_policies"):
        op.drop_table("jobwork_billing_policies")
    if _has_table(bind, "jobwork_tds_rates"):
        op.drop_table("jobwork_tds_rates")

    bill_cols = [c for c, _t, _d in _BILL_COLS if _has_column(bind, "job_work_bills", c)]
    issue_cols = [c for c, _t in _ISSUE_COLS if _has_column(bind, "job_work_issues", c)]
    if is_sqlite:
        with op.batch_alter_table("job_work_bills") as batch:
            for c in bill_cols:
                batch.drop_column(c)
        with op.batch_alter_table("job_work_issues") as batch:
            for c in issue_cols:
                batch.drop_column(c)
    else:
        for c in bill_cols:
            op.drop_column("job_work_bills", c)
        for c in issue_cols:
            op.drop_column("job_work_issues", c)
