"""Direct yarn sales foundation (Stage 5B, additive).

Adds the yarn sales-order / dispatch / FIFO-allocation / invoice-allocation tables
(mirroring the trading sales shape but keyed on the yarn SKU and the ORIGINAL
supplier yarn lot), plus:
* nullable ``sales_invoice_lines.yarn_id`` / ``yarn_lot_id`` (yarn invoice lines
  only; NULL for manufactured/trading lines);
* a PARTIAL unique index enforcing company-scoped uniqueness of YARN invoice
  numbers only, leaving manufactured/trading/historical numbers untouched.

Nothing existing is altered in behaviour: no manufactured/trading posting logic,
no inventory/StockLayer/COGS change, no historical backfill, no new yarn lots.

Revision ID: 0028_yarn_sales
Revises: 0027_trading_sales_invoices
Create Date: 2026-11-18
"""
import sqlalchemy as sa
from alembic import op

from app.core.types import Money, Quantity

revision = "0028_yarn_sales"
down_revision = "0027_trading_sales_invoices"
branch_labels = None
depends_on = None


def _has_table(bind, name):
    return sa.inspect(bind).has_table(name)


def _has_column(bind, table, col):
    insp = sa.inspect(bind)
    return insp.has_table(table) and col in {c["name"] for c in insp.get_columns(table)}


def _audit_cols():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
    ]


