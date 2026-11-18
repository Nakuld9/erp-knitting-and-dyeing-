"""Trading dispatches (Stage 4B-3A.1b.1, additive).

Adds, additively and reversibly:
* ``trading_dispatches`` / ``trading_dispatch_lines`` — aggregate-quantity dispatch
  documents for trading fabric, always against an approved trading sales order.

This is the DRAFT-lifecycle foundation only: these tables carry no COGS, no FIFO
allocation and no posting reference. Creating/submitting/cancelling a dispatch
moves no stock and posts no accounting.

Nothing existing is altered: inventory layers/transactions, manufactured sales,
SalesInvoice, trading sales orders and chemical/manufactured records are
untouched; no historical row is backfilled or reclassified.

Dispatch numbers are unique per company.

Revision ID: 0025_trading_dispatches
Revises: 0024_trading_sales_orders
Create Date: 2026-11-18
"""
import sqlalchemy as sa
from alembic import op

from app.core.types import Quantity

revision = "0025_trading_dispatches"
down_revision = "0024_trading_sales_orders"
branch_labels = None
depends_on = None


def _has_table(bind, name):
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "trading_dispatches"):
        op.create_table(
            "trading_dispatches",
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
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
            sa.ForeignKeyConstraint(["factory_id"], ["branches.id"]),
            sa.ForeignKeyConstraint(["sales_order_id"], ["trading_sales_orders.id"]),
            sa.UniqueConstraint("company_id", "dispatch_number", name="uq_trading_dispatch_company_number"),
        )
        op.create_index("ix_trading_dispatch_company_id", "trading_dispatches", ["company_id"])
        op.create_index("ix_trading_dispatch_customer_id", "trading_dispatches", ["customer_id"])
        op.create_index("ix_trading_dispatch_factory_id", "trading_dispatches", ["factory_id"])
        op.create_index("ix_trading_dispatch_so_id", "trading_dispatches", ["sales_order_id"])
        op.create_index("ix_trading_dispatch_status", "trading_dispatches", ["status"])

    if not _has_table(bind, "trading_dispatch_lines"):
        op.create_table(
            "trading_dispatch_lines",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("dispatch_id", sa.Uuid(), nullable=False),
            sa.Column("line_no", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("trading_item_id", sa.Uuid(), nullable=False),
            sa.Column("sales_order_line_id", sa.Uuid(), nullable=False),
            sa.Column("warehouse_id", sa.Uuid(), nullable=False),
            sa.Column("quantity", Quantity(), nullable=True),
            sa.Column("uom_id", sa.Uuid(), nullable=True),
            sa.Column("description", sa.String(length=200), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["dispatch_id"], ["trading_dispatches.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["trading_item_id"], ["trading_fabric_items.id"]),
            sa.ForeignKeyConstraint(["sales_order_line_id"], ["trading_sales_order_lines.id"]),
            sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
            sa.ForeignKeyConstraint(["uom_id"], ["units_of_measure.id"]),
        )
        op.create_index("ix_trading_dispatch_lines_dispatch_id", "trading_dispatch_lines", ["dispatch_id"])
        op.create_index("ix_trading_dispatch_lines_item_id", "trading_dispatch_lines", ["trading_item_id"])
        op.create_index("ix_trading_dispatch_lines_so_line", "trading_dispatch_lines", ["sales_order_line_id"])
        op.create_index("ix_trading_dispatch_lines_warehouse", "trading_dispatch_lines", ["warehouse_id"])


def downgrade() -> None:
    bind = op.get_bind()
    # drop_table cascades the tables' indexes AND unique constraints; dropping a
    # unique-constraint-backed index explicitly is rejected on PostgreSQL.
    for tbl in ("trading_dispatch_lines", "trading_dispatches"):
        if _has_table(bind, tbl):
            op.drop_table(tbl)
