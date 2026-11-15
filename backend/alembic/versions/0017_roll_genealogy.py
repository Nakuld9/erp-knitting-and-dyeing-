"""Stage 2: protected roll identity + manufacturing genealogy (additive).

Adds, additively and reversibly:
* ``fabric_rolls.ref_code`` — a nullable, searchable secondary reference kept
  alongside the protected ``roll_no`` (e.g. a dyed roll's "{job}-{lot}").
* ``roll_genealogy_edges`` — the traceability edge table (source roll -> output
  roll, with process/event/quantity/loss/rejection/correction metadata).
* A **company-scoped** unique index on ``(company_id, roll_no)`` — created ONLY
  after a duplicate pre-flight scan confirms there are no conflicts. If any
  duplicate exists the migration raises and the index is NOT created, so no data
  is ever renamed, merged or deleted to force uniqueness.

Nothing existing is altered or removed. No historical rolls are renumbered, no
genealogy is backfilled, and no company/factory IDs are backfilled. Guarded so it
is safe on both the control database and each company database, on SQLite and
PostgreSQL.

Revision ID: 0017_roll_genealogy
Revises: 0016_hybrid_foundation
Create Date: 2026-11-15
"""
import sqlalchemy as sa
from alembic import op

from app.core.types import Quantity

revision = "0017_roll_genealogy"
down_revision = "0016_hybrid_foundation"
branch_labels = None
depends_on = None

_UNIQUE_IX = "uq_fabric_rolls_company_roll_no"


def _insp(bind):
    return sa.inspect(bind)


def _has_table(bind, name):
    return _insp(bind).has_table(name)


def _has_column(bind, table, col):
    insp = _insp(bind)
    return insp.has_table(table) and col in {c["name"] for c in insp.get_columns(table)}


def _has_index(bind, table, name):
    insp = _insp(bind)
    if not insp.has_table(table):
        return False
    return name in {ix["name"] for ix in insp.get_indexes(table)}


def _duplicate_roll_nos(bind):
    """Return representative (company_id, roll_no, count) rows that would violate
    company-scoped uniqueness. NULL company_id rows are excluded (SQL treats NULLs
    as distinct, so they never conflict and historical NULLs are preserved)."""
    if not _has_table(bind, "fabric_rolls"):
        return []
    rows = bind.execute(sa.text(
        "SELECT company_id, roll_no, COUNT(*) AS n "
        "FROM fabric_rolls "
        "WHERE company_id IS NOT NULL AND roll_no IS NOT NULL "
        "GROUP BY company_id, roll_no HAVING COUNT(*) > 1 "
        "ORDER BY n DESC"
    )).fetchall()
    return rows


def upgrade() -> None:
    bind = op.get_bind()

    # 1) additive column
    if not _has_column(bind, "fabric_rolls", "ref_code"):
        op.add_column("fabric_rolls", sa.Column("ref_code", sa.String(length=60), nullable=True))
        op.create_index("ix_fabric_rolls_ref_code", "fabric_rolls", ["ref_code"])

    # 2) genealogy edge table
    if not _has_table(bind, "roll_genealogy_edges"):
        op.create_table(
            "roll_genealogy_edges",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("factory_id", sa.Uuid(), nullable=True),
            sa.Column("event_group_id", sa.Uuid(), nullable=False),
            sa.Column("source_roll_id", sa.Uuid(), nullable=True),
            sa.Column("output_roll_id", sa.Uuid(), nullable=True),
            sa.Column("process_type", sa.String(length=20), nullable=False),
            sa.Column("event_type", sa.String(length=20), nullable=False),
            sa.Column("production_ref", sa.String(length=60), nullable=True),
            sa.Column("uom", sa.String(length=10), nullable=True),
            sa.Column("input_qty", Quantity(), nullable=True),
            sa.Column("output_qty", Quantity(), nullable=True),
            sa.Column("input_weight_kg", Quantity(), nullable=True),
            sa.Column("output_weight_kg", Quantity(), nullable=True),
            sa.Column("input_length_m", Quantity(), nullable=True),
            sa.Column("output_length_m", Quantity(), nullable=True),
            sa.Column("process_loss", Quantity(), nullable=True),
            sa.Column("rejected_qty", Quantity(), nullable=True),
            sa.Column("event_datetime", sa.DateTime(timezone=True), nullable=False),
            sa.Column("recorded_by", sa.Uuid(), nullable=True),
            sa.Column("audit_ref", sa.String(length=120), nullable=True),
            sa.Column("correction_of_id", sa.Uuid(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["factory_id"], ["branches.id"]),
            sa.ForeignKeyConstraint(["source_roll_id"], ["fabric_rolls.id"]),
            sa.ForeignKeyConstraint(["output_roll_id"], ["fabric_rolls.id"]),
            sa.ForeignKeyConstraint(["correction_of_id"], ["roll_genealogy_edges.id"]),
        )
        op.create_index("ix_roll_genealogy_company_id", "roll_genealogy_edges", ["company_id"])
        op.create_index("ix_roll_genealogy_factory_id", "roll_genealogy_edges", ["factory_id"])
        op.create_index("ix_roll_genealogy_event_group", "roll_genealogy_edges", ["event_group_id"])
        op.create_index("ix_roll_genealogy_source", "roll_genealogy_edges", ["source_roll_id"])
        op.create_index("ix_roll_genealogy_output", "roll_genealogy_edges", ["output_roll_id"])
        op.create_index("ix_roll_genealogy_process", "roll_genealogy_edges", ["process_type"])
        op.create_index("ix_roll_genealogy_production_ref", "roll_genealogy_edges", ["production_ref"])
        op.create_index("ix_roll_genealogy_event_dt", "roll_genealogy_edges", ["event_datetime"])

    # 3) company-scoped uniqueness — ONLY after a clean duplicate pre-flight.
    if not _has_index(bind, "fabric_rolls", _UNIQUE_IX):
        dups = _duplicate_roll_nos(bind)
        if dups:
            sample = "; ".join(f"company={r[0]} roll_no={r[1]} x{r[2]}" for r in dups[:10])
            raise RuntimeError(
                "Cannot add company-scoped unique index on fabric_rolls(company_id, roll_no): "
                f"{len(dups)} duplicate group(s) exist. No data was changed. Examples: {sample}")
        op.create_index(_UNIQUE_IX, "fabric_rolls", ["company_id", "roll_no"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind, "fabric_rolls", _UNIQUE_IX):
        op.drop_index(_UNIQUE_IX, table_name="fabric_rolls")
    if _has_table(bind, "roll_genealogy_edges"):
        op.drop_table("roll_genealogy_edges")
    if _has_column(bind, "fabric_rolls", "ref_code"):
        if _has_index(bind, "fabric_rolls", "ix_fabric_rolls_ref_code"):
            op.drop_index("ix_fabric_rolls_ref_code", table_name="fabric_rolls")
        op.drop_column("fabric_rolls", "ref_code")