def upgrade() -> None:
    bind = op.get_bind()

    # --- yarn_sales_orders ---
    if not _has_table(bind, "yarn_sales_orders"):
        op.create_table(
            "yarn_sales_orders",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("customer_id", sa.Uuid(), nullable=False),
            sa.Column("factory_id", sa.Uuid(), nullable=True),
            sa.Column("order_number", sa.String(length=40), nullable=False),
            sa.Column("order_date", sa.Date(), nullable=False),
            sa.Column("expected_dispatch_date", sa.Date(), nullable=True),
            sa.Column("customer_po_ref", sa.String(length=60), nullable=True),
            sa.Column("status", sa.String(length=12), nullable=False, server_default="DRAFT"),
            sa.Column("remarks", sa.String(length=300), nullable=True),
            sa.Column("approved_by", sa.Uuid(), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancelled_by", sa.Uuid(), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
            *_audit_cols(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
            sa.ForeignKeyConstraint(["factory_id"], ["branches.id"]),
            sa.UniqueConstraint("company_id", "order_number", name="uq_yarn_so_company_number"),
        )
        op.create_index("ix_yarn_so_company_id", "yarn_sales_orders", ["company_id"])
        op.create_index("ix_yarn_so_customer_id", "yarn_sales_orders", ["customer_id"])
        op.create_index("ix_yarn_so_factory_id", "yarn_sales_orders", ["factory_id"])
        op.create_index("ix_yarn_so_number", "yarn_sales_orders", ["order_number"])
        op.create_index("ix_yarn_so_status", "yarn_sales_orders", ["status"])

    if not _has_table(bind, "yarn_sales_order_lines"):
        op.create_table(
            "yarn_sales_order_lines",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("order_id", sa.Uuid(), nullable=False),
            sa.Column("line_no", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("yarn_id", sa.Uuid(), nullable=False),
            sa.Column("warehouse_id", sa.Uuid(), nullable=True),
            sa.Column("qty_ordered", Quantity(), nullable=True),
            sa.Column("qty_dispatched", Quantity(), nullable=True),
            sa.Column("qty_cancelled", Quantity(), nullable=True),
            sa.Column("uom_id", sa.Uuid(), nullable=True),
            sa.Column("rate", Money(), nullable=True),
            sa.Column("discount", Money(), nullable=True),
            sa.Column("tax_code_id", sa.Uuid(), nullable=True),
            sa.Column("description", sa.String(length=200), nullable=True),
            *_audit_cols(),
            sa.ForeignKeyConstraint(["order_id"], ["yarn_sales_orders.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["yarn_id"], ["yarn_masters.id"]),
            sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
            sa.ForeignKeyConstraint(["uom_id"], ["units_of_measure.id"]),
            sa.ForeignKeyConstraint(["tax_code_id"], ["tax_codes.id"]),
        )
        op.create_index("ix_yarn_sol_order_id", "yarn_sales_order_lines", ["order_id"])
        op.create_index("ix_yarn_sol_yarn_id", "yarn_sales_order_lines", ["yarn_id"])

    # --- yarn_sales_dispatches ---
    if not _has_table(bind, "yarn_sales_dispatches"):
        op.create_table(
            "yarn_sales_dispatches",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("customer_id", sa.Uuid(), nullable=False),
            sa.Column("factory_id", sa.Uuid(), nullable=True),
            sa.Column("sales_order_id", sa.Uuid(), nullable=False),
            sa.Column("dispatch_number", sa.String(length=40), nullable=False),
            sa.Column("dispatch_date", sa.Date(), nullable=False),
            sa.Column("status", sa.String(length=12), nullable=False, server_default="DRAFT"),
            sa.Column("vehicle_number", sa.String(length=30), nullable=True),
            sa.Column("transporter", sa.String(length=120), nullable=True),
            sa.Column("customer_delivery_ref", sa.String(length=60), nullable=True),
            sa.Column("remarks", sa.String(length=300), nullable=True),
            sa.Column("submitted_by", sa.Uuid(), nullable=True),
            sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancelled_by", sa.Uuid(), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cogs_value", Money(), nullable=True),
            sa.Column("posting_reference", sa.String(length=120), nullable=True),
            sa.Column("approved_by", sa.Uuid(), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("is_over_dispatched", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("over_dispatch_reason", sa.String(length=300), nullable=True),
            sa.Column("over_dispatched_by", sa.Uuid(), nullable=True),
            sa.Column("over_dispatched_at", sa.DateTime(timezone=True), nullable=True),
            *_audit_cols(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
            sa.ForeignKeyConstraint(["factory_id"], ["branches.id"]),
            sa.ForeignKeyConstraint(["sales_order_id"], ["yarn_sales_orders.id"]),
            sa.UniqueConstraint("company_id", "dispatch_number", name="uq_yarn_dispatch_company_number"),
        )
        op.create_index("ix_yarn_disp_company_id", "yarn_sales_dispatches", ["company_id"])
        op.create_index("ix_yarn_disp_customer_id", "yarn_sales_dispatches", ["customer_id"])
        op.create_index("ix_yarn_disp_factory_id", "yarn_sales_dispatches", ["factory_id"])
        op.create_index("ix_yarn_disp_so_id", "yarn_sales_dispatches", ["sales_order_id"])
        op.create_index("ix_yarn_disp_number", "yarn_sales_dispatches", ["dispatch_number"])
        op.create_index("ix_yarn_disp_status", "yarn_sales_dispatches", ["status"])

    if not _has_table(bind, "yarn_sales_dispatch_lines"):
        op.create_table(
            "yarn_sales_dispatch_lines",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("dispatch_id", sa.Uuid(), nullable=False),
            sa.Column("line_no", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("yarn_id", sa.Uuid(), nullable=False),
            sa.Column("sales_order_line_id", sa.Uuid(), nullable=False),
            sa.Column("warehouse_id", sa.Uuid(), nullable=True),
            sa.Column("lot_id", sa.Uuid(), nullable=True),
            sa.Column("quantity", Quantity(), nullable=True),
            sa.Column("uom_id", sa.Uuid(), nullable=True),
            sa.Column("description", sa.String(length=200), nullable=True),
            sa.Column("cogs_value", Money(), nullable=True),
            sa.Column("qty_invoiced", Quantity(), nullable=True),
            *_audit_cols(),
            sa.ForeignKeyConstraint(["dispatch_id"], ["yarn_sales_dispatches.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["yarn_id"], ["yarn_masters.id"]),
            sa.ForeignKeyConstraint(["sales_order_line_id"], ["yarn_sales_order_lines.id"]),
            sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
            sa.ForeignKeyConstraint(["lot_id"], ["yarn_lots.id"]),
            sa.ForeignKeyConstraint(["uom_id"], ["units_of_measure.id"]),
        )
        op.create_index("ix_yarn_displine_dispatch_id", "yarn_sales_dispatch_lines", ["dispatch_id"])
        op.create_index("ix_yarn_displine_yarn_id", "yarn_sales_dispatch_lines", ["yarn_id"])
        op.create_index("ix_yarn_displine_so_line", "yarn_sales_dispatch_lines", ["sales_order_line_id"])
        op.create_index("ix_yarn_displine_lot_id", "yarn_sales_dispatch_lines", ["lot_id"])

    if not _has_table(bind, "yarn_sales_dispatch_fifo_allocations"):
        op.create_table(
            "yarn_sales_dispatch_fifo_allocations",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("dispatch_id", sa.Uuid(), nullable=False),
            sa.Column("dispatch_line_id", sa.Uuid(), nullable=False),
            sa.Column("stock_layer_id", sa.Uuid(), nullable=False),
            sa.Column("source_lot_id", sa.Uuid(), nullable=True),
            sa.Column("quantity_consumed", Quantity(), nullable=True),
            sa.Column("unit_cost", Money(), nullable=True),
            sa.Column("total_cost", Money(), nullable=True),
            *_audit_cols(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["dispatch_id"], ["yarn_sales_dispatches.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["dispatch_line_id"], ["yarn_sales_dispatch_lines.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["stock_layer_id"], ["inventory_valuation_layers.id"]),
        )
        op.create_index("ix_yarn_fifo_company_id", "yarn_sales_dispatch_fifo_allocations", ["company_id"])
        op.create_index("ix_yarn_fifo_dispatch_id", "yarn_sales_dispatch_fifo_allocations", ["dispatch_id"])
        op.create_index("ix_yarn_fifo_dispatch_line", "yarn_sales_dispatch_fifo_allocations", ["dispatch_line_id"])
        op.create_index("ix_yarn_fifo_stock_layer", "yarn_sales_dispatch_fifo_allocations", ["stock_layer_id"])

    if not _has_table(bind, "yarn_dispatch_invoice_allocations"):
        op.create_table(
            "yarn_dispatch_invoice_allocations",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("invoice_id", sa.Uuid(), nullable=False),
            sa.Column("invoice_line_id", sa.Uuid(), nullable=False),
            sa.Column("dispatch_id", sa.Uuid(), nullable=False),
            sa.Column("dispatch_line_id", sa.Uuid(), nullable=False),
            sa.Column("quantity_allocated", Quantity(), nullable=True),
            sa.Column("uom_id", sa.Uuid(), nullable=True),
            sa.Column("status", sa.String(length=10), nullable=False, server_default="ACTIVE"),
            sa.Column("released_by", sa.Uuid(), nullable=True),
            sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
            *_audit_cols(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["invoice_id"], ["sales_invoices.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["invoice_line_id"], ["sales_invoice_lines.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["dispatch_id"], ["yarn_sales_dispatches.id"]),
            sa.ForeignKeyConstraint(["dispatch_line_id"], ["yarn_sales_dispatch_lines.id"]),
        )
        op.create_index("ix_yarn_dia_company_id", "yarn_dispatch_invoice_allocations", ["company_id"])
        op.create_index("ix_yarn_dia_invoice_id", "yarn_dispatch_invoice_allocations", ["invoice_id"])
        op.create_index("ix_yarn_dia_invoice_line", "yarn_dispatch_invoice_allocations", ["invoice_line_id"])
        op.create_index("ix_yarn_dia_dispatch_id", "yarn_dispatch_invoice_allocations", ["dispatch_id"])
        op.create_index("ix_yarn_dia_dispatch_line", "yarn_dispatch_invoice_allocations", ["dispatch_line_id"])
        op.create_index("ix_yarn_dia_status", "yarn_dispatch_invoice_allocations", ["status"])

    # --- additive sales-invoice-line yarn references + YARN partial unique index ---
    for col in ("yarn_id", "yarn_lot_id"):
        if not _has_column(bind, "sales_invoice_lines", col):
            op.add_column("sales_invoice_lines", sa.Column(col, sa.Uuid(), nullable=True))

    if _has_table(bind, "sales_invoices"):
        insp = sa.inspect(bind)
        if "uq_sales_invoice_yarn_number" not in {i["name"] for i in insp.get_indexes("sales_invoices")}:
            op.create_index("uq_sales_invoice_yarn_number", "sales_invoices",
                            ["company_id", "number"], unique=True,
                            sqlite_where=sa.text("source = 'YARN'"),
                            postgresql_where=sa.text("source = 'YARN'"))


def downgrade() -> None:
    bind = op.get_bind()

    if _has_table(bind, "sales_invoices"):
        insp = sa.inspect(bind)
        if "uq_sales_invoice_yarn_number" in {i["name"] for i in insp.get_indexes("sales_invoices")}:
            op.drop_index("uq_sales_invoice_yarn_number", table_name="sales_invoices")

    for col in ("yarn_lot_id", "yarn_id"):
        if _has_column(bind, "sales_invoice_lines", col):
            with op.batch_alter_table("sales_invoice_lines") as b:
                b.drop_column(col)

    for tbl in ("yarn_dispatch_invoice_allocations", "yarn_sales_dispatch_fifo_allocations",
                "yarn_sales_dispatch_lines", "yarn_sales_dispatches",
                "yarn_sales_order_lines", "yarn_sales_orders"):
        if _has_table(bind, tbl):
            op.drop_table(tbl)   # cascades its indexes
