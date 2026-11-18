"""Trading sales invoice foundation (Stage 4B-3A.1c.1, additive).

Approach C — reuse the shared SalesInvoice/SalesInvoiceLine register additively:
* ``sales_invoices.source`` (stream discriminator; historical rows stay NULL) and
  nullable ``sales_invoices.factory_id``;
* ``sales_invoice_lines.trading_item_id`` / ``discount`` / ``uom_id`` (trading only;
  NULL for manufactured lines);
* ``trading_dispatch_lines.qty_invoiced`` (cumulative counter);
* ``trading_dispatch_invoice_allocations`` — quantity-level invoice<->dispatch link;
* a PARTIAL unique index enforcing company-scoped uniqueness of TRADING invoice
  numbers only, leaving manufactured/historical numbers untouched.

Nothing existing is altered in behaviour: no manufactured invoice posting logic,
no inventory/StockLayer/COGS change, no historical backfill.

Revision ID: 0027_trading_sales_invoices
Revises: 0026_trading_dispatch_approval
Create Date: 2026-11-18
"""
import sqlalchemy as sa
from alembic import op

from app.core.types import Money, Quantity

revision = "0027_trading_sales_invoices"
down_revision = "0026_trading_dispatch_approval"
branch_labels = None
depends_on = None


def _has_table(bind, name):
    return sa.inspect(bind).has_table(name)


def _has_column(bind, table, col):
    insp = sa.inspect(bind)
    return insp.has_table(table) and col in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column(bind, "sales_invoices", "source"):
        op.add_column("sales_invoices", sa.Column("source", sa.String(length=16), nullable=True))
    if not _has_column(bind, "sales_invoices", "factory_id"):
        op.add_column("sales_invoices", sa.Column("factory_id", sa.Uuid(), nullable=True))
    if _has_column(bind, "sales_invoices", "factory_id"):
        insp = sa.inspect(bind)
        if "ix_sales_invoices_factory_id" not in {i["name"] for i in insp.get_indexes("sales_invoices")}:
            op.create_index("ix_sales_invoices_factory_id", "sales_invoices", ["factory_id"])
        # Partial unique index: trading invoice numbers unique per company; manufactured
        # and historical (source NULL) rows are excluded, so existing duplicates are safe.
        if "uq_sales_invoice_trading_number" not in {i["name"] for i in insp.get_indexes("sales_invoices")}:
            op.create_index("uq_sales_invoice_trading_number", "sales_invoices",
                            ["company_id", "number"], unique=True,
                            sqlite_where=sa.text("source = 'TRADING'"),
                            postgresql_where=sa.text("source = 'TRADING'"))

    for col, type_ in [("trading_item_id", sa.Uuid()), ("discount", Money()), ("uom_id", sa.Uuid())]:
        if not _has_column(bind, "sales_invoice_lines", col):
            op.add_column("sales_invoice_lines", sa.Column(col, type_, nullable=True))

    if not _has_column(bind, "trading_dispatch_lines", "qty_invoiced"):
        op.add_column("trading_dispatch_lines", sa.Column("qty_invoiced", Quantity(), nullable=True))

    if not _has_table(bind, "trading_dispatch_invoice_allocations"):
        op.create_table(
            "trading_dispatch_invoice_allocations",
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
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["invoice_id"], ["sales_invoices.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["invoice_line_id"], ["sales_invoice_lines.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["dispatch_id"], ["trading_dispatches.id"]),
            sa.ForeignKeyConstraint(["dispatch_line_id"], ["trading_dispatch_lines.id"]),
        )
        op.create_index("ix_trading_dia_company_id", "trading_dispatch_invoice_allocations", ["company_id"])
        op.create_index("ix_trading_dia_invoice_id", "trading_dispatch_invoice_allocations", ["invoice_id"])
        op.create_index("ix_trading_dia_invoice_line", "trading_dispatch_invoice_allocations", ["invoice_line_id"])
        op.create_index("ix_trading_dia_dispatch_id", "trading_dispatch_invoice_allocations", ["dispatch_id"])
        op.create_index("ix_trading_dia_dispatch_line", "trading_dispatch_invoice_allocations", ["dispatch_line_id"])
        op.create_index("ix_trading_dia_status", "trading_dispatch_invoice_allocations", ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "trading_dispatch_invoice_allocations"):
        op.drop_table("trading_dispatch_invoice_allocations")   # cascades its indexes

    if _has_column(bind, "trading_dispatch_lines", "qty_invoiced"):
        with op.batch_alter_table("trading_dispatch_lines") as b:
            b.drop_column("qty_invoiced")

    for col in ("uom_id", "discount", "trading_item_id"):
        if _has_column(bind, "sales_invoice_lines", col):
            with op.batch_alter_table("sales_invoice_lines") as b:
                b.drop_column(col)

    if _has_table(bind, "sales_invoices"):
        insp = sa.inspect(bind)
        ix = {i["name"] for i in insp.get_indexes("sales_invoices")}
        if "uq_sales_invoice_trading_number" in ix:
            op.drop_index("uq_sales_invoice_trading_number", table_name="sales_invoices")
        if "ix_sales_invoices_factory_id" in ix:
            op.drop_index("ix_sales_invoices_factory_id", table_name="sales_invoices")
        for col in ("factory_id", "source"):
            if _has_column(bind, "sales_invoices", col):
                with op.batch_alter_table("sales_invoices") as b:
                    b.drop_column(col)
