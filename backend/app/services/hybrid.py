"""Hybrid-manufacturing configuration service (Stage 1).

Company-scoped process-mode configuration + a ``mode_enabled`` guard that later
stages use to BLOCK NEW transactions for a disabled mode (it never hides history).
All reads/writes are scoped to one ``company_id`` and every change is recorded in
the append-only audit log. Nothing here posts a voucher or touches inventory.
"""
from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.hybrid import Process, ProcessMode, WarehouseCategory, WorkMode
from app.models.masters import Warehouse

_TOGGLES = ("knit_internal", "knit_outsourced", "dye_internal", "dye_outsourced",
            "finish_internal", "finish_outsourced")


class HybridConfigError(ValueError):
    pass


def get_process_mode(db: Session, company_id: uuid.UUID) -> ProcessMode | None:
    return db.execute(
        select(ProcessMode).where(ProcessMode.company_id == company_id)
    ).scalar_one_or_none()


def get_or_create_process_mode(db: Session, company_id: uuid.UUID,
                               created_by: uuid.UUID | None = None) -> ProcessMode:
    """Return the company's config, creating it with defaults (internal ON,
    outsourced OFF) if absent."""
    row = get_process_mode(db, company_id)
    if row is None:
        row = ProcessMode(company_id=company_id, created_by=created_by)  # column defaults apply
        db.add(row)
        db.flush()
    return row


def update_process_mode(db: Session, company_id: uuid.UUID, changes: dict,
                        updated_by: uuid.UUID | None = None) -> ProcessMode:
    """Apply a subset of the six toggles, auditing each actual change. Unknown keys
    are rejected (fail-closed)."""
    bad = set(changes) - set(_TOGGLES)
    if bad:
        raise HybridConfigError(f"Unknown setting(s): {', '.join(sorted(bad))}")
    row = get_or_create_process_mode(db, company_id, created_by=updated_by)
    before = row.as_dict()
    for k, v in changes.items():
        setattr(row, k, bool(v))
    row.updated_by = updated_by
    after = row.as_dict()
    diff = {k: {"from": before[k], "to": after[k]} for k in _TOGGLES if before[k] != after[k]}
    if diff:
        db.add(AuditLog(actor_id=updated_by, action="CONFIG_UPDATE", entity="process_mode",
                        entity_id=str(company_id), detail=json.dumps(diff)))
    db.flush()
    return row


def mode_enabled(db: Session, company_id: uuid.UUID, process: Process, mode: WorkMode) -> bool:
    """Whether (process, mode) is enabled for the company. Defaults to internal ON /
    outsourced OFF when no config row exists, so behaviour is unchanged until a
    company configures it."""
    row = get_process_mode(db, company_id)
    if row is None:
        return WorkMode(mode) == WorkMode.INTERNAL
    return bool(getattr(row, ProcessMode.column_for(process, mode)))


def require_mode(db: Session, company_id: uuid.UUID, process: Process, mode: WorkMode) -> None:
    """Raise if a NEW transaction is attempted for a disabled mode (used by later
    stages). Never affects historical records."""
    if not mode_enabled(db, company_id, process, mode):
        raise HybridConfigError(
            f"{WorkMode(mode).value.title()} {Process(process).value.title()} is disabled "
            "for this company; enable it in configuration before creating new work.")


def require_warehouse_stage(db: Session, company_id: uuid.UUID, warehouse_id: uuid.UUID | None,
                            expected: WarehouseCategory, *, factory_id: uuid.UUID | None = None):
    """Validate a destination warehouse for a NEW manufacturing-stage receipt.

    Rules (Stage 3B):
    * A ``None`` warehouse is permitted (unspecified / legacy) and returns None —
      the stage behaves as before.
    * The warehouse must belong to ``company_id`` (cross-company is always
      rejected), and, when a ``factory_id`` is given and the warehouse is tied to
      a factory, it must be that factory (cross-factory is rejected).
    * The category is enforced ONLY when the warehouse has one set: a warehouse
      explicitly categorised for a different stage is rejected with a clear error.
      An un-categorised (legacy) warehouse is allowed so existing operations never
      break. Historical rolls are never reclassified by this check.
    """
    if warehouse_id is None:
        return None
    wh = db.get(Warehouse, warehouse_id)
    if wh is None or wh.company_id != company_id:
        raise HybridConfigError("Destination warehouse not found in the active company")
    if factory_id is not None and wh.branch_id is not None and wh.branch_id != factory_id:
        raise HybridConfigError(
            f"Warehouse '{wh.code}' belongs to a different factory; select a warehouse "
            "in this factory for this stage.")
    want = WarehouseCategory(expected).value
    if wh.category and wh.category != want:
        raise HybridConfigError(
            f"Warehouse '{wh.code}' is a {wh.category} warehouse; this stage requires a "
            f"{want} warehouse. Select an authorised {want} warehouse.")
    return wh


def set_warehouse_category(db: Session, company_id: uuid.UUID, warehouse_id: uuid.UUID,
                           category: str | None, updated_by: uuid.UUID | None = None) -> Warehouse:
    """Set (or clear) a warehouse's inventory-stage category. Validates the value;
    does not move stock or change the legacy ``kind``."""
    wh = db.get(Warehouse, warehouse_id)
    if wh is None or wh.company_id != company_id:
        raise HybridConfigError("Warehouse not found in the active company")
    cat = None
    if category:
        try:
            cat = WarehouseCategory(category).value
        except ValueError:
            allowed = ", ".join(c.value for c in WarehouseCategory)
            raise HybridConfigError(f"Invalid category '{category}' (allowed: {allowed})")
    old = wh.category
    wh.category = cat
    wh.updated_by = updated_by
    if old != cat:
        db.add(AuditLog(actor_id=updated_by, action="CONFIG_UPDATE", entity="warehouse_category",
                        entity_id=str(warehouse_id), detail=json.dumps({"from": old, "to": cat})))
    db.flush()
    return wh
