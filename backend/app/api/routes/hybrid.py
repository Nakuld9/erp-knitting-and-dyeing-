"""Hybrid-manufacturing configuration API (Stage 1).

Company-scoped process-mode configuration and warehouse-category assignment.
Reads are open to any authenticated member of the active company; changes require
the ``config.manage`` permission and are audited. Nothing here posts a voucher or
moves stock. Company isolation is enforced by ``get_company_db`` routing + the
acting-company ``user.company_id``.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_company_db, get_current_user, require_permission
from app.models.core import User
from app.models.hybrid import WarehouseCategory
from app.services import hybrid as svc

router = APIRouter(prefix="/api/config", tags=["hybrid-config"])


@router.get("/process-mode")
def get_process_mode(db: Session = Depends(get_company_db),
                     user: User = Depends(get_current_user)) -> dict:
    """The active company's hybrid-manufacturing configuration (defaults applied
    when not yet set: internal ON, outsourced OFF)."""
    row = svc.get_process_mode(db, user.company_id)
    if row is None:
        return {"company_id": str(user.company_id), "configured": False,
                "knit_internal": True, "knit_outsourced": False,
                "dye_internal": True, "dye_outsourced": False,
                "finish_internal": True, "finish_outsourced": False}
    return {"company_id": str(user.company_id), "configured": True, **row.as_dict()}


class ProcessModeIn(BaseModel):
    knit_internal: bool | None = None
    knit_outsourced: bool | None = None
    dye_internal: bool | None = None
    dye_outsourced: bool | None = None
    finish_internal: bool | None = None
    finish_outsourced: bool | None = None


@router.put("/process-mode")
def update_process_mode(payload: ProcessModeIn,
                        db: Session = Depends(get_company_db),
                        user: User = Depends(require_permission("config.manage"))) -> dict:
    """Update any subset of the six toggles (admin only; audited). Disabling a mode
    only blocks NEW work for it — historical records are never affected."""
    changes = {k: v for k, v in payload.model_dump().items() if v is not None}
    try:
        row = svc.update_process_mode(db, user.company_id, changes, updated_by=user.id)
    except svc.HybridConfigError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    db.commit()
    return {"company_id": str(user.company_id), "configured": True, **row.as_dict()}


@router.get("/warehouse-categories")
def list_warehouse_categories(user: User = Depends(get_current_user)) -> list[str]:
    """The allowed inventory-stage categories (for UI selectors). VENDOR_CUSTODY is
    system-managed (auto-assigned to a job worker's custody warehouse) and is not a
    manually selectable stage."""
    return [c.value for c in WarehouseCategory if c != WarehouseCategory.VENDOR_CUSTODY]


class CategoryIn(BaseModel):
    category: str | None = None


@router.put("/warehouses/{warehouse_id}/category")
def set_warehouse_category(warehouse_id: uuid.UUID, payload: CategoryIn,
                           db: Session = Depends(get_company_db),
                           user: User = Depends(require_permission("config.manage"))) -> dict:
    """Assign (or clear) a warehouse's inventory-stage category. Additive — it does
    not move stock or change the legacy warehouse `kind`."""
    try:
        wh = svc.set_warehouse_category(db, user.company_id, warehouse_id, payload.category,
                                        updated_by=user.id)
    except svc.HybridConfigError as e:
        code = status.HTTP_404_NOT_FOUND if "not found" in str(e).lower() else status.HTTP_400_BAD_REQUEST
        raise HTTPException(code, str(e))
    db.commit()
    return {"id": str(wh.id), "code": wh.code, "name": wh.name, "category": wh.category}
