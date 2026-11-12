"""Fabric BOM (version-controlled) + production orders (additive).

Adds the fabric Bill-of-Materials tables (parent + versioned revisions + yarn /
knitting / finishing children) and the production-integration tables
(production orders, reservations, consumptions). Also adds a nullable
``production_order_id`` link to the knitting / dyeing / finishing actuals.

Everything is additive and non-destructive; downgrade drops only what it added.

Revision ID: 0011_fabric_bom
Revises: 0010_intercompany_code_based
Create Date: 2026-11-12
"""
import sqlalchemy as sa
from alembic import op

revision = "0011_fabric_bom"
down_revision = "0010_intercompany_code_based"
branch_labels = None
depends_on = None

NUM = sa.Numeric(18, 4)


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _has_column(bind, table: str, col: str) -> bool:
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return False
    return col in {c["name"] for c in insp.get_columns(table)}


def _audit_cols():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
    )


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "fabric_boms"):
        op.create_table(
            "fabric_boms",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("code", sa.String(length=40), nullable=False),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("fabric_type_id", sa.Uuid(), nullable=True),
            sa.Column("customer_id", sa.Uuid(), nullable=True),
            sa.Column("shade_id", sa.Uuid(), nullable=True),
            sa.Column("finished_quality", sa.String(length=80), nullable=True),
            sa.Column("uom_id", sa.Uuid(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            *_audit_cols(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["fabric_type_id"], ["fabric_types.id"]),
            sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
            sa.ForeignKeyConstraint(["shade_id"], ["shade_codes.id"]),
            sa.ForeignKeyConstraint(["uom_id"], ["units_of_measure.id"]),
        )
        op.create_index("ix_fabric_boms_company_id", "fabric_boms", ["company_id"])
        op.create_index("ix_fabric_boms_code", "fabric_boms", ["code"])

    if not _has_table(bind, "fabric_bom_versions"):
        op.create_table(
            "fabric_bom_versions",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("bom_id", sa.Uuid(), nullable=False),
            sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(length=12), nullable=False, server_default="DRAFT"),
            sa.Column("effective_from", sa.Date(), nullable=True),
            sa.Column("effective_to", sa.Date(), nullable=True),
            sa.Column("approved_by", sa.Uuid(), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revision_notes", sa.Text(), nullable=True),
            sa.Column("supersedes_id", sa.Uuid(), nullable=True),
            sa.Column("construction", sa.String(length=120), nullable=True),
            sa.Column("composition", sa.String(length=160), nullable=True),
            sa.Column("yarn_count", sa.String(length=60), nullable=True),
            sa.Column("gsm", NUM, nullable=True),
            sa.Column("width_cm", NUM, nullable=True),
            sa.Column("gauge", NUM, nullable=True),
            sa.Column("diameter_inch", NUM, nullable=True),
            sa.Column("stitch_length_mm", NUM, nullable=True),
            sa.Column("finished_gsm", NUM, nullable=True),
            sa.Column("finished_width_cm", NUM, nullable=True),
            sa.Column("dye_recipe_version_id", sa.Uuid(), nullable=True),
            sa.Column("std_knitting_cost", NUM, nullable=False, server_default="0"),
            sa.Column("std_dyeing_cost", NUM, nullable=False, server_default="0"),
            sa.Column("std_finishing_cost", NUM, nullable=False, server_default="0"),
            sa.Column("std_labour_cost", NUM, nullable=False, server_default="0"),
            sa.Column("std_overhead_cost", NUM, nullable=False, server_default="0"),
            sa.Column("std_yarn_cost", NUM, nullable=False, server_default="0"),
            sa.Column("std_dye_chem_cost", NUM, nullable=False, server_default="0"),
            sa.Column("std_total_cost_per_kg", NUM, nullable=False, server_default="0"),
            sa.Column("cost_rolled_at", sa.DateTime(timezone=True), nullable=True),
            *_audit_cols(),
            sa.ForeignKeyConstraint(["bom_id"], ["fabric_boms.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["supersedes_id"], ["fabric_bom_versions.id"]),
            sa.ForeignKeyConstraint(["dye_recipe_version_id"], ["recipe_versions.id"]),
        )
        op.create_index("ix_fabric_bom_versions_bom_id", "fabric_bom_versions", ["bom_id"])
        op.create_index("ix_fabric_bom_versions_status", "fabric_bom_versions", ["status"])

    if not _has_table(bind, "fabric_bom_yarns"):
        op.create_table(
            "fabric_bom_yarns",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("version_id", sa.Uuid(), nullable=False),
            sa.Column("yarn_id", sa.Uuid(), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("blend_percent", NUM, nullable=False, server_default="0"),
            sa.Column("feeder_allocation", sa.String(length=60), nullable=True),
            sa.Column("std_consumption_per_kg", NUM, nullable=False, server_default="0"),
            sa.Column("wastage_percent", NUM, nullable=False, server_default="0"),
            sa.Column("process_loss_percent", NUM, nullable=False, server_default="0"),
            sa.Column("std_unit_cost", NUM, nullable=False, server_default="0"),
            *_audit_cols(),
            sa.ForeignKeyConstraint(["version_id"], ["fabric_bom_versions.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["yarn_id"], ["yarn_masters.id"]),
        )
        op.create_index("ix_fabric_bom_yarns_version_id", "fabric_bom_yarns", ["version_id"])

    if not _has_table(bind, "fabric_bom_knitting"):
        op.create_table(
            "fabric_bom_knitting",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("version_id", sa.Uuid(), nullable=False),
            sa.Column("machine_type", sa.String(length=12), nullable=True),
            sa.Column("machine_id", sa.Uuid(), nullable=True),
            sa.Column("gauge", NUM, nullable=True),
            sa.Column("diameter_inch", NUM, nullable=True),
            sa.Column("feeders", sa.Integer(), nullable=True),
            sa.Column("stitch_length_mm", NUM, nullable=True),
            sa.Column("rpm", sa.Integer(), nullable=True),
            sa.Column("efficiency_percent", NUM, nullable=True),
            sa.Column("expected_output_kg_per_shift", NUM, nullable=True),
            sa.Column("settings", sa.Text(), nullable=True),
            *_audit_cols(),
            sa.ForeignKeyConstraint(["version_id"], ["fabric_bom_versions.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["machine_id"], ["machines.id"]),
            sa.UniqueConstraint("version_id", name="uq_fabric_bom_knitting_version"),
        )
        op.create_index("ix_fabric_bom_knitting_version_id", "fabric_bom_knitting", ["version_id"])

    if not _has_table(bind, "fabric_bom_finishing"):
        op.create_table(
            "fabric_bom_finishing",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("version_id", sa.Uuid(), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("process_type", sa.String(length=16), nullable=False),
            sa.Column("machine_type", sa.String(length=12), nullable=True),
            sa.Column("target_gsm", NUM, nullable=True),
            sa.Column("target_width_cm", NUM, nullable=True),
            sa.Column("params", sa.Text(), nullable=True),
            sa.Column("std_cost_per_kg", NUM, nullable=False, server_default="0"),
            *_audit_cols(),
            sa.ForeignKeyConstraint(["version_id"], ["fabric_bom_versions.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_fabric_bom_finishing_version_id", "fabric_bom_finishing", ["version_id"])

    if not _has_table(bind, "production_orders"):
        op.create_table(
            "production_orders",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("factory_id", sa.Uuid(), nullable=True),
            sa.Column("number", sa.String(length=40), nullable=False),
            sa.Column("bom_version_id", sa.Uuid(), nullable=False),
            sa.Column("sales_order_id", sa.Uuid(), nullable=True),
            sa.Column("shade_id", sa.Uuid(), nullable=True),
            sa.Column("planned_qty_kg", NUM, nullable=False, server_default="0"),
            sa.Column("produced_qty_kg", NUM, nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=12), nullable=False, server_default="PLANNED"),
            sa.Column("planned_start", sa.Date(), nullable=True),
            sa.Column("planned_end", sa.Date(), nullable=True),
            sa.Column("warehouse_id", sa.Uuid(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            *_audit_cols(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["factory_id"], ["branches.id"]),
            sa.ForeignKeyConstraint(["bom_version_id"], ["fabric_bom_versions.id"]),
            sa.ForeignKeyConstraint(["sales_order_id"], ["sales_orders.id"]),
            sa.ForeignKeyConstraint(["shade_id"], ["shade_codes.id"]),
            sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
        )
        op.create_index("ix_production_orders_company_id", "production_orders", ["company_id"])
        op.create_index("ix_production_orders_factory_id", "production_orders", ["factory_id"])
        op.create_index("ix_production_orders_status", "production_orders", ["status"])
        op.create_index("ix_production_orders_bom_version_id", "production_orders", ["bom_version_id"])

    if not _has_table(bind, "production_reservations"):
        op.create_table(
            "production_reservations",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("production_order_id", sa.Uuid(), nullable=False),
            sa.Column("stage", sa.String(length=12), nullable=False),
            sa.Column("item_type", sa.String(length=10), nullable=False),
            sa.Column("item_id", sa.Uuid(), nullable=False),
            sa.Column("planned_qty", NUM, nullable=False, server_default="0"),
            sa.Column("issued_qty", NUM, nullable=False, server_default="0"),
            sa.Column("std_unit_cost", NUM, nullable=False, server_default="0"),
            sa.Column("std_cost", NUM, nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=18), nullable=False, server_default="RESERVED"),
            *_audit_cols(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["production_order_id"], ["production_orders.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_production_reservations_order", "production_reservations", ["production_order_id"])
        op.create_index("ix_production_reservations_item", "production_reservations", ["item_id"])

    if not _has_table(bind, "production_consumptions"):
        op.create_table(
            "production_consumptions",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("production_order_id", sa.Uuid(), nullable=False),
            sa.Column("stage", sa.String(length=12), nullable=False),
            sa.Column("item_type", sa.String(length=10), nullable=False),
            sa.Column("item_id", sa.Uuid(), nullable=False),
            sa.Column("lot_id", sa.Uuid(), nullable=True),
            sa.Column("qty", NUM, nullable=False, server_default="0"),
            sa.Column("actual_cost", NUM, nullable=False, server_default="0"),
            sa.Column("wastage_qty", NUM, nullable=False, server_default="0"),
            sa.Column("rejection_qty", NUM, nullable=False, server_default="0"),
            sa.Column("is_reprocess", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("posting_reference", sa.String(length=120), nullable=True),
            *_audit_cols(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["production_order_id"], ["production_orders.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_production_consumptions_order", "production_consumptions", ["production_order_id"])

    # --- nullable traceability links on the actuals (additive) ---
    for table in ("knitting_production", "dye_batches", "finishing_transactions"):
        if not _has_column(bind, table, "production_order_id"):
            op.add_column(table, sa.Column("production_order_id", sa.Uuid(), nullable=True))
            op.create_index(f"ix_{table}_production_order_id", table, ["production_order_id"])


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("knitting_production", "dye_batches", "finishing_transactions"):
        if _has_column(bind, table, "production_order_id"):
            try:
                op.drop_index(f"ix_{table}_production_order_id", table_name=table)
            except Exception:
                pass
            # batch mode rebuilds the table on SQLite (no native indexed-column drop).
            with op.batch_alter_table(table) as batch:
                batch.drop_column("production_order_id")
    for t in (
        "production_consumptions",
        "production_reservations",
        "production_orders",
        "fabric_bom_finishing",
        "fabric_bom_knitting",
        "fabric_bom_yarns",
        "fabric_bom_versions",
        "fabric_boms",
    ):
        if _has_table(bind, t):
            op.drop_table(t)
