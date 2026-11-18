"""Trading purchase orders + goods receipts (Stage 4B-2, additive).

Adds, additively and reversibly, four tables for the trading-fabric purchase and
goods-receipt workflow:
* ``trading_purchase_orders`` / ``trading_purchase_order_lines``
* ``trading_goods_receipt_notes`` / ``trading_goods_receipt_lines``

Nothing existing is altered: chemical purchase orders, chemical GRNs,
manufactured-fabric records, stock layers and inventory transactions are left
untouched, and no historical row is backfilled or reclassified.

Supplier-invoice duplicate protection is a unique constraint on
(company_id, supplier_id, supplier_invoice_no); GRN and PO numbers are unique per
company.

Revision ID: 0023_trading_purchase_grn
Revises: 0022_trading_fabric_items
Create Date: 2026-11-15
"""
import sqlalchemy as sa
from alembic import op

from app.core.types import Money, Quantity

revision = "0023_trading_purchase_grn"
down_revision = "0022_trading_fabric_items"
branch_labels = None
depends_on = None


def _has_table(bind, name):
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "trading_purchase_orders"):
        op.create_table(
            "trading_purchase_orders",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("supplier_id", sa.Uuid(), nullable=False),
            sa.Column("factory_id", sa.Uuid(), nullable=True),
            sa.Column("number", sa.String(length=40), nullable=False),
            sa.Column("order_date", sa.Date(), nullable=False),
            sa.Column("expected_date", sa.Date(), nullable=True),
            sa.Column("status", sa.String(length=12), nullable=False, server_default="DRAFT"),
            sa.Column("remarks", sa.String(length=300), nullable=True),
            sa.Column("approved_by", sa.Uuid(), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancelled_by", sa.Uuid(), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"]),
            sa.ForeignKeyConstraint(["factory_id"], ["branches.id"]),
            sa.UniqueConstraint("company_id", "number", name="uq_trading_po_company_number"),
        )
        op.create_index("ix_trading_po_company_id", "trading_purchase_orders", ["company_id"])
        op.create_index("ix_trading_po_supplier_id", "trading_purchase_orders", ["supplier_id"])
        op.create_index("ix_trading_po_factory_id", "trading_purchase_orders", ["factory_id"])
        op.create_index("ix_trading_po_status", "trading_purchase_orders", ["status"])

    if not _has_table(bind, "trading_purchase_order_lines"):
        op.create_table(
            "trading_purchase_order_lines",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("order_id", sa.Uuid(), nullable=False),
            sa.Column("line_no", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("trading_item_id", sa.Uuid(), nullable=False),
            sa.Column("qty_ordered", Quantity(), nullable=True),
            sa.Column("qty_received", Quantity(), nullable=True),
            sa.Column("qty_cancelled", Quantity(), nullable=True),
            sa.Column("uom_id", sa.Uuid(), nullable=True),
            sa.Column("rate", Money(), nullable=True),
            sa.Column("tax_code_id", sa.Uuid(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["order_id"], ["trading_purchase_orders.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["trading_item_id"], ["trading_fabric_items.id"]),
            sa.ForeignKeyConstraint(["uom_id"], ["units_of_measure.id"]),
            sa.ForeignKeyConstraint(["tax_code_id"], ["tax_codes.id"]),
        )
        op.create_index("ix_trading_po_lines_order_id", "trading_purchase_order_lines", ["order_id"])
        op.create_index("ix_trading_po_lines_item_id", "trading_purchase_order_lines", ["trading_item_id"])

    if not _has_table(bind, "trading_goods_receipt_notes"):
        op.create_table(
            "trading_goods_receipt_notes",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("supplier_id", sa.Uuid(), nullable=False),
            sa.Column("factory_id", sa.Uuid(), nullable=True),
            sa.Column("purchase_order_id", sa.Uuid(), nullable=True),
            sa.Column("source_type", sa.String(length=8), nullable=False, server_default="DIRECT"),
            sa.Column("number", sa.String(length=40), nullable=False),
            sa.Column("grn_date", sa.Date(), nullable=False),
            sa.Column("supplier_invoice_no", sa.String(length=40), nullable=False),
            sa.Column("supplier_invoice_date", sa.Date(), nullable=True),
            sa.Column("status", sa.String(length=12), nullable=False, server_default="DRAFT"),
            sa.Column("remarks", sa.Text(), nullable=True),
            sa.Column("posting_reference", sa.String(length=120), nullable=True),
            sa.Column("taxable_value", Money(), nullable=True),
            sa.Column("tax_amount", Money(), nullable=True),
            sa.Column("total_value", Money(), nullable=True),
            sa.Column("submitted_by", sa.Uuid(), nullable=True),
            sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("approved_by", sa.Uuid(), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rejected_by", sa.Uuid(), nullable=True),
            sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("reject_reason", sa.String(length=300), nullable=True),
            sa.Column("cancelled_by", sa.Uuid(), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("is_over_received", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("over_receive_reason", sa.String(length=300), nullable=True),
            sa.Column("over_received_by", sa.Uuid(), nullable=True),
            sa.Column("over_received_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"]),
            sa.ForeignKeyConstraint(["factory_id"], ["branches.id"]),
            sa.ForeignKeyConstraint(["purchase_order_id"], ["trading_purchase_orders.id"]),
            sa.UniqueConstraint("company_id", "number", name="uq_trading_grn_company_number"),
            sa.UniqueConstraint("company_id", "supplier_id", "supplier_invoice_no",
                                name="uq_trading_grn_supplier_invoice"),
        )
        op.create_index("ix_trading_grn_company_id", "trading_goods_receipt_notes", ["company_id"])
        op.create_index("ix_trading_grn_supplier_id", "trading_goods_receipt_notes", ["supplier_id"])
        op.create_index("ix_trading_grn_factory_id", "trading_goods_receipt_notes", ["factory_id"])
        op.create_index("ix_trading_grn_po_id", "trading_goods_receipt_notes", ["purchase_order_id"])
        op.create_index("ix_trading_grn_status", "trading_goods_receipt_notes", ["status"])
        op.create_index("ix_trading_grn_posting_ref", "trading_goods_receipt_notes", ["posting_reference"])

    if not _has_table(bind, "trading_goods_receipt_lines"):
        op.create_table(
            "trading_goods_receipt_lines",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("grn_id", sa.Uuid(), nullable=False),
            sa.Column("line_no", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("trading_item_id", sa.Uuid(), nullable=False),
            sa.Column("purchase_order_line_id", sa.Uuid(), nullable=True),
            sa.Column("warehouse_id", sa.Uuid(), nullable=False),
            sa.Column("qty_received", Quantity(), nullable=True),
            sa.Column("qty_rejected", Quantity(), nullable=True),
            sa.Column("uom_id", sa.Uuid(), nullable=True),
            sa.Column("rate", Money(), nullable=True),
            sa.Column("tax_code_id", sa.Uuid(), nullable=True),
            sa.Column("taxable_value", Money(), nullable=True),
            sa.Column("cgst", Money(), nullable=True),
            sa.Column("sgst", Money(), nullable=True),
            sa.Column("igst", Money(), nullable=True),
            sa.Column("total_value", Money(), nullable=True),
            sa.Column("remarks", sa.String(length=200), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["grn_id"], ["trading_goods_receipt_notes.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["trading_item_id"], ["trading_fabric_items.id"]),
            sa.ForeignKeyConstraint(["purchase_order_line_id"], ["trading_purchase_order_lines.id"]),
            sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
            sa.ForeignKeyConstraint(["uom_id"], ["units_of_measure.id"]),
            sa.ForeignKeyConstraint(["tax_code_id"], ["tax_codes.id"]),
        )
        op.create_index("ix_trading_grn_lines_grn_id", "trading_goods_receipt_lines", ["grn_id"])
        op.create_index("ix_trading_grn_lines_item_id", "trading_goods_receipt_lines", ["trading_item_id"])
        op.create_index("ix_trading_grn_lines_po_line", "trading_goods_receipt_lines", ["purchase_order_line_id"])
        op.create_index("ix_trading_grn_lines_warehouse", "trading_goods_receipt_lines", ["warehouse_id"])


def downgrade() -> None:
    bind = op.get_bind()
    for tbl in ("trading_goods_receipt_lines", "trading_goods_receipt_notes",
                "trading_purchase_order_lines", "trading_purchase_orders"):
        if _has_table(bind, tbl):
            insp = sa.inspect(bind)
            for ix in insp.get_indexes(tbl):
                op.drop_index(ix["name"], table_name=tbl)
            op.drop_table(tbl)
