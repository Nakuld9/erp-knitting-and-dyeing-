"""Opening manufactured-roll lines + FabricRoll.legacy_label (Stage 6F-P3, additive).

Adds:
* ``opening_roll_lines`` — the typed opening lines for physical manufactured
  fabric rolls (greige / dyed / finished), hanging off a P1 ``opening_batches``
  row of type MANUFACTURED_ROLLS;
* ``fabric_rolls.legacy_label`` — a nullable, searchable legacy factory /
  handwritten label captured at cutover, with a company-scoped UNIQUE index on
  NON-EMPTY values (NULLs are distinct, so historical rolls are never blocked).

Purely additive: one new table, one new nullable column and its indexes. No row
is altered, no roll is renumbered, no backfill. Fresh databases already have
``legacy_label`` and both indexes from the metadata baseline (0001 runs
``Base.metadata.create_all``), so the column/index steps here are guarded no-ops
there and only act on databases built before this change.

Safety: before adding the legacy-label unique index this migration runs a
read-only duplicate preflight. If historical duplicate non-empty
(company_id, legacy_label) rows exist it raises and changes nothing — they must
be resolved by hand, never renamed/deleted/merged here.

Revision ID: 0032_opening_roll_lines
Revises: 0031_yarn_lot_unique_identity
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from alembic import op

from app.core.types import Money, Quantity

revision = "0032_opening_roll_lines"
down_revision = "0031_yarn_lot_unique_identity"
branch_labels = None
depends_on = None

_LEGACY_INDEX = "uq_fabric_rolls_company_legacy_label"
_LEGACY_COLS = {"company_id", "legacy_label"}


def _has_table(bind, name):
    return sa.inspect(bind).has_table(name)


def _has_column(bind, table, col):
    insp = sa.inspect(bind)
    return insp.has_table(table) and col in {c["name"] for c in insp.get_columns(table)}


def _legacy_unique_present(bind) -> bool:
    insp = sa.inspect(bind)
    for ix in insp.get_indexes("fabric_rolls"):
        if ix.get("unique") and set(ix.get("column_names") or []) == _LEGACY_COLS:
            return True
    for uc in insp.get_unique_constraints("fabric_rolls"):
        if set(uc.get("column_names") or []) == _LEGACY_COLS:
            return True
    return False


def _audit_cols():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
    ]


def upgrade() -> None:
    bind = op.get_bind()

    # 0) Widen the opening line_type columns so they fit "MANUFACTURED_ROLLS" (18
    #    chars). Migration 0030 created them as varchar(12). PostgreSQL enforces
    #    varchar length; SQLite stores TEXT regardless, so it needs no change
    #    (and a SQLite type change would force a full table rebuild). No-op when
    #    already wide enough.
    if bind.dialect.name == "postgresql":
        for tbl in ("opening_import_files", "opening_stock_lines"):
            if _has_column(bind, tbl, "line_type"):
                op.alter_column(tbl, "line_type", type_=sa.String(length=24),
                                existing_type=sa.String(length=12), existing_nullable=False)

    # 1) FabricRoll.legacy_label column (legacy DBs only; fresh DBs have it).
    if _has_table(bind, "fabric_rolls") and not _has_column(bind, "fabric_rolls", "legacy_label"):
        op.add_column("fabric_rolls", sa.Column("legacy_label", sa.String(length=80), nullable=True))
        op.create_index("ix_fabric_rolls_legacy_label", "fabric_rolls", ["legacy_label"])

    # 2) Company-scoped partial unique index on non-empty legacy labels.
    if _has_table(bind, "fabric_rolls") and not _legacy_unique_present(bind):
        dupes = bind.execute(sa.text(
            "SELECT company_id, legacy_label, COUNT(*) AS n FROM fabric_rolls "
            "WHERE legacy_label IS NOT NULL "
            "GROUP BY company_id, legacy_label HAVING COUNT(*) > 1")).fetchall()
        if dupes:
            sample = "; ".join(f"company={r[0]} label={r[1]} x{r[2]}" for r in dupes[:10])
            raise RuntimeError(
                "Cannot add uq_fabric_rolls_company_legacy_label: existing duplicate "
                f"(company_id, legacy_label) rows must be resolved first: {sample}")
        op.create_index(_LEGACY_INDEX, "fabric_rolls", ["company_id", "legacy_label"], unique=True,
                        sqlite_where=sa.text("legacy_label IS NOT NULL"),
                        postgresql_where=sa.text("legacy_label IS NOT NULL"))

    # 3) opening_roll_lines table.
    if not _has_table(bind, "opening_roll_lines"):
        op.create_table(
            "opening_roll_lines",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("batch_id", sa.Uuid(), nullable=False),
            sa.Column("import_file_id", sa.Uuid(), nullable=True),
            sa.Column("row_number", sa.Integer(), nullable=True),
            sa.Column("erp_roll_no", sa.String(length=40), nullable=True),
            sa.Column("legacy_label", sa.String(length=80), nullable=True),
            sa.Column("registration_mode", sa.String(length=20), nullable=True),
            sa.Column("roll_kind", sa.String(length=10), nullable=True),
            sa.Column("origin_stream", sa.String(length=10), nullable=True),
            sa.Column("fabric_roll_id", sa.Uuid(), nullable=True),
            sa.Column("factory_id", sa.Uuid(), nullable=True),
            sa.Column("warehouse_id", sa.Uuid(), nullable=True),
            sa.Column("warehouse_category", sa.String(length=20), nullable=True),
            sa.Column("effective_date", sa.Date(), nullable=True),
            sa.Column("fabric_type_id", sa.Uuid(), nullable=True),
            sa.Column("structure", sa.String(length=120), nullable=True),
            sa.Column("description", sa.String(length=200), nullable=True),
            sa.Column("gsm", Quantity(), nullable=True),
            sa.Column("width_cm", Quantity(), nullable=True),
            sa.Column("colour", sa.String(length=60), nullable=True),
            sa.Column("shade_id", sa.Uuid(), nullable=True),
            sa.Column("dye_lot", sa.String(length=40), nullable=True),
            sa.Column("net_weight_kg", Quantity(), nullable=True),
            sa.Column("uom_id", sa.Uuid(), nullable=True),
            sa.Column("length_m", Quantity(), nullable=True),
            sa.Column("quality_status", sa.String(length=20), nullable=True),
            sa.Column("job_ref", sa.String(length=60), nullable=True),
            sa.Column("processing_ref", sa.String(length=120), nullable=True),
            sa.Column("source_document", sa.String(length=120), nullable=True),
            sa.Column("source_reference", sa.String(length=200), nullable=True),
            sa.Column("physical_verification_status", sa.String(length=30), nullable=True),
            sa.Column("disputed_unknown", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("unit_cost", Money(), nullable=True),
            sa.Column("total_value", Money(), nullable=True),
            sa.Column("valuation_source", sa.String(length=120), nullable=True),
            sa.Column("validation_status", sa.String(length=12), nullable=False, server_default="PENDING"),
            sa.Column("validation_errors", sa.Text(), nullable=True),
            sa.Column("dedup_key", sa.String(length=200), nullable=True),
            sa.Column("stock_layer_id", sa.Uuid(), nullable=True),
            sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
            *_audit_cols(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["batch_id"], ["opening_batches.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["import_file_id"], ["opening_import_files.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["factory_id"], ["branches.id"]),
            sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
            sa.ForeignKeyConstraint(["fabric_type_id"], ["fabric_types.id"]),
            sa.ForeignKeyConstraint(["shade_id"], ["shade_codes.id"]),
            sa.ForeignKeyConstraint(["uom_id"], ["units_of_measure.id"]),
            sa.UniqueConstraint("batch_id", "dedup_key", name="uq_opening_roll_batch_dedup"),
        )
        op.create_index("ix_opening_roll_company_id", "opening_roll_lines", ["company_id"])
        op.create_index("ix_opening_roll_batch_id", "opening_roll_lines", ["batch_id"])
        op.create_index("ix_opening_roll_import_file", "opening_roll_lines", ["import_file_id"])
        op.create_index("ix_opening_roll_erp_no", "opening_roll_lines", ["erp_roll_no"])
        op.create_index("ix_opening_roll_legacy_label", "opening_roll_lines", ["legacy_label"])
        op.create_index("ix_opening_roll_fabric_roll", "opening_roll_lines", ["fabric_roll_id"])
        op.create_index("ix_opening_roll_factory_id", "opening_roll_lines", ["factory_id"])
        op.create_index("ix_opening_roll_validation", "opening_roll_lines", ["validation_status"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "opening_roll_lines"):
        op.drop_table("opening_roll_lines")   # cascades its indexes
    # Drop only what this migration itself can have created on a legacy DB.
    if _has_table(bind, "fabric_rolls"):
        existing = {i["name"] for i in sa.inspect(bind).get_indexes("fabric_rolls")}
        if _LEGACY_INDEX in existing:
            op.drop_index(_LEGACY_INDEX, table_name="fabric_rolls")
        if "ix_fabric_rolls_legacy_label" in existing and _has_column(bind, "fabric_rolls", "legacy_label"):
            op.drop_index("ix_fabric_rolls_legacy_label", table_name="fabric_rolls")
        if _has_column(bind, "fabric_rolls", "legacy_label"):
            with op.batch_alter_table("fabric_rolls") as b:
                b.drop_column("legacy_label")
