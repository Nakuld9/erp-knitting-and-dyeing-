"""Master-data routes (customers & suppliers as the representative slice)."""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_permission
from app.models.core import User
from app.models.commercial import SalesOrder
from app.models.masters import (
    Chemical,
    Customer,
    FabricType,
    Machine,
    Recipe,
    RecipeVersion,
    ShadeCode,
    Supplier,
    TaxCode,
    Warehouse,
    YarnMaster,
    MachineType,
)
from app.schemas import CustomerCreate, PartyOut, SupplierCreate, WarehouseCreate, WarehouseOut

router = APIRouter(prefix="/api/masters", tags=["masters"])


@router.get("/customers", response_model=list[PartyOut])
def list_customers(
    db: Session = Depends(get_db), user: User = Depends(require_permission("master.read"))
):
    return (
        db.execute(select(Customer).where(Customer.company_id == user.company_id).order_by(Customer.code))
        .scalars()
        .all()
    )


@router.post("/customers", response_model=PartyOut, status_code=status.HTTP_201_CREATED)
def create_customer(
    payload: CustomerCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("master.write")),
):
    exists = db.execute(
        select(Customer).where(Customer.company_id == user.company_id, Customer.code == payload.code)
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Customer code {payload.code} already exists")
    cust = Customer(company_id=user.company_id, created_by=user.id, **payload.model_dump())
    db.add(cust)
    db.commit()
    db.refresh(cust)
    return cust


@router.get("/suppliers", response_model=list[PartyOut])
def list_suppliers(
    db: Session = Depends(get_db), user: User = Depends(require_permission("master.read"))
):
    return (
        db.execute(select(Supplier).where(Supplier.company_id == user.company_id).order_by(Supplier.code))
        .scalars()
        .all()
    )


@router.post("/suppliers", response_model=PartyOut, status_code=status.HTTP_201_CREATED)
def create_supplier(
    payload: SupplierCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("master.write")),
):
    exists = db.execute(
        select(Supplier).where(Supplier.company_id == user.company_id, Supplier.code == payload.code)
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Supplier code {payload.code} already exists")
    sup = Supplier(company_id=user.company_id, created_by=user.id, **payload.model_dump())
    db.add(sup)
    db.commit()
    db.refresh(sup)
    return sup


@router.get("/warehouses", response_model=list[WarehouseOut])
def list_warehouses(
    db: Session = Depends(get_db), user: User = Depends(require_permission("master.read"))
):
    return (
        db.execute(select(Warehouse).where(Warehouse.company_id == user.company_id).order_by(Warehouse.code))
        .scalars()
        .all()
    )


@router.post("/warehouses", response_model=WarehouseOut, status_code=status.HTTP_201_CREATED)
def create_warehouse(
    payload: WarehouseCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("master.write")),
):
    exists = db.execute(
        select(Warehouse).where(Warehouse.company_id == user.company_id, Warehouse.code == payload.code)
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Warehouse code {payload.code} already exists")
    wh = Warehouse(company_id=user.company_id, created_by=user.id, **payload.model_dump())
    db.add(wh)
    db.commit()
    db.refresh(wh)
    return wh


# --- read-only lookup lists (populate transaction-entry dropdowns) ----------

@router.get("/chemicals")
def list_chemicals(db: Session = Depends(get_db), user: User = Depends(require_permission("master.read"))):
    rows = db.execute(select(Chemical).where(
        Chemical.company_id == user.company_id, Chemical.is_active.is_(True)).order_by(Chemical.code)).scalars().all()
    return [{"id": str(c.id), "code": c.code, "name": c.name, "is_dye": c.is_dye,
             "tax_code_id": str(c.tax_code_id) if c.tax_code_id else None} for c in rows]


@router.get("/tax-codes")
def list_tax_codes(db: Session = Depends(get_db), user: User = Depends(require_permission("master.read"))):
    rows = db.execute(select(TaxCode).where(
        TaxCode.company_id == user.company_id, TaxCode.is_active.is_(True)).order_by(TaxCode.code)).scalars().all()
    return [{"id": str(t.id), "code": t.code, "name": t.name} for t in rows]


@router.get("/machines")
def list_machines(machine_type: str | None = None, db: Session = Depends(get_db),
                  user: User = Depends(require_permission("master.read"))):
    stmt = select(Machine).where(Machine.company_id == user.company_id, Machine.is_active.is_(True))
    if machine_type:
        stmt = stmt.where(Machine.machine_type == machine_type)
    rows = db.execute(stmt.order_by(Machine.code)).scalars().all()
    return [{"id": str(m.id), "code": m.code, "name": m.name, "machine_type": m.machine_type} for m in rows]


@router.get("/shades")
def list_shades(db: Session = Depends(get_db), user: User = Depends(require_permission("master.read"))):
    rows = db.execute(select(ShadeCode).where(
        ShadeCode.company_id == user.company_id, ShadeCode.is_active.is_(True)).order_by(ShadeCode.code)).scalars().all()
    return [{"id": str(s.id), "code": s.code, "name": s.name} for s in rows]


@router.get("/recipe-versions")
def list_recipe_versions(db: Session = Depends(get_db), user: User = Depends(require_permission("master.read"))):
    rows = db.execute(
        select(RecipeVersion, Recipe).join(Recipe, RecipeVersion.recipe_id == Recipe.id).where(
            Recipe.company_id == user.company_id, RecipeVersion.status == "APPROVED")
        .order_by(Recipe.code, RecipeVersion.version_no)
    ).all()
    return [{"id": str(v.id), "label": f"{r.code} v{v.version_no}", "recipe_code": r.code,
             "version_no": v.version_no} for v, r in rows]


@router.get("/yarns")
def list_yarns(db: Session = Depends(get_db), user: User = Depends(require_permission("master.read"))):
    rows = db.execute(select(YarnMaster).where(
        YarnMaster.company_id == user.company_id, YarnMaster.is_active.is_(True)).order_by(YarnMaster.code)).scalars().all()
    return [{"id": str(y.id), "code": y.code, "name": y.name,
             "label": f"{y.code} — {y.name}"} for y in rows]


@router.get("/fabric-types")
def list_fabric_types(db: Session = Depends(get_db), user: User = Depends(require_permission("master.read"))):
    rows = db.execute(select(FabricType).where(
        FabricType.company_id == user.company_id, FabricType.is_active.is_(True)).order_by(FabricType.code)).scalars().all()
    return [{"id": str(f.id), "code": f.code, "name": f.name, "label": f"{f.code} — {f.name}"} for f in rows]


@router.get("/sales-orders")
def list_sales_orders(db: Session = Depends(get_db), user: User = Depends(require_permission("master.read"))):
    rows = db.execute(select(SalesOrder).where(
        SalesOrder.company_id == user.company_id).order_by(SalesOrder.order_date.desc())).scalars().all()
    out = []
    for o in rows:
        cust = db.get(Customer, o.customer_id)
        out.append({"id": str(o.id), "label": f"{o.number} ({cust.name if cust else '-'})"})
    return out


# --- master create endpoints (operate fully from the UI) --------------------

import uuid as _uuid


def _dup(db, model, company_id, code):
    exists = db.execute(select(model).where(model.company_id == company_id, model.code == code)).scalar_one_or_none()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Code {code} already exists")


class ChemicalIn(BaseModel):
    code: str
    name: str
    is_dye: bool = False
    dye_class: str | None = None
    tax_code_id: _uuid.UUID | None = None
    standard_cost: Decimal = Decimal("0")
    reorder_level: Decimal = Decimal("0")


@router.post("/chemicals", status_code=status.HTTP_201_CREATED)
def create_chemical(p: ChemicalIn, db: Session = Depends(get_db), user: User = Depends(require_permission("master.write"))):
    _dup(db, Chemical, user.company_id, p.code)
    c = Chemical(company_id=user.company_id, created_by=user.id, **p.model_dump())
    db.add(c); db.commit()
    return {"id": str(c.id), "code": c.code}


class TaxCodeIn(BaseModel):
    code: str
    name: str
    cgst_rate: Decimal = Decimal("0")
    sgst_rate: Decimal = Decimal("0")
    igst_rate: Decimal = Decimal("0")
    hsn_sac: str | None = None


@router.post("/tax-codes", status_code=status.HTTP_201_CREATED)
def create_tax_code(p: TaxCodeIn, db: Session = Depends(get_db), user: User = Depends(require_permission("master.write"))):
    _dup(db, TaxCode, user.company_id, p.code)
    t = TaxCode(company_id=user.company_id, created_by=user.id, **p.model_dump())
    db.add(t); db.commit()
    return {"id": str(t.id), "code": t.code}


class MachineIn(BaseModel):
    machine_type: str
    code: str
    name: str
    capacity_kg: Decimal | None = None


@router.post("/machines", status_code=status.HTTP_201_CREATED)
def create_machine(p: MachineIn, db: Session = Depends(get_db), user: User = Depends(require_permission("master.write"))):
    _dup(db, Machine, user.company_id, p.code)
    try:
        mt = MachineType(p.machine_type).value
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "machine_type must be KNITTING/DYEING/FINISHING")
    m = Machine(company_id=user.company_id, created_by=user.id, machine_type=mt, code=p.code,
                name=p.name, capacity_kg=p.capacity_kg)
    db.add(m); db.commit()
    return {"id": str(m.id), "code": m.code}


