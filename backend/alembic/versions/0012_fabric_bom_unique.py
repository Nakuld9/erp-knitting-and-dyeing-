"""Fabric BOM uniqueness constraints (concurrency safety; additive).

Adds unique indexes so concurrent creation / revision cannot produce duplicate
BOM codes within a company or duplicate version numbers within a BOM:

* ``fabric_boms (company_id, code)``   — one code per company
* ``fabric_bom_versions (bom_id, version_no)`` — one version_no per BOM

Additive and reversible. A pre-existing database with duplicates would need them
resolved first, but these tables are new in 0011 so none can exist yet.

Revision ID: 0012_fabric_bom_unique
Revises: 0011_fabric_bom
Create Date: 2026-11-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0012_fabric_bom_unique"
down_revision = "0011_fabric_bom"
branch_labels = None
depends_on = None


def _indexes(bind, table):
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return set()
    return {ix["name"] for ix in insp.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if "uq_fabric_boms_company_code" not in _indexes(bind, "fabric_boms"):
        op.create_index("uq_fabric_boms_company_code", "fabric_boms",
                        ["company_id", "code"], unique=True)
    if "uq_fabric_bom_versions_bom_versionno" not in _indexes(bind, "fabric_bom_versions"):
        op.create_index("uq_fabric_bom_versions_bom_versionno", "fabric_bom_versions",
                        ["bom_id", "version_no"], unique=True)


def _drop_unique(bind, table: str, name: str) -> None:
    """Drop a unique index created with ``create_index(unique=True)``.

    PostgreSQL backs such an index with a unique *constraint* of the same name, so
    it must be dropped via ``ALTER TABLE ... DROP CONSTRAINT``; SQLite (and others)
    drop it as a plain index."""
    if name not in _indexes(bind, table):
        return
    if bind.dialect.name == "postgresql":
        op.drop_constraint(name, table, type_="unique")
    else:
        op.drop_index(name, table_name=table)


def downgrade() -> None:
    bind = op.get_bind()
    _drop_unique(bind, "fabric_bom_versions", "uq_fabric_bom_versions_bom_versionno")
    _drop_unique(bind, "fabric_boms", "uq_fabric_boms_company_code")
