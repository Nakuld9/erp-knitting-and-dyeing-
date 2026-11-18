"""Trading sales orders (Stage 4B-3A.1a, additive).

Adds, additively and reversibly:
* ``trading_sales_orders`` / ``trading_sales_order_lines`` — customer sales orders
  for trading fabric. Creating/approving an order records intent only: NO stock
  movement, NO reservation and NO accounting is implied by these tables.

Nothing existing is altered: manufactured sales orders/invoices, inventory
layers/transactions, StockLayer and chemical/manufactured records are untouched,
and no historical row is backfilled or reclassified.

Order numbers are unique per company.

Revision ID: 0024_trading_sales_orders
Revises: 0023_trading_purchase_grn
Create Date: 2026-11-18
"""
import sqlalchemy as sa
from alembic import op

from app.core.types import Money, Quantity

revision = "0024_trading_sales_orders"
down_revision = "0023_trading_purchase_grn"
branch_labels = None
depends_on = None


def _has_table(bind, name):
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "trading_sales_orders"):
        op.create_table(
            "trading_sales_orders",
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
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
            sa.ForeignKeyConstraint(["factory_id"], ["branches.id"]),
            sa.UniqueConstraint("company_id", "order_number", name="uq_trading_so_company_number"),
        )
        op.create_index("ix_trading_so_company_id", "trading_sales_orders", ["company_id"])
        op.create_index("ix_trading_so_customer_id", "trading_sales_orders", ["customer_id"])
        op.create_index("ix_trading_so_factory_id", "trading_sales_orders", ["factory_id"])
        op.create_index("ix_trading_so_status", "trading_sales_orders", ["status"])

    if not _has_table(bind, "trading_sales_order_lines"):
        op.create_table(
            "trading_sales_order_lines",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("order_id", sa.Uuid(), nullable=False),
            sa.Column("line_no", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("trading_item_id", sa.Uuid(), nullable=False),
            sa.Column("warehouse_id", sa.Uuid(), nullable=False),
            sa.Column("qty_ordered", Quantity(), nullable=True),
            sa.Column("qty_dispatched", Quantity(), nullable=True),
            sa.Column("qty_cancelled", Quantity(), nullable=True),
            sa.Column("uom_id", sa.Uuid(), nullable=True),
            sa.Column("rate", Money(), nullable=True),
            sa.Column("discount", Money(), nullable=True),
            sa.Column("tax_code_id", sa.Uuid(), nullable=True),
            sa.Column("description", sa.String(length=200), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["order_id"], ["trading_sales_orders.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["trading_item_id"], ["trading_fabric_items.id"]),
            sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
            sa.ForeignKeyConstraint(["uom_id"], ["units_of_measure.id"]),
            sa.ForeignKeyConstraint(["tax_code_id"], ["tax_codes.id"]),
        )
        op.create_index("ix_trading_so_lines_order_id", "trading_sales_order_lines", ["order_id"])
        op.create_index("ix_trading_so_lines_item_id", "trading_sales_order_lines", ["trading_item_id"])
        op.create_index("ix_trading_so_lines_warehouse", "trading_sales_order_lines", ["warehouse_id"])


def downgrade() -> None:
    bind = op.get_bind()
    for tbl in ("trading_sales_order_lines", "trading_sales_orders"):
        if _has_table(bind, tbl):
            insp = sa.inspect(bind)
            for ix in insp.get_indexes(tbl):
                op.drop_index(ix["name"], table_name=tbl)
            op.drop_table(tbl)
