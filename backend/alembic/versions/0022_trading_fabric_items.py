"""Trading-fabric item master (Stage 4B-1, additive).

Adds, additively and reversibly:
* ``trading_fabric_items`` — the purchased-for-resale fabric SKU master, owned by
  one company, with company-scoped unique SKU code and an optional reference to
  an existing ``fabric_types`` row for shared specifications.

Nothing existing is altered. In particular NO inventory table is touched: the new
``ItemType.TRADING_FABRIC`` is stored as the 12-character value ``TRADE_FABRIC``,
which fits the existing ``item_type`` String(12) columns, so no column is
widened and no historical stock layer or transaction is changed or reclassified.

Guarded so it is safe to run against an already-migrated database and against
each company database.

Revision ID: 0022_trading_fabric_items
Revises: 0021_jobwork_billing_hardening
Create Date: 2026-11-15
"""
import sqlalchemy as sa
from alembic import op

from app.core.types import Quantity

revision = "0022_trading_fabric_items"
down_revision = "0021_jobwork_billing_hardening"
branch_labels = None
depends_on = None


def _has_table(bind, name):
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "trading_fabric_items"):
        op.create_table(
            "trading_fabric_items",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("code", sa.String(length=40), nullable=False),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("fabric_type_id", sa.Uuid(), nullable=True),
            sa.Column("construction", sa.String(length=120), nullable=True),
            sa.Column("composition", sa.String(length=120), nullable=True),
            sa.Column("gsm", Quantity(), nullable=True),
            sa.Column("width_cm", Quantity(), nullable=True),
            sa.Column("colour", sa.String(length=60), nullable=True),
            sa.Column("uom_id", sa.Uuid(), nullable=True),
            sa.Column("tax_code_id", sa.Uuid(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["fabric_type_id"], ["fabric_types.id"]),
            sa.ForeignKeyConstraint(["uom_id"], ["units_of_measure.id"]),
            sa.ForeignKeyConstraint(["tax_code_id"], ["tax_codes.id"]),
            sa.UniqueConstraint("company_id", "code", name="uq_trading_item_company_code"),
        )
        op.create_index("ix_trading_fabric_items_company_id", "trading_fabric_items", ["company_id"])
        op.create_index("ix_trading_fabric_items_code", "trading_fabric_items", ["code"])
        op.create_index("ix_trading_fabric_items_fabric_type_id", "trading_fabric_items", ["fabric_type_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "trading_fabric_items"):
        op.drop_index("ix_trading_fabric_items_fabric_type_id", table_name="trading_fabric_items")
        op.drop_index("ix_trading_fabric_items_code", table_name="trading_fabric_items")
        op.drop_index("ix_trading_fabric_items_company_id", table_name="trading_fabric_items")
        op.drop_table("trading_fabric_items")
