"""Fabric BOM + production-order API.

Company-scoped (``get_company_db`` → the acting company's database) and
permission-guarded (``bom.*`` / ``production.manage``). BOMs are company master
data; production orders are factory-aware (``factory_id`` stamped from the
payload and constrained to the user's factory access).
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_company_db, require_permission
from app.models.core import User
from app.models.fabric_bom import (
    BomStatus,
    FabricBom,
    FabricBomVersion,
    ProductionConsumption,
    ProductionOrder,
)
from app.services.bom import production as prod
from app.services.bom import service as bom
from app.services.inventory import InsufficientStockError

router = APIRouter(prefix="/api/bom", tags=["bom"])


# --- schemas ----------------------------------------------------------------

class BomIn(BaseModel):
    code: str
    name: str
    fabric_type_id: uuid.UUID | None = None
    customer_id: uuid.UUID | None = None
    shade_id: uuid.UUID | None = None
    finished_quality: str | None = None
    uom_id: uuid.UUID | None = None


class SpecIn(BaseModel):
    construction: str | None = None
    composition: str | None = None
    yarn_count: str | None = None
    gsm: Decimal | None = None
    width_cm: Decimal | None = None
    gauge: Decimal | None = None
    diameter_inch: Decimal | None = None
    stitch_length_mm: Decimal | None = None
    finished_gsm: Decimal | None = None
    finished_width_cm: Decimal | None = None
    dye_recipe_version_id: uuid.UUID | None = None
    std_knitting_cost: Decimal | None = None
    std_dyeing_cost: Decimal | None = None
    std_labour_cost: Decimal | None = None
    std_overhead_cost: Decimal | None = None
    revision_notes: str | None = None


class YarnIn(BaseModel):
    yarn_id: uuid.UUID
    sequence: int = 1
    blend_percent: Decimal = Decimal("0")
    feeder_allocation: str | None = None
    std_consumption_per_kg: Decimal = Decimal("0")
    wastage_percent: Decimal = Decimal("0")
    process_loss_percent: Decimal = Decimal("0")
    std_unit_cost: Decimal = Decimal("0")


class KnittingIn(BaseModel):
    machine_type: str | None = None
    machine_id: uuid.UUID | None = None
    gauge: Decimal | None = None
    diameter_inch: Decimal | None = None
    feeders: int | None = None
    stitch_length_mm: Decimal | None = None
    rpm: int | None = None
    efficiency_percent: Decimal | None = None
    expected_output_kg_per_shift: Decimal | None = None
    settings: str | None = None


class FinishingIn(BaseModel):
    process_type: str
    sequence: int = 1
    machine_type: str | None = None
    target_gsm: Decimal | None = None
    target_width_cm: Decimal | None = None
    params: str | None = None
    std_cost_per_kg: Decimal = Decimal("0")


class VersionIn(BaseModel):
    clone_from_version_id: uuid.UUID | None = None
    revision_notes: str | None = None


class ApproveIn(BaseModel):
    effective_from: date | None = None


class OrderIn(BaseModel):
    number: str
    planned_qty_kg: Decimal
    bom_id: uuid.UUID | None = None
    bom_version_id: uuid.UUID | None = None
    order_date: date | None = None
    factory_id: uuid.UUID | None = None
    sales_order_id: uuid.UUID | None = None
    shade_id: uuid.UUID | None = None
    warehouse_id: uuid.UUID | None = None
    planned_start: date | None = None
    planned_end: date | None = None


class IssueIn(BaseModel):
    reservation_id: uuid.UUID
    qty: Decimal
    lot_id: uuid.UUID | None = None
    warehouse_id: uuid.UUID | None = None
    method: str = "FIFO"
    wastage_qty: Decimal = Decimal("0")
    rejection_qty: Decimal = Decimal("0")
    is_reprocess: bool = False


class CompleteIn(BaseModel):
    produced_qty_kg: Decimal


# --- serialization ----------------------------------------------------------

def _version_dict(v: FabricBomVersion) -> dict:
    return {
        "id": str(v.id), "version_no": v.version_no,
        "status": v.status.value if hasattr(v.status, "value") else v.status,
        "effective_from": v.effective_from.isoformat() if v.effective_from else None,
        "effective_to": v.effective_to.isoformat() if v.effective_to else None,
        "approved_by": str(v.approved_by) if v.approved_by else None,
        "revision_notes": v.revision_notes,
        "construction": v.construction, "composition": v.composition, "yarn_count": v.yarn_count,
        "gsm": str(v.gsm) if v.gsm is not None else None,
        "width_cm": str(v.width_cm) if v.width_cm is not None else None,
        "gauge": str(v.gauge) if v.gauge is not None else None,
        "diameter_inch": str(v.diameter_inch) if v.diameter_inch is not None else None,
        "stitch_length_mm": str(v.stitch_length_mm) if v.stitch_length_mm is not None else None,
        "dye_recipe_version_id": str(v.dye_recipe_version_id) if v.dye_recipe_version_id else None,
        "std_yarn_cost": str(v.std_yarn_cost), "std_dye_chem_cost": str(v.std_dye_chem_cost),
        "std_knitting_cost": str(v.std_knitting_cost), "std_dyeing_cost": str(v.std_dyeing_cost),
        "std_finishing_cost": str(v.std_finishing_cost), "std_labour_cost": str(v.std_labour_cost),
        "std_overhead_cost": str(v.std_overhead_cost),
        "std_total_cost_per_kg": str(v.std_total_cost_per_kg),
        "yarns": [{
            "id": str(y.id), "yarn_id": str(y.yarn_id), "sequence": y.sequence,
            "blend_percent": str(y.blend_percent), "feeder_allocation": y.feeder_allocation,
            "std_consumption_per_kg": str(y.std_consumption_per_kg),
            "wastage_percent": str(y.wastage_percent), "process_loss_percent": str(y.process_loss_percent),
            "std_unit_cost": str(y.std_unit_cost),
        } for y in v.yarns],
        "knitting": None if v.knitting is None else {
            "machine_type": v.knitting.machine_type,
            "machine_id": str(v.knitting.machine_id) if v.knitting.machine_id else None,
            "gauge": str(v.knitting.gauge) if v.knitting.gauge is not None else None,
            "diameter_inch": str(v.knitting.diameter_inch) if v.knitting.diameter_inch is not None else None,
            "feeders": v.knitting.feeders,
            "stitch_length_mm": str(v.knitting.stitch_length_mm) if v.knitting.stitch_length_mm is not None else None,
            "rpm": v.knitting.rpm,
            "efficiency_percent": str(v.knitting.efficiency_percent) if v.knitting.efficiency_percent is not None else None,
            "expected_output_kg_per_shift": str(v.knitting.expected_output_kg_per_shift) if v.knitting.expected_output_kg_per_shift is not None else None,
            "settings": v.knitting.settings,
        },
        "finishing_steps": [{
            "id": str(s.id), "sequence": s.sequence, "process_type": s.process_type,
            "machine_type": s.machine_type,
            "target_gsm": str(s.target_gsm) if s.target_gsm is not None else None,
            "target_width_cm": str(s.target_width_cm) if s.target_width_cm is not None else None,
            "params": s.params, "std_cost_per_kg": str(s.std_cost_per_kg),
        } for s in v.finishing_steps],
    }


def _bom_dict(b: FabricBom, *, with_versions: bool = True) -> dict:
    d = {
        "id": str(b.id), "code": b.code, "name": b.name,
        "fabric_type_id": str(b.fabric_type_id) if b.fabric_type_id else None,
        "customer_id": str(b.customer_id) if b.customer_id else None,
        "shade_id": str(b.shade_id) if b.shade_id else None,
        "finished_quality": b.finished_quality, "is_active": b.is_active,
    }
    if with_versions:
        d["versions"] = [_version_dict(v) for v in b.versions]
    return d


def _get_bom(db: Session, user: User, bom_id: uuid.UUID) -> FabricBom:
    b = db.get(FabricBom, bom_id)
    if b is None or b.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "BOM not found")
    return b


def _get_version(db: Session, user: User, version_id: uuid.UUID) -> FabricBomVersion:
    v = db.get(FabricBomVersion, version_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "version not found")
    _get_bom(db, user, v.bom_id)  # company guard via parent
    return v


# --- BOM endpoints ----------------------------------------------------------

@router.get("")
def list_boms(db: Session = Depends(get_company_db),
              user: User = Depends(require_permission("bom.read"))):
    rows = db.execute(
        select(FabricBom).where(FabricBom.company_id == user.company_id).order_by(FabricBom.code)
    ).scalars().all()
    return [_bom_dict(b, with_versions=False) for b in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_bom(payload: BomIn, db: Session = Depends(get_company_db),
               user: User = Depends(require_permission("bom.write"))):
    try:
        b = bom.create_bom(db, company_id=user.company_id, created_by=user.id, **payload.model_dump())
        db.commit()
    except bom.BomError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    db.refresh(b)
    return _bom_dict(b)


@router.post("/{bom_id}/versions", status_code=status.HTTP_201_CREATED)
def add_version(bom_id: uuid.UUID, payload: VersionIn, db: Session = Depends(get_company_db),
                user: User = Depends(require_permission("bom.write"))):
    _get_bom(db, user, bom_id)
    try:
        v = bom.add_version(db, bom_id=bom_id, created_by=user.id, **payload.model_dump())
        db.commit()
    except bom.BomError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    db.refresh(v)
    return _version_dict(v)


@router.put("/versions/{version_id}/spec")
def set_spec(version_id: uuid.UUID, payload: SpecIn, db: Session = Depends(get_company_db),
             user: User = Depends(require_permission("bom.write"))):
    _get_version(db, user, version_id)
    try:
        v = bom.set_spec(db, version_id, **{k: val for k, val in payload.model_dump().items() if val is not None})
        db.commit()
    except bom.BomError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    db.refresh(v)
    return _version_dict(v)


@router.put("/versions/{version_id}/yarns")
def set_yarns(version_id: uuid.UUID, rows: list[YarnIn], db: Session = Depends(get_company_db),
              user: User = Depends(require_permission("bom.write"))):
    _get_version(db, user, version_id)
    try:
        bom.set_yarns(db, version_id, [r.model_dump() for r in rows])
        db.commit()
    except bom.BomError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    return _version_dict(_get_version(db, user, version_id))


@router.put("/versions/{version_id}/knitting")
def set_knitting(version_id: uuid.UUID, payload: KnittingIn, db: Session = Depends(get_company_db),
                 user: User = Depends(require_permission("bom.write"))):
    _get_version(db, user, version_id)
    try:
        bom.set_knitting(db, version_id, **payload.model_dump())
        db.commit()
    except bom.BomError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    return _version_dict(_get_version(db, user, version_id))


@router.put("/versions/{version_id}/finishing")
def set_finishing(version_id: uuid.UUID, rows: list[FinishingIn], db: Session = Depends(get_company_db),
                  user: User = Depends(require_permission("bom.write"))):
    _get_version(db, user, version_id)
    try:
        bom.set_finishing(db, version_id, [r.model_dump() for r in rows])
        db.commit()
    except bom.BomError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    return _version_dict(_get_version(db, user, version_id))


@router.get("/versions/{version_id}/cost")
def cost_rollup(version_id: uuid.UUID, db: Session = Depends(get_company_db),
                user: User = Depends(require_permission("bom.read"))):
    v = _get_version(db, user, version_id)
    roll = bom.compute_cost_rollup(db, v)
    return {k: str(val) for k, val in roll.items()}


@router.post("/versions/{version_id}/approve")
def approve_version(version_id: uuid.UUID, payload: ApproveIn, db: Session = Depends(get_company_db),
                    user: User = Depends(require_permission("bom.approve"))):
    _get_version(db, user, version_id)
    try:
        v = bom.approve_version(db, version_id=version_id, approved_by=user.id,
                                effective_from=payload.effective_from)
        db.commit()
    except bom.BomError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    db.refresh(v)
    return _version_dict(v)


@router.post("/versions/{version_id}/obsolete")
def obsolete_version(version_id: uuid.UUID, db: Session = Depends(get_company_db),
                     user: User = Depends(require_permission("bom.approve"))):
    _get_version(db, user, version_id)
    try:
        v = bom.obsolete_version(db, version_id)
        db.commit()
    except bom.BomError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    db.refresh(v)
    return _version_dict(v)


# --- production orders ------------------------------------------------------

def _order_dict(o: ProductionOrder) -> dict:
    return {
        "id": str(o.id), "number": o.number, "bom_version_id": str(o.bom_version_id),
        "status": o.status.value if hasattr(o.status, "value") else o.status,
        "planned_qty_kg": str(o.planned_qty_kg), "produced_qty_kg": str(o.produced_qty_kg),
        "factory_id": str(o.factory_id) if o.factory_id else None,
        "sales_order_id": str(o.sales_order_id) if o.sales_order_id else None,
        "reservations": [{
            "id": str(r.id), "stage": r.stage, "item_type": r.item_type, "item_id": str(r.item_id),
            "planned_qty": str(r.planned_qty), "issued_qty": str(r.issued_qty),
            "std_unit_cost": str(r.std_unit_cost), "std_cost": str(r.std_cost),
            "status": r.status.value if hasattr(r.status, "value") else r.status,
        } for r in o.reservations],
    }


def _get_order(db: Session, user: User, order_id: uuid.UUID) -> ProductionOrder:
    o = db.get(ProductionOrder, order_id)
    if o is None or o.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found")
    return o


@router.get("/orders")
def list_orders(db: Session = Depends(get_company_db),
                user: User = Depends(require_permission("bom.read"))):
    rows = db.execute(
        select(ProductionOrder).where(ProductionOrder.company_id == user.company_id)
        .order_by(ProductionOrder.number)
    ).scalars().all()
    return [_order_dict(o) for o in rows]


@router.post("/orders", status_code=status.HTTP_201_CREATED)
def create_order(payload: OrderIn, db: Session = Depends(get_company_db),
                 user: User = Depends(require_permission("production.manage"))):
    try:
        o = prod.create_order(db, company_id=user.company_id, created_by=user.id, **payload.model_dump())
        db.commit()
    except prod.ProductionError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    db.refresh(o)
    return _order_dict(o)


@router.get("/orders/{order_id}")
def get_order(order_id: uuid.UUID, db: Session = Depends(get_company_db),
              user: User = Depends(require_permission("bom.read"))):
    return _order_dict(_get_order(db, user, order_id))


@router.get("/orders/{order_id}/requirements")
def order_requirements(order_id: uuid.UUID, db: Session = Depends(get_company_db),
                       user: User = Depends(require_permission("bom.read"))):
    _get_order(db, user, order_id)
    reqs = prod.material_requirements(db, order_id)
    return [{k: (str(v) if isinstance(v, Decimal) else v) for k, v in r.items()} for r in reqs]


@router.post("/orders/{order_id}/issue")
def issue_material(order_id: uuid.UUID, payload: IssueIn, db: Session = Depends(get_company_db),
                   user: User = Depends(require_permission("production.manage"))):
    _get_order(db, user, order_id)
    try:
        c = prod.issue_material(db, order_id=order_id, **payload.model_dump())
        db.commit()
    except InsufficientStockError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    except prod.ProductionError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    return {"id": str(c.id), "stage": c.stage, "item_id": str(c.item_id),
            "qty": str(c.qty), "actual_cost": str(c.actual_cost)}


@router.post("/orders/{order_id}/complete")
def complete_order(order_id: uuid.UUID, payload: CompleteIn, db: Session = Depends(get_company_db),
                   user: User = Depends(require_permission("production.manage"))):
    _get_order(db, user, order_id)
    try:
        o = prod.complete_order(db, order_id=order_id, produced_qty_kg=payload.produced_qty_kg)
        db.commit()
    except prod.ProductionError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    db.refresh(o)
    return _order_dict(o)


@router.get("/orders/{order_id}/variance")
def order_variance(order_id: uuid.UUID, db: Session = Depends(get_company_db),
                   user: User = Depends(require_permission("bom.read"))):
    _get_order(db, user, order_id)
    return prod.variance_report(db, order_id)


# NOTE: this single-segment catch-all is declared LAST so literal paths
# (/orders, /versions/...) are matched before it.
@router.get("/{bom_id}")
def get_bom(bom_id: uuid.UUID, db: Session = Depends(get_company_db),
            user: User = Depends(require_permission("bom.read"))):
    return _bom_dict(_get_bom(db, user, bom_id))
