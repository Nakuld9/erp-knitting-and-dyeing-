"""Multi-company / multi-factory foundation (additive, non-destructive).

Adds the group/membership tables and a nullable ``companies.organization_id``.
Nothing existing is altered or dropped; all new columns are nullable and no data
is touched. Safe to run on a live database after a backup.

Revision ID: 0002_multicompany_foundation
Revises: 0001_initial
Create Date: 2026-11-11
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_multicompany_foundation"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "organizations"):
        op.create_table(
            "organizations",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("code", sa.String(length=20), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
        )
        op.create_index("ix_organizations_code", "organizations", ["code"], unique=True)

    if not _has_column(bind, "companies", "organization_id"):
        # Batch mode is portable: native ALTER on PostgreSQL, copy-and-move on
        # SQLite (which cannot ALTER constraints directly). The FK is declared
        # inline on the column so SQLite's table-recreate carries it, and a
        # named reference so a later downgrade's drop_column removes it cleanly.
        with op.batch_alter_table("companies") as batch:
            batch.add_column(
                sa.Column(
                    "organization_id",
                    sa.Uuid(),
                    sa.ForeignKey("organizations.id", name="fk_companies_organization_id"),
                    nullable=True,
                )
            )
        op.create_index(
            "ix_companies_organization_id", "companies", ["organization_id"]
        )

    if not _has_table(bind, "gst_registrations"):
        op.create_table(
            "gst_registrations",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("branch_id", sa.Uuid(), nullable=True),
            sa.Column("gstin", sa.String(length=15), nullable=False),
            sa.Column("state_code", sa.String(length=2), nullable=True),
            sa.Column("legal_name", sa.String(length=200), nullable=True),
            sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["branch_id"], ["branches.id"]),
            sa.UniqueConstraint("company_id", "gstin", name="uq_gstreg_company_gstin"),
        )
        op.create_index("ix_gst_registrations_company_id", "gst_registrations", ["company_id"])
        op.create_index("ix_gst_registrations_branch_id", "gst_registrations", ["branch_id"])

    if not _has_table(bind, "user_company_memberships"):
        op.create_table(
            "user_company_memberships",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("role_code", sa.String(length=40), nullable=True),
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("user_id", "company_id", name="uq_ucm_user_company"),
        )
        op.create_index("ix_user_company_memberships_user_id", "user_company_memberships", ["user_id"])
        op.create_index("ix_user_company_memberships_company_id", "user_company_memberships", ["company_id"])

    if not _has_table(bind, "user_factory_memberships"):
        op.create_table(
            "user_factory_memberships",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("company_id", sa.Uuid(), nullable=False),
            sa.Column("branch_id", sa.Uuid(), nullable=False),
            sa.Column("role_code", sa.String(length=40), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("user_id", "branch_id", name="uq_ufm_user_branch"),
        )
        op.create_index("ix_user_factory_memberships_user_id", "user_factory_memberships", ["user_id"])
        op.create_index("ix_user_factory_memberships_company_id", "user_factory_memberships", ["company_id"])
        op.create_index("ix_user_factory_memberships_branch_id", "user_factory_memberships", ["branch_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "user_factory_memberships"):
        op.drop_table("user_factory_memberships")
    if _has_table(bind, "user_company_memberships"):
        op.drop_table("user_company_memberships")
    if _has_table(bind, "gst_registrations"):
        op.drop_table("gst_registrations")
    if _has_column(bind, "companies", "organization_id"):
        try:
            op.drop_index("ix_companies_organization_id", table_name="companies")
        except Exception:
            pass  # batch recreate below drops it anyway on SQLite
        # Dropping the column removes its FK on both PostgreSQL (dependent
        # constraint auto-dropped) and SQLite (table recreated without it).
        with op.batch_alter_table("companies") as batch:
            batch.drop_column("organization_id")
    if _has_table(bind, "organizations"):
        op.drop_table("organizations")