class ShadeIn(BaseModel):
    code: str
    name: str
    customer_id: _uuid.UUID | None = None


@router.post("/shades", status_code=status.HTTP_201_CREATED)
def create_shade(p: ShadeIn, db: Session = Depends(get_db), user: User = Depends(require_permission("master.write"))):
    _dup(db, ShadeCode, user.company_id, p.code)
    s = ShadeCode(company_id=user.company_id, created_by=user.id, **p.model_dump())
    db.add(s); db.commit()
    return {"id": str(s.id), "code": s.code}


class FabricIn(BaseModel):
    code: str
    name: str
    construction: str | None = None
    composition: str | None = None
    gsm: Decimal | None = None
    width_cm: Decimal | None = None


@router.post("/fabric-types", status_code=status.HTTP_201_CREATED)
def create_fabric_type(p: FabricIn, db: Session = Depends(get_db), user: User = Depends(require_permission("master.write"))):
    _dup(db, FabricType, user.company_id, p.code)
    f = FabricType(company_id=user.company_id, created_by=user.id, **p.model_dump())
    db.add(f); db.commit()
    return {"id": str(f.id), "code": f.code}


class YarnIn(BaseModel):
    code: str
    name: str
    yarn_type: str | None = None
    count: str | None = None


@router.post("/yarns", status_code=status.HTTP_201_CREATED)
def create_yarn(p: YarnIn, db: Session = Depends(get_db), user: User = Depends(require_permission("master.write"))):
    _dup(db, YarnMaster, user.company_id, p.code)
    y = YarnMaster(company_id=user.company_id, created_by=user.id, **p.model_dump())
    db.add(y); db.commit()
    return {"id": str(y.id), "code": y.code}


