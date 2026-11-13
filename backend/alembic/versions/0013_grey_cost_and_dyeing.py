"""Grey fabric actual-cost fields + dyeing cost tracking (additive).

Adds, additively and reversibly:
* ``production_orders`` grey-cost columns (actual knitting conversion cost, grey
  inventory value, grey_valued_at);
* ``dyeing_jobs`` / ``dyeing_inputs`` / ``dyeing_outputs`` — the chemical-free
  dyeing operational + cost-tracking tables.

Nothing existing is removed; the dye recipe / chemical masters are untouched.

Revision ID: 0013_grey_cost_and_dyeing
Revises: 0012_fabric_bom_unique
Create Date: 2026-11-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0013_grey_cost_and_dyeing"
down_revision = "0012_fabric_bom_unique"
branch_labels = None
depends_on = None

NUM = sa.Numeric(18, 4)


def _has_table(bind, name):
    return sa.inspect(bind).has_table(name)


def _has_column(bind, table, col):
    insp = sa.inspect(bind)
    return insp.has_table(table) and col in {c["name"] for c in insp.get_columns(table)}


def _audit():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
    )


def upgrade() -> None:
    bind = op.get_bind()

    for col, type_ in (
        ("actual_knitting_conversion_cost", NUM),
        ("grey_inventory_value", NUM),
        ("grey_valued_at", sa.DateTime(timezone=True)),
    ):
        if not _has_column(bind, "production_orders", col):
            default = sa.text("0") if col == "grey_inventory_value" else None
            op.add_column("production_orders",
                          sa.Column(col, type_, nullable=True, server_default=default))

    if not _has_table(bind, "dyeing_jobs"):
        op.create_table(
            "dyeing_jobs",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("factory_id", sa.Uuid(), nullable=True),
            sa.Column("number", sa.String(length=40), nullable=False),
            sa.Column("grey_production_order_id", sa.Uuid(), nullable=True),
            sa.Column("sales_order_id", sa.Uuid(), nullable=True),
            sa.Column("customer_id", sa.Uuid(), nullable=True),
            sa.Column("shade_id", sa.Uuid(), nullable=True),
            sa.Column("dye_lot", sa.String(length=40), nullable=True),
            sa.Column("mode", sa.String(length=10), nullable=False, server_default="INTERNAL"),
            sa.Column("jobwork_supplier_id", sa.Uuid(), nullable=True),
            sa.Column("dye_date", sa.Date(), nullable=True),
            sa.Column("input_qty_kg", NUM, nullable=False, server_default="0"),
            sa.Column("output_qty_kg", NUM, nullable=False, server_default="0"),
            sa.Column("process_loss_kg", NUM, nullable=False, server_default="0"),
            sa.Column("rejection_kg", NUM, nullable=False, server_default="0"),
            sa.Column("reprocess_qty_kg", NUM, nullable=False, server_default="0"),
            sa.Column("grey_cost_consumed", NUM, nullable=False, server_default="0"),
            sa.Column("dyeing_charge", NUM, nullable=True),
            sa.Column("jobwork_charge", NUM, nullable=True),
            sa.Column("chemical_cost", NUM, nullable=True),
            sa.Column("finishing_cost", NUM, nullable=True),
            sa.Column("testing_cost", NUM, nullable=True),
            sa.Column("other_cost", NUM, nullable=True),
            sa.Column("reprocess_cost", NUM, nullable=True),
            sa.Column("dyed_inventory_value", NUM, nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=12), nullable=False, server_default="PLANNED"),
            sa.Column("notes", sa.Text(), nullable=True),
            *_audit(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["factory_id"], ["branches.id"]),
            sa.ForeignKeyConstraint(["grey_production_order_id"], ["production_orders.id"]),
            sa.ForeignKeyConstraint(["sales_order_id"], ["sales_orders.id"]),
            sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
            sa.ForeignKeyConstraint(["shade_id"], ["shade_codes.id"]),
            sa.ForeignKeyConstraint(["jobwork_supplier_id"], ["suppliers.id"]),
        )
        op.create_index("ix_dyeing_jobs_company_id", "dyeing_jobs", ["company_id"])
        op.create_index("ix_dyeing_jobs_factory_id", "dyeing_jobs", ["factory_id"])
        op.create_index("ix_dyeing_jobs_status", "dyeing_jobs", ["status"])
        op.create_index("ix_dyeing_jobs_grey_po", "dyeing_jobs", ["grey_production_order_id"])

    if not _has_table(bind, "dyeing_inputs"):
        op.create_table(
            "dyeing_inputs",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("job_id", sa.Uuid(), nullable=False),
            sa.Column("item_type", sa.String(length=10), nullable=False, server_default="FABRIC"),
            sa.Column("item_id", sa.Uuid(), nullable=False),
            sa.Column("source_production_order_id", sa.Uuid(), nullable=True),
            sa.Column("qty_kg", NUM, nullable=False, server_default="0"),
            sa.Column("cost", NUM, nullable=False, server_default="0"),
            sa.Column("posting_reference", sa.String(length=120), nullable=True),
            *_audit(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["job_id"], ["dyeing_jobs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["source_production_order_id"], ["production_orders.id"]),
        )
        op.create_index("ix_dyeing_inputs_job", "dyeing_inputs", ["job_id"])
        op.create_index("ix_dyeing_inputs_item", "dyeing_inputs", ["item_id"])
        op.create_index("ix_dyeing_inputs_src_po", "dyeing_inputs", ["source_production_order_id"])

    if not _has_table(bind, "dyeing_outputs"):
        op.create_table(
            "dyeing_outputs",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("job_id", sa.Uuid(), nullable=False),
            sa.Column("lot_code", sa.String(length=40), nullable=False),
            sa.Column("shade_id", sa.Uuid(), nullable=True),
            sa.Column("qty_kg", NUM, nullable=False, server_default="0"),
            sa.Column("allocated_cost", NUM, nullable=False, server_default="0"),
            sa.Column("dyed_item_id", sa.Uuid(), nullable=True),
            sa.Column("warehouse_id", sa.Uuid(), nullable=True),
            sa.Column("is_allocated", sa.Boolean(), nullable=False, server_default=sa.false()),
            *_audit(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["job_id"], ["dyeing_jobs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["shade_id"], ["shade_codes.id"]),
            sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
        )
        op.create_index("ix_dyeing_outputs_job", "dyeing_outputs", ["job_id"])
        op.create_index("ix_dyeing_outputs_lot", "dyeing_outputs", ["lot_code"])


def downgrade() -> None:
    bind = op.get_bind()
    for t in ("dyeing_outputs", "dyeing_inputs", "dyeing_jobs"):
        if _has_table(bind, t):
            op.drop_table(t)
    for col in ("grey_valued_at", "grey_inventory_value", "actual_knitting_conversion_cost"):
        if _has_column(bind, "production_orders", col):
            with op.batch_alter_table("production_orders") as batch:
                batch.drop_column(col)
