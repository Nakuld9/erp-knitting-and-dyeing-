"""Company-scoped YarnLot identity uniqueness (Stage 6F-P2-FIX, additive).

Adds a UNIQUE index on ``yarn_lots(company_id, internal_lot_no)``. This is the
company-scoped lot identity the system already relies on: the normal yarn-receipt
path (``receive_yarn_lot``) keys its idempotency on the voucher reference
``YARNLOT:<internal_lot_no>`` per company, so a second receipt of the same lot
number is silently skipped. The opening-stock path posts no voucher and so had no
database guard, allowing two concurrent opening approvals to register the same
new lot. This index closes that race at the database level: the losing
transaction fails on the unique index and rolls back, creating no lot and no
stock.

Purely additive: it creates ONE unique index and alters no row. It does NOT
renumber, merge, delete or modify any historical lot. No backfill.

Safety: ``upgrade`` runs a read-only duplicate preflight first. If any existing
(company_id, internal_lot_no) duplicates are present it raises with the offending
keys and makes NO change, so an operator can resolve the historical data before
the index is created (never resolved destructively by this migration).

Revision ID: 0031_yarn_lot_unique_identity
Revises: 0030_opening_stock_lines
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from alembic import op

revision = "0031_yarn_lot_unique_identity"
down_revision = "0030_opening_stock_lines"
branch_labels = None
depends_on = None

_INDEX = "uq_yarn_lot_company_internal_no"
_COLS = {"company_id", "internal_lot_no"}


def _has_table(bind, name):
    return sa.inspect(bind).has_table(name)


def _already_unique(bind) -> bool:
    """True when (company_id, internal_lot_no) is already enforced unique — either
    by the model-level UniqueConstraint (present on every fresh DB, because the
    baseline migration builds the schema from Base.metadata) or by this
    migration's own index on a DB it has already run against. Column-set based so
    it matches regardless of the constraint/index name and never collides with the
    baseline constraint on PostgreSQL."""
    insp = sa.inspect(bind)
    for uc in insp.get_unique_constraints("yarn_lots"):
        if set(uc.get("column_names") or []) == _COLS:
            return True
    for ix in insp.get_indexes("yarn_lots"):
        if ix.get("unique") and set(ix.get("column_names") or []) == _COLS:
            return True
    return False


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "yarn_lots"):
        return
    # Fresh DBs already have the model-level constraint from the metadata baseline;
    # do nothing (and never try to re-create a same-named index on PostgreSQL).
    # This migration only adds the guarantee to DBs built before the change.
    if _already_unique(bind):
        return

    # Read-only duplicate preflight. If historical duplicates exist, abort with a
    # clear message and change nothing — duplicates must be resolved by hand, never
    # by deleting/renaming/merging lot history here.
    dupes = bind.execute(sa.text(
        "SELECT company_id, internal_lot_no, COUNT(*) AS n "
        "FROM yarn_lots GROUP BY company_id, internal_lot_no HAVING COUNT(*) > 1"
    )).fetchall()
    if dupes:
        sample = "; ".join(f"company={r[0]} lot={r[1]} x{r[2]}" for r in dupes[:10])
        raise RuntimeError(
            "Cannot add uq_yarn_lot_company_internal_no: existing duplicate "
            f"(company_id, internal_lot_no) rows must be resolved first: {sample}")

    op.create_index(_INDEX, "yarn_lots", ["company_id", "internal_lot_no"], unique=True)


def downgrade() -> None:
    # Only drop the index this migration itself created (legacy-DB path). On a
    # fresh DB the uniqueness comes from the model baseline, which this migration
    # never created and must not remove.
    bind = op.get_bind()
    if not _has_table(bind, "yarn_lots"):
        return
    existing = {i["name"] for i in sa.inspect(bind).get_indexes("yarn_lots")}
    if _INDEX in existing:
        op.drop_index(_INDEX, table_name="yarn_lots")
