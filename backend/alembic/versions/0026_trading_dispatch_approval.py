"""Trading dispatch approval: FIFO COGS evidence + approval columns (4B-3A.1b.2).

Adds, additively and reversibly:
* approval/COGS/over-dispatch columns on ``trading_dispatches``;
* ``cogs_value`` on ``trading_dispatch_lines``;
* ``trading_dispatch_fifo_allocations`` — per-layer FIFO consumption evidence for
  an approved dispatch (dispatch -> line -> stock layer -> qty/unit/total cost).

Nothing existing is altered beyond additive columns on the (new, Stage-4B-3A.1b.1)
dispatch tables. No inventory layer/transaction, manufactured sales, SalesInvoice
or chemical record is changed; no historical row is backfilled.

Revision ID: 0026_trading_dispatch_approval
Revises: 0025_trading_dispatches
Create Date: 2026-11-18
"""
import sqlalchemy as sa
from alembic import op

from app.core.types import Money, Quantity

revision = "0026_trading_dispatch_approval"
down_revision = "0025_trading_dispatches"
branch_labels = None
depends_on = None


def _has_table(bind, name):
    return sa.inspect(bind).has_table(name)


def _has_column(bind, table, col):
    insp = sa.inspect(bind)
    return insp.has_table(table) and col in {c["name"] for c in insp.get_columns(table)}


_DISPATCH_COLS = [
    ("cogs_value", Money(), True),
    ("posting_reference", sa.String(length=120), True),
    ("approved_by", sa.Uuid(), True),
    ("approved_at", sa.DateTime(timezone=True), True),
    ("is_over_dispatched", sa.Boolean(), False),
    ("over_dispatch_reason", sa.String(length=300), True),
    ("over_dispatched_by", sa.Uuid(), True),
    ("over_dispatched_at", sa.DateTime(timezone=True), True),
]


def upgrade() -> None:
    bind = op.get_bind()

    for name, type_, nullable in _DISPATCH_COLS:
        if _has_table(bind, "trading_dispatches") and not _has_column(bind, "trading_dispatches", name):
            kwargs = {}
            if name == "is_over_dispatched":
                kwargs["server_default"] = sa.false()
            op.add_column("trading_dispatches", sa.Column(name, type_, nullable=nullable, **kwargs))
    if _has_column(bind, "trading_dispatches", "posting_reference"):
        insp = sa.inspect(bind)
        if "ix_trading_dispatch_posting_ref" not in {i["name"] for i in insp.get_indexes("trading_dispatches")}:
            op.create_index("ix_trading_dispatch_posting_ref", "trading_dispatches", ["posting_reference"])

    if _has_table(bind, "trading_dispatch_lines") and not _has_column(bind, "trading_dispatch_lines", "cogs_value"):
        op.add_column("trading_dispatch_lines", sa.Column("cogs_value", Money(), nullable=True))

    if not _has_table(bind, "trading_dispatch_fifo_allocations"):
        op.create_table(
            "trading_dispatch_fifo_allocations",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("dispatch_id", sa.Uuid(), nullable=False),
            sa.Column("dispatch_line_id", sa.Uuid(), nullable=False),
            sa.Column("stock_layer_id", sa.Uuid(), nullable=False),
            sa.Column("source_lot_id", sa.Uuid(), nullable=True),
            sa.Column("quantity_consumed", Quantity(), nullable=True),
            sa.Column("unit_cost", Money(), nullable=True),
            sa.Column("total_cost", Money(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["dispatch_id"], ["trading_dispatches.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["dispatch_line_id"], ["trading_dispatch_lines.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["stock_layer_id"], ["inventory_valuation_layers.id"]),
        )
        op.create_index("ix_trading_dfa_company_id", "trading_dispatch_fifo_allocations", ["company_id"])
        op.create_index("ix_trading_dfa_dispatch_id", "trading_dispatch_fifo_allocations", ["dispatch_id"])
        op.create_index("ix_trading_dfa_line_id", "trading_dispatch_fifo_allocations", ["dispatch_line_id"])
        op.create_index("ix_trading_dfa_layer_id", "trading_dispatch_fifo_allocations", ["stock_layer_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "trading_dispatch_fifo_allocations"):
        op.drop_table("trading_dispatch_fifo_allocations")   # cascades its indexes

    if _has_column(bind, "trading_dispatch_lines", "cogs_value"):
        with op.batch_alter_table("trading_dispatch_lines") as b:
            b.drop_column("cogs_value")

    if _has_table(bind, "trading_dispatches"):
        insp = sa.inspect(bind)
        existing_ix = {i["name"] for i in insp.get_indexes("trading_dispatches")}
        if "ix_trading_dispatch_posting_ref" in existing_ix:
            op.drop_index("ix_trading_dispatch_posting_ref", table_name="trading_dispatches")
        with op.batch_alter_table("trading_dispatches") as b:
            for name, _type, _nullable in reversed(_DISPATCH_COLS):
                if _has_column(bind, "trading_dispatches", name):
                    b.drop_column(name)
