"""GST portal / ITC reconciliation & adjustment module (Stage 6F-P5 GST, additive).

Adds ten new tables for the GST portal-import, ITC reconciliation, ITC lifecycle
(defer / carry-forward / reversal / reclaim), versioned GST policy, CA-gated
credit cross-utilisation and electronic-ledger reconciliation:

  gst_return_imports, gst_portal_documents, gst_itc_invoice_status,
  gst_itc_reco_runs, gst_itc_reco_matches, gst_itc_status_history,
  gst_itc_reversal_reclaim, gst_policy_versions, gst_cross_adjustments,
  gst_electronic_ledger_balances.

Purely additive: ten new tables + their indexes. Changes NO existing table — not
the ledger, chart of accounts, GST registers, purchase/trading tables or any
opening (P1-P5) table. No backfill. Every table is company-scoped. Original
imported portal files are retained (``raw_content``) with their SHA-256 for audit;
identical re-imports are rejected by ``uq_gst_import_period_hash``.

Revision ID: 0035_gst_itc_module
Revises: 0034_opening_financial_lines
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from alembic import op

from app.core.types import Money

revision = "0035_gst_itc_module"
down_revision = "0034_opening_financial_lines"
branch_labels = None
depends_on = None

_TABLES = (
    "gst_electronic_ledger_balances", "gst_cross_adjustments", "gst_policy_versions",
    "gst_itc_reversal_reclaim", "gst_itc_status_history", "gst_itc_reco_matches",
    "gst_itc_reco_runs", "gst_itc_invoice_status", "gst_portal_documents",
    "gst_return_imports",
)


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

    if not _has_table(bind, "gst_return_imports"):
        op.create_table(
            "gst_return_imports",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("gstin", sa.String(length=15), nullable=False),
            sa.Column("financial_year", sa.String(length=9), nullable=False),
            sa.Column("return_period", sa.String(length=6), nullable=False),
            sa.Column("return_type", sa.String(length=12), nullable=False),
            sa.Column("source", sa.String(length=16), nullable=False),
            sa.Column("file_name", sa.String(length=255), nullable=False),
            sa.Column("file_hash", sa.String(length=64), nullable=False),
            sa.Column("raw_content", sa.LargeBinary(), nullable=False),
            sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("doc_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=12), nullable=False, server_default="IMPORTED"),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("notes", sa.String(length=255), nullable=True),
            sa.Column("imported_by", sa.Uuid(), nullable=True),
            sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
            *_audit(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.UniqueConstraint("company_id", "return_type", "return_period", "file_hash",
                                name="uq_gst_import_period_hash"),
        )
        op.create_index("ix_gst_imports_company", "gst_return_imports", ["company_id"])
        op.create_index("ix_gst_imports_gstin", "gst_return_imports", ["gstin"])
        op.create_index("ix_gst_imports_period", "gst_return_imports", ["return_period"])
        op.create_index("ix_gst_imports_type", "gst_return_imports", ["return_type"])
        op.create_index("ix_gst_imports_hash", "gst_return_imports", ["file_hash"])
        op.create_index("ix_gst_imports_status", "gst_return_imports", ["status"])

    if not _has_table(bind, "gst_portal_documents"):
        op.create_table(
            "gst_portal_documents",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("import_id", sa.Uuid(), nullable=False),
            sa.Column("return_type", sa.String(length=12), nullable=False),
            sa.Column("return_period", sa.String(length=6), nullable=False),
            sa.Column("category", sa.String(length=10), nullable=False),
            sa.Column("doc_type", sa.String(length=4), nullable=False, server_default="INV"),
            sa.Column("supplier_gstin", sa.String(length=15), nullable=True),
            sa.Column("supplier_name", sa.String(length=160), nullable=True),
            sa.Column("doc_number", sa.String(length=40), nullable=True),
            sa.Column("doc_date", sa.Date(), nullable=True),
            sa.Column("taxable_value", Money(), nullable=True),
            sa.Column("igst", Money(), nullable=True),
            sa.Column("cgst", Money(), nullable=True),
            sa.Column("sgst", Money(), nullable=True),
            sa.Column("cess", Money(), nullable=True),
            sa.Column("total_value", Money(), nullable=True),
            sa.Column("rate", Money(), nullable=True),
            sa.Column("place_of_supply", sa.String(length=40), nullable=True),
            sa.Column("reverse_charge", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("itc_available", sa.Boolean(), nullable=True),
            sa.Column("itc_unavailable_reason", sa.String(length=120), nullable=True),
            sa.Column("is_amendment", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("original_doc_number", sa.String(length=40), nullable=True),
            sa.Column("original_doc_date", sa.Date(), nullable=True),
            sa.Column("raw", sa.Text(), nullable=True),
            *_audit(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["import_id"], ["gst_return_imports.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_gst_docs_company", "gst_portal_documents", ["company_id"])
        op.create_index("ix_gst_docs_import", "gst_portal_documents", ["import_id"])
        op.create_index("ix_gst_docs_type", "gst_portal_documents", ["return_type"])
        op.create_index("ix_gst_docs_period", "gst_portal_documents", ["return_period"])
        op.create_index("ix_gst_docs_supplier", "gst_portal_documents", ["supplier_gstin"])
        op.create_index("ix_gst_docs_number", "gst_portal_documents", ["doc_number"])

    if not _has_table(bind, "gst_itc_invoice_status"):
        op.create_table(
            "gst_itc_invoice_status",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("gstin", sa.String(length=15), nullable=False),
            sa.Column("books_key", sa.String(length=120), nullable=False),
            sa.Column("source_module", sa.String(length=40), nullable=True),
            sa.Column("source_id", sa.String(length=80), nullable=True),
            sa.Column("supplier_id", sa.Uuid(), nullable=True),
            sa.Column("supplier_gstin", sa.String(length=15), nullable=True),
            sa.Column("supplier_invoice_no", sa.String(length=40), nullable=True),
            sa.Column("invoice_date", sa.Date(), nullable=True),
            sa.Column("doc_type", sa.String(length=4), nullable=False, server_default="INV"),
            sa.Column("booked_period", sa.String(length=6), nullable=False),
            sa.Column("taxable_value", Money(), nullable=True),
            sa.Column("igst", Money(), nullable=True),
            sa.Column("cgst", Money(), nullable=True),
            sa.Column("sgst", Money(), nullable=True),
            sa.Column("cess", Money(), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="BOOKED_NOT_IN_2B"),
            sa.Column("matched_period", sa.String(length=6), nullable=True),
            sa.Column("claim_period", sa.String(length=6), nullable=True),
            sa.Column("deferred_reason", sa.String(length=255), nullable=True),
            sa.Column("eligibility_note", sa.String(length=255), nullable=True),
            sa.Column("matched_document_id", sa.Uuid(), nullable=True),
            sa.Column("reversed_total", Money(), nullable=True),
            sa.Column("reclaimed_total", Money(), nullable=True),
            sa.Column("last_reco_run_id", sa.Uuid(), nullable=True),
            *_audit(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"]),
            sa.UniqueConstraint("company_id", "books_key", name="uq_gst_itc_books_key"),
        )
        op.create_index("ix_gst_itc_company", "gst_itc_invoice_status", ["company_id"])
        op.create_index("ix_gst_itc_gstin", "gst_itc_invoice_status", ["gstin"])
        op.create_index("ix_gst_itc_bookskey", "gst_itc_invoice_status", ["books_key"])
        op.create_index("ix_gst_itc_supplier", "gst_itc_invoice_status", ["supplier_id"])
        op.create_index("ix_gst_itc_supgstin", "gst_itc_invoice_status", ["supplier_gstin"])
        op.create_index("ix_gst_itc_invno", "gst_itc_invoice_status", ["supplier_invoice_no"])
        op.create_index("ix_gst_itc_booked", "gst_itc_invoice_status", ["booked_period"])
        op.create_index("ix_gst_itc_status", "gst_itc_invoice_status", ["status"])
        op.create_index("ix_gst_itc_matched", "gst_itc_invoice_status", ["matched_period"])

    if not _has_table(bind, "gst_itc_reco_runs"):
        op.create_table(
            "gst_itc_reco_runs",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("gstin", sa.String(length=15), nullable=False),
            sa.Column("return_type", sa.String(length=12), nullable=False),
            sa.Column("return_period", sa.String(length=6), nullable=False),
            sa.Column("import_id", sa.Uuid(), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("run_by", sa.Uuid(), nullable=True),
            sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
            *_audit(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["import_id"], ["gst_return_imports.id"], ondelete="SET NULL"),
        )
        op.create_index("ix_gst_reco_company", "gst_itc_reco_runs", ["company_id"])
        op.create_index("ix_gst_reco_type", "gst_itc_reco_runs", ["return_type"])
        op.create_index("ix_gst_reco_period", "gst_itc_reco_runs", ["return_period"])

    if not _has_table(bind, "gst_itc_reco_matches"):
        op.create_table(
            "gst_itc_reco_matches",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("run_id", sa.Uuid(), nullable=False),
            sa.Column("return_period", sa.String(length=6), nullable=False),
            sa.Column("invoice_status_id", sa.Uuid(), nullable=True),
            sa.Column("portal_document_id", sa.Uuid(), nullable=True),
            sa.Column("supplier_gstin", sa.String(length=15), nullable=True),
            sa.Column("doc_number", sa.String(length=40), nullable=True),
            sa.Column("match_type", sa.String(length=24), nullable=False),
            sa.Column("diff", sa.Text(), nullable=True),
            sa.Column("resolution_status", sa.String(length=12), nullable=False,
                      server_default="UNRESOLVED"),
            sa.Column("resolution_remark", sa.String(length=500), nullable=True),
            sa.Column("resolved_by", sa.Uuid(), nullable=True),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            *_audit(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["run_id"], ["gst_itc_reco_runs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["invoice_status_id"], ["gst_itc_invoice_status.id"],
                                    ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["portal_document_id"], ["gst_portal_documents.id"],
                                    ondelete="SET NULL"),
        )
        op.create_index("ix_gst_match_company", "gst_itc_reco_matches", ["company_id"])
        op.create_index("ix_gst_match_run", "gst_itc_reco_matches", ["run_id"])
        op.create_index("ix_gst_match_period", "gst_itc_reco_matches", ["return_period"])
        op.create_index("ix_gst_match_inv", "gst_itc_reco_matches", ["invoice_status_id"])
        op.create_index("ix_gst_match_doc", "gst_itc_reco_matches", ["portal_document_id"])
        op.create_index("ix_gst_match_supgstin", "gst_itc_reco_matches", ["supplier_gstin"])
        op.create_index("ix_gst_match_type", "gst_itc_reco_matches", ["match_type"])
        op.create_index("ix_gst_match_resolution", "gst_itc_reco_matches", ["resolution_status"])

    if not _has_table(bind, "gst_itc_status_history"):
        op.create_table(
            "gst_itc_status_history",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("invoice_status_id", sa.Uuid(), nullable=False),
            sa.Column("from_status", sa.String(length=24), nullable=True),
            sa.Column("to_status", sa.String(length=24), nullable=False),
            sa.Column("period", sa.String(length=6), nullable=True),
            sa.Column("reason", sa.String(length=500), nullable=True),
            sa.Column("reference", sa.String(length=120), nullable=True),
            sa.Column("voucher_id", sa.Uuid(), nullable=True),
            sa.Column("actor_id", sa.Uuid(), nullable=True),
            sa.Column("at", sa.DateTime(timezone=True), nullable=False),
            *_audit(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["invoice_status_id"], ["gst_itc_invoice_status.id"],
                                    ondelete="CASCADE"),
        )
        op.create_index("ix_gst_hist_company", "gst_itc_status_history", ["company_id"])
        op.create_index("ix_gst_hist_inv", "gst_itc_status_history", ["invoice_status_id"])

    if not _has_table(bind, "gst_itc_reversal_reclaim"):
        op.create_table(
            "gst_itc_reversal_reclaim",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("invoice_status_id", sa.Uuid(), nullable=False),
            sa.Column("action", sa.String(length=10), nullable=False),
            sa.Column("tax_period", sa.String(length=6), nullable=False),
            sa.Column("igst", Money(), nullable=True),
            sa.Column("cgst", Money(), nullable=True),
            sa.Column("sgst", Money(), nullable=True),
            sa.Column("cess", Money(), nullable=True),
            sa.Column("is_partial", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("reason", sa.String(length=500), nullable=True),
            sa.Column("reference", sa.String(length=120), nullable=False),
            sa.Column("voucher_id", sa.Uuid(), nullable=True),
            sa.Column("linked_reversal_id", sa.Uuid(), nullable=True),
            sa.Column("created_at_actor", sa.Uuid(), nullable=True),
            sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
            *_audit(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["invoice_status_id"], ["gst_itc_invoice_status.id"],
                                    ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["linked_reversal_id"], ["gst_itc_reversal_reclaim.id"]),
            sa.UniqueConstraint("company_id", "reference", name="uq_gst_itc_rr_reference"),
        )
        op.create_index("ix_gst_rr_company", "gst_itc_reversal_reclaim", ["company_id"])
        op.create_index("ix_gst_rr_inv", "gst_itc_reversal_reclaim", ["invoice_status_id"])
        op.create_index("ix_gst_rr_action", "gst_itc_reversal_reclaim", ["action"])
        op.create_index("ix_gst_rr_period", "gst_itc_reversal_reclaim", ["tax_period"])
        op.create_index("ix_gst_rr_reference", "gst_itc_reversal_reclaim", ["reference"])

    if not _has_table(bind, "gst_policy_versions"):
        op.create_table(
            "gst_policy_versions",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("version_no", sa.Integer(), nullable=False),
            sa.Column("effective_from", sa.Date(), nullable=True),
            sa.Column("rules", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=12), nullable=False, server_default="DRAFT"),
            sa.Column("title", sa.String(length=160), nullable=True),
            sa.Column("approved_by", sa.Uuid(), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("approver_name", sa.String(length=120), nullable=True),
            *_audit(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.UniqueConstraint("company_id", "version_no", name="uq_gst_policy_version"),
        )
        op.create_index("ix_gst_policy_company", "gst_policy_versions", ["company_id"])
        op.create_index("ix_gst_policy_status", "gst_policy_versions", ["status"])

    if not _has_table(bind, "gst_cross_adjustments"):
        op.create_table(
            "gst_cross_adjustments",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("tax_period", sa.String(length=6), nullable=False),
            sa.Column("policy_version_id", sa.Uuid(), nullable=True),
            sa.Column("posting_reference", sa.String(length=120), nullable=False),
            sa.Column("proposal", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=12), nullable=False, server_default="PROPOSED"),
            sa.Column("ca_approved_by", sa.Uuid(), nullable=True),
            sa.Column("ca_approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("ca_approver_name", sa.String(length=120), nullable=True),
            sa.Column("posted_voucher_id", sa.Uuid(), nullable=True),
            sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at_actor", sa.Uuid(), nullable=True),
            *_audit(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["policy_version_id"], ["gst_policy_versions.id"]),
            sa.UniqueConstraint("company_id", "posting_reference", name="uq_gst_crossadj_ref"),
        )
        op.create_index("ix_gst_crossadj_company", "gst_cross_adjustments", ["company_id"])
        op.create_index("ix_gst_crossadj_period", "gst_cross_adjustments", ["tax_period"])
        op.create_index("ix_gst_crossadj_status", "gst_cross_adjustments", ["status"])

    if not _has_table(bind, "gst_electronic_ledger_balances"):
        op.create_table(
            "gst_electronic_ledger_balances",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("import_id", sa.Uuid(), nullable=True),
            sa.Column("ledger_type", sa.String(length=12), nullable=False),
            sa.Column("return_period", sa.String(length=6), nullable=True),
            sa.Column("as_of_date", sa.Date(), nullable=True),
            sa.Column("head", sa.String(length=12), nullable=False),
            sa.Column("opening_balance", Money(), nullable=True),
            sa.Column("credit", Money(), nullable=True),
            sa.Column("debit", Money(), nullable=True),
            sa.Column("closing_balance", Money(), nullable=True),
            sa.Column("raw", sa.Text(), nullable=True),
            *_audit(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["import_id"], ["gst_return_imports.id"], ondelete="SET NULL"),
        )
        op.create_index("ix_gst_ecl_company", "gst_electronic_ledger_balances", ["company_id"])
        op.create_index("ix_gst_ecl_import", "gst_electronic_ledger_balances", ["import_id"])
        op.create_index("ix_gst_ecl_type", "gst_electronic_ledger_balances", ["ledger_type"])
        op.create_index("ix_gst_ecl_period", "gst_electronic_ledger_balances", ["return_period"])


def downgrade() -> None:
    bind = op.get_bind()
    for tbl in _TABLES:
        if _has_table(bind, tbl):
            op.drop_table(tbl)