# --- yarn lots (full lot tracking) -----------------------------------------

from app.models.masters import YarnLot  # noqa: E402
from app.services.inventory import balance as _inv_balance  # noqa: E402
from app.models.inventory import ItemType as _ItemType  # noqa: E402
from app.services import yarn as _yarn_svc  # noqa: E402
from app.services.accounting import posting_engine as _pe  # noqa: E402
from decimal import Decimal as _Dec  # noqa: E402


@router.get("/yarn-lots")
def list_yarn_lots(yarn_id: _uuid.UUID | None = None, db: Session = Depends(get_db),
                   user: User = Depends(require_permission("master.read"))):
    stmt = select(YarnLot).where(YarnLot.company_id == user.company_id)
    if yarn_id:
        stmt = stmt.where(YarnLot.yarn_id == yarn_id)
    out = []
    for lot in db.execute(stmt.order_by(YarnLot.internal_lot_no)).scalars():
        bal = _inv_balance(db, company_id=user.company_id, item_type=_ItemType.YARN,
                           item_id=lot.yarn_id, lot_id=lot.id)
        out.append({"id": str(lot.id), "yarn_id": str(lot.yarn_id), "internal_lot_no": lot.internal_lot_no,
                    "supplier_lot_no": lot.supplier_lot_no, "unit_cost": str(lot.unit_cost),
                    "on_hand": str(bal["qty_on_hand"]), "value": str(bal["value"]),
                    "label": f"{lot.internal_lot_no} ({bal['qty_on_hand']} kg)"})
    return out


class YarnLotIn(BaseModel):
    yarn_id: _uuid.UUID
    internal_lot_no: str
    qty: Decimal
    unit_cost: Decimal
    supplier_id: _uuid.UUID | None = None
    supplier_lot_no: str | None = None
    warehouse_id: _uuid.UUID | None = None


@router.post("/yarn-lots", status_code=status.HTTP_201_CREATED)
def receive_yarn_lot(p: YarnLotIn, db: Session = Depends(get_db),
                     user: User = Depends(require_permission("master.write"))):
    try:
        lot, voucher = _yarn_svc.receive_yarn_lot(
            db, user.company_id, yarn_id=p.yarn_id, internal_lot_no=p.internal_lot_no,
            qty=p.qty, unit_cost=p.unit_cost, supplier_id=p.supplier_id,
            supplier_lot_no=p.supplier_lot_no, warehouse_id=p.warehouse_id, created_by=user.id)
        db.commit()
    except _pe.PostingError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    if lot is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Yarn lot {p.internal_lot_no} already received")
    return {"id": str(lot.id), "internal_lot_no": lot.internal_lot_no, "voucher_number": voucher.number}
