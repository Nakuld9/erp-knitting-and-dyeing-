"""Incremental Alembic migration tests (additive 0002 foundation).

Runs the real migration scripts against a disposable on-disk SQLite database and
asserts the upgrade/downgrade round-trip is clean — the automated form of the
manual proof in the production-readiness work. This guards against the baseline
regressing to create_all-only and proves 0002 is reversible.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest
from alembic import command
from alembic.config import Config

import app.core.config as config_mod

NEW_TABLES = [
    "organizations",
    "gst_registrations",
    "user_company_memberships",
    "user_factory_memberships",
]
FACTORY_TABLES = [
    "machines",
    "knitting_production",
    "fabric_rolls",
    "dye_batches",
    "finishing_transactions",
    "quality_inspections",
    "dispensing_transactions",
]
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _alembic_cfg(db_url: str) -> Config:
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _has_table(path: str, name: str) -> bool:
    con = sqlite3.connect(path)
    try:
        return bool(
            con.execute(
                "select 1 from sqlite_master where type='table' and name=?", (name,)
            ).fetchone()
        )
    finally:
        con.close()


def _has_org_column(path: str) -> bool:
    con = sqlite3.connect(path)
    try:
        return any(r[1] == "organization_id" for r in con.execute("PRAGMA table_info(companies)"))
    finally:
        con.close()


def _has_column(path: str, table: str, column: str) -> bool:
    con = sqlite3.connect(path)
    try:
        return any(r[1] == column for r in con.execute(f"PRAGMA table_info({table})"))
    finally:
        con.close()


@pytest.fixture()
def temp_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # alembic/sqlite will create it
    url = f"sqlite:///{path}"
    # env.py reads settings.database_url at runtime; point it at the temp DB.
    monkeypatch.setattr(config_mod.settings, "database_url", url, raising=False)
    try:
        yield url, path
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_upgrade_creates_foundation_tables(temp_db):
    url, path = temp_db
    cfg = _alembic_cfg(url)
    command.upgrade(cfg, "head")
    for t in NEW_TABLES:
        assert _has_table(path, t), f"{t} should exist after upgrade"
    assert _has_org_column(path), "companies.organization_id should exist after upgrade"
    # existing data tables are present too (baseline create_all)
    assert _has_table(path, "companies")
    assert _has_table(path, "vouchers")


def test_downgrade_removes_only_foundation(temp_db):
    url, path = temp_db
    cfg = _alembic_cfg(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0001_initial")
    for t in NEW_TABLES:
        assert not _has_table(path, t), f"{t} should be gone after downgrade"
    assert not _has_org_column(path), "organization_id should be gone after downgrade"
    # existing business tables survive the downgrade (non-destructive to 0001)
    assert _has_table(path, "companies")
    assert _has_table(path, "vouchers")


def test_roundtrip_is_repeatable(temp_db):
    url, path = temp_db
    cfg = _alembic_cfg(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0001_initial")
    command.upgrade(cfg, "head")  # must re-apply cleanly
    for t in NEW_TABLES:
        assert _has_table(path, t)
    assert _has_org_column(path)


def test_company_databases_table_added_and_reversible(temp_db):
    """0008 adds company_databases; downgrade to 0007 removes it."""
    url, path = temp_db
    cfg = _alembic_cfg(url)
    command.upgrade(cfg, "head")
    assert _has_table(path, "company_databases")
    command.downgrade(cfg, "0007_factory_stock_transfers")
    assert not _has_table(path, "company_databases")
    assert _has_table(path, "companies")


def test_factory_transfer_table_added_and_reversible(temp_db):
    """0007 adds factory_stock_transfers; downgrade to 0006 removes it."""
    url, path = temp_db
    cfg = _alembic_cfg(url)
    command.upgrade(cfg, "head")
    assert _has_table(path, "factory_stock_transfers")
    command.downgrade(cfg, "0006_intercompany_mappings")
    assert not _has_table(path, "factory_stock_transfers")
    assert _has_table(path, "warehouses")


def test_intercompany_table_added_and_reversible(temp_db):
    """0006 adds intercompany_account_mappings; downgrade to 0005 removes it."""
    url, path = temp_db
    cfg = _alembic_cfg(url)
    command.upgrade(cfg, "head")
    assert _has_table(path, "intercompany_account_mappings")
    command.downgrade(cfg, "0005_consolidation_eliminations")
    assert not _has_table(path, "intercompany_account_mappings")
    assert _has_table(path, "companies")


def test_consolidation_table_added_and_reversible(temp_db):
    """0005 adds consolidation_eliminations; downgrade to 0004 removes it."""
    url, path = temp_db
    cfg = _alembic_cfg(url)
    command.upgrade(cfg, "head")
    assert _has_table(path, "consolidation_eliminations")
    command.downgrade(cfg, "0004_branch_legacy_flag")
    assert not _has_table(path, "consolidation_eliminations")
    assert _has_table(path, "companies")


def test_branch_legacy_flag_added_and_reversible(temp_db):
    """0004 adds branches.is_legacy_unassigned; downgrade to 0003 removes it."""
    url, path = temp_db
    cfg = _alembic_cfg(url)
    command.upgrade(cfg, "head")
    assert _has_column(path, "branches", "is_legacy_unassigned")
    command.downgrade(cfg, "0003_factory_ownership_columns")
    assert not _has_column(path, "branches", "is_legacy_unassigned")
    assert _has_table(path, "branches")


def test_factory_columns_added_and_reversible(temp_db):
    """0003 adds nullable factory_id to factory-owned tables; downgrade to 0002
    removes only those columns, leaving the tables intact."""
    url, path = temp_db
    cfg = _alembic_cfg(url)
    command.upgrade(cfg, "head")
    for t in FACTORY_TABLES:
        assert _has_column(path, t, "factory_id"), f"{t}.factory_id should exist after upgrade"
    command.downgrade(cfg, "0002_multicompany_foundation")
    for t in FACTORY_TABLES:
        assert not _has_column(path, t, "factory_id"), f"{t}.factory_id should be gone after downgrade"
        assert _has_table(path, t), f"{t} itself must survive the downgrade"


def test_intercompany_saga_table_added_and_reversible(temp_db):
    """0009 adds intercompany_txns + mapping account-code columns; reversible."""
    url, path = temp_db
    cfg = _alembic_cfg(url)
    command.upgrade(cfg, "head")
    assert _has_table(path, "intercompany_txns")
    assert _has_column(path, "intercompany_account_mappings", "due_from_account_code")
    command.downgrade(cfg, "0008_company_databases")
    assert not _has_table(path, "intercompany_txns")
    assert not _has_column(path, "intercompany_account_mappings", "due_from_account_code")


def test_intercompany_mapping_ids_nullable(temp_db):
    """0010 keeps the mapping table and account-code columns; account ids optional."""
    url, path = temp_db
    cfg = _alembic_cfg(url)
    command.upgrade(cfg, "head")
    assert _has_column(path, "intercompany_account_mappings", "due_from_account_code")
    assert _has_table(path, "intercompany_account_mappings")
    command.downgrade(cfg, "0009_intercompany_saga")
    assert _has_table(path, "intercompany_account_mappings")


def test_fabric_bom_tables_added_and_reversible(temp_db):
    """0011 adds the fabric BOM + production-order tables and the traceability
    links on the actuals; fully reversible."""
    url, path = temp_db
    cfg = _alembic_cfg(url)
    command.upgrade(cfg, "head")
    for t in ("fabric_boms", "fabric_bom_versions", "fabric_bom_yarns",
              "fabric_bom_knitting", "fabric_bom_finishing", "production_orders",
              "production_reservations", "production_consumptions"):
        assert _has_table(path, t), t
    assert _has_column(path, "fabric_bom_yarns", "std_unit_cost")
    assert _has_column(path, "knitting_production", "production_order_id")
    command.downgrade(cfg, "0010_intercompany_code_based")
    assert not _has_table(path, "fabric_boms")
    assert not _has_column(path, "knitting_production", "production_order_id")


def _has_index(path, table, name):
    import sqlite3
    con = sqlite3.connect(path)
    try:
        rows = con.execute(f"PRAGMA index_list('{table}')").fetchall()
        return any(r[1] == name for r in rows)
    finally:
        con.close()


def test_fabric_bom_unique_indexes_added_and_reversible(temp_db):
    """0012 adds the BOM uniqueness indexes (concurrency safety); reversible."""
    url, path = temp_db
    cfg = _alembic_cfg(url)
    command.upgrade(cfg, "head")
    assert _has_index(path, "fabric_boms", "uq_fabric_boms_company_code")
    assert _has_index(path, "fabric_bom_versions", "uq_fabric_bom_versions_bom_versionno")
    command.downgrade(cfg, "0011_fabric_bom")
    assert not _has_index(path, "fabric_boms", "uq_fabric_boms_company_code")


def test_grey_cost_and_dyeing_tables_added_and_reversible(temp_db):
    """0013 adds grey-cost columns + dyeing cost-tracking tables; reversible."""
    url, path = temp_db
    cfg = _alembic_cfg(url)
    command.upgrade(cfg, "head")
    for t in ("dyeing_jobs", "dyeing_inputs", "dyeing_outputs"):
        assert _has_table(path, t), t
    assert _has_column(path, "production_orders", "actual_knitting_conversion_cost")
    assert _has_column(path, "production_orders", "grey_inventory_value")
    command.downgrade(cfg, "0012_fabric_bom_unique")
    assert not _has_table(path, "dyeing_jobs")
    assert not _has_column(path, "production_orders", "grey_inventory_value")
