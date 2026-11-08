"""Stage 2-4 operational routes: procurement, knitting, dyeing + dispensing, sales.

These expose the tested service layer. The ``simulate-dispense`` endpoint drives
the Color Service *simulator* and is a development aid (a verified vendor adapter
replaces it in production).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_permission
from app.models.commercial import GoodsReceiptNote, GRNLine, SalesInvoice, SalesInvoiceLine
from app.models.core import User
from app.models.manufacturing import BatchStatus, DyeBatch, DyeBath
from app.services import batching, procurement, sales
from app.services.colorservice import ColorServiceSimulator
from app.services.colorservice import ingest as cs
from app.services.colorservice.register import bath_consumption_register
from app.services.manufacturing.recipes import RecipeNotApprovedError

router = APIRouter(prefix="/api", tags=["operations"])


# ----------------------------- procurement ---------------------------------

class GRNLineIn(BaseModel):
    chemical_id: uuid.UUID
    chemical_lot_id: uuid.UUID | None = None
    warehouse_id: uuid.UUID | None = None
    purchase_order_line_id: uuid.UUID | None = None
    qty: Decimal
    unit_cost: Decimal
    tax_code_id: uuid.UUID | None = None


class GRNIn(BaseModel):
    supplier_id: uuid.UUID
    number: str
    grn_date: date
    supplier_invoice_no: str | None = None
    purchase_order_id: uuid.UUID | None = None
    lines: list[GRNLineIn] = Field(min_length=1)


@router.post("/procurement/grn", status_code=status.HTTP_201_CREATED)
def post_grn(payload: GRNIn, db: Session = Depends(get_db),
            user: User = Depends(require_permission("purchase.manage"))):
    grn = GoodsReceiptNote(company_id=user.company_id, supplier_id=payload.supplier_id,
                           number=payload.number, grn_date=payload.grn_date,
                           supplier_invoice_no=payload.supplier_invoice_no,
                           purchase_order_id=payload.purchase_order_id, created_by=user.id)
    for i, ln in enumerate(payload.lines, start=1):
        grn.lines.append(GRNLine(line_no=i, **ln.model_dump()))
    db.add(grn)
    db.flush()
    voucher = procurement.post_chemical_grn(db, grn, created_by=user.id)
    po_status = None
    if payload.purchase_order_id:
        from app.models.commercial import PurchaseOrder
        po = db.get(PurchaseOrder, payload.purchase_order_id)
        po_status = po.status if po else None
    db.commit()
    return {"grn_id": str(grn.id), "voucher_number": voucher.number,
            "total": str(voucher.total_debit), "po_status": po_status}


# ------------------------------- sales --------------------------------------

class SalesLineIn(BaseModel):
    description: str | None = None
    qty: Decimal
    rate: Decimal
    tax_code_id: uuid.UUID | None = None


class SalesInvoiceIn(BaseModel):
    customer_id: uuid.UUID
    number: str
    invoice_date: date
    sales_order_id: uuid.UUID | None = None
    lines: list[SalesLineIn] = Field(min_length=1)


@router.post("/sales/invoices", status_code=status.HTTP_201_CREATED)
def post_sales_invoice(payload: SalesInvoiceIn, db: Session = Depends(get_db),
                       user: User = Depends(require_permission("sales.manage"))):
    inv = SalesInvoice(company_id=user.company_id, customer_id=payload.customer_id,
                       number=payload.number, invoice_date=payload.invoice_date,
                       sales_order_id=payload.sales_order_id, created_by=user.id)
    for i, ln in enumerate(payload.lines, start=1):
        inv.lines.append(SalesInvoiceLine(line_no=i, **ln.model_dump()))
    db.add(inv)
    db.flush()
    voucher = sales.post_sales_invoice(db, inv, created_by=user.id)
    db.commit()
    return {"invoice_id": str(inv.id), "voucher_number": voucher.number,
            "taxable_value": str(inv.taxable_value), "tax_amount": str(inv.tax_amount),
            "total_value": str(inv.total_value)}


# ------------------------------ dyeing --------------------------------------

class BatchIn(BaseModel):
    batch_code: str
    fabric_kg: Decimal = Decimal("0")   # auto-summed from rolls when roll_ids given
    shade_id: uuid.UUID | None = None
    sales_order_id: uuid.UUID | None = None
    roll_ids: list[uuid.UUID] = Field(default_factory=list)


class AssignRollsIn(BaseModel):
    roll_ids: list[uuid.UUID] = Field(min_length=1)


class BathIn(BaseModel):
    batch_id: uuid.UUID
    bath_code: str
    machine_id: uuid.UUID
    recipe_version_id: uuid.UUID
    fabric_kg: Decimal
    liquor_ratio: Decimal | None = None


@router.post("/dyeing/batches", status_code=status.HTTP_201_CREATED)
def create_batch(payload: BatchIn, db: Session = Depends(get_db),
                 user: User = Depends(require_permission("dyeing.manage"))):
    batch = DyeBatch(company_id=user.company_id, status=BatchStatus.PLANNED, created_by=user.id,
                     batch_code=payload.batch_code, fabric_kg=payload.fabric_kg,
                     shade_id=payload.shade_id, sales_order_id=payload.sales_order_id)
    db.add(batch)
    db.flush()
    if payload.roll_ids:
        try:
            batching.assign_rolls(db, user.company_id, batch, payload.roll_ids)
        except batching.BatchingError as exc:
            db.rollback()
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    db.commit()
    return {"batch_id": str(batch.id), "batch_code": batch.batch_code,
            "fabric_kg": str(batch.fabric_kg), "rolls": len(batch.rolls)}


@router.post("/dyeing/batches/{batch_id}/assign-rolls")
def assign_rolls(batch_id: uuid.UUID, payload: AssignRollsIn, db: Session = Depends(get_db),
                 user: User = Depends(require_permission("dyeing.manage"))):
    batch = db.get(DyeBatch, batch_id)
    if batch is None or batch.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Batch not found")
    try:
        batching.assign_rolls(db, user.company_id, batch, payload.roll_ids)
        db.commit()
    except batching.BatchingError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return {"batch_id": str(batch.id), "fabric_kg": str(batch.fabric_kg), "rolls": len(batch.rolls)}


@router.get("/dyeing/batches")
def list_batches(db: Session = Depends(get_db), user: User = Depends(require_permission("dyeing.manage"))):
    from sqlalchemy import select
    rows = db.execute(select(DyeBatch).where(DyeBatch.company_id == user.company_id)
                      .order_by(DyeBatch.batch_code.desc())).scalars().all()
    return [{"id": str(b.id), "label": f"{b.batch_code} ({b.fabric_kg} kg, {len(b.rolls)} rolls)",
             "batch_code": b.batch_code, "fabric_kg": str(b.fabric_kg),
             "rolls": len(b.rolls), "status": b.status} for b in rows[:100]]


@router.get("/dyeing/batches/{batch_id}/rolls")
def batch_rolls(batch_id: uuid.UUID, db: Session = Depends(get_db),
                user: User = Depends(require_permission("dyeing.manage"))):
    batch = db.get(DyeBatch, batch_id)
    if batch is None or batch.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Batch not found")
    return [{"id": str(r.id), "roll_no": r.roll_no, "weight_kg": str(r.weight_kg)} for r in batch.rolls]


@router.post("/dyeing/baths", status_code=status.HTTP_201_CREATED)
def create_bath(payload: BathIn, db: Session = Depends(get_db),
                user: User = Depends(require_permission("dyeing.manage"))):
    bath = DyeBath(company_id=user.company_id, status=BatchStatus.RUNNING,
                   actual_start=datetime.now(timezone.utc), created_by=user.id, **payload.model_dump())
    db.add(bath)
    db.commit()
    return {"bath_id": str(bath.id), "bath_code": bath.bath_code}


@router.get("/dyeing/baths/{bath_id}/dose-order")
def preview_dose_order(bath_id: uuid.UUID, db: Session = Depends(get_db),
                       user: User = Depends(require_permission("dyeing.manage"))):
    bath = db.get(DyeBath, bath_id)
    if bath is None or bath.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bath not found")
    try:
        order = cs.build_dose_order(db, bath)
    except (ValueError, RecipeNotApprovedError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return {"bath_code": order.bath_code, "fabric_kg": str(order.fabric_kg),
            "materials": [{"material_code": m.material_code, "target_qty": str(m.target_qty),
                           "sequence": m.dosing_sequence, "is_dye": m.is_dye} for m in order.materials]}


@router.post("/dyeing/baths/{bath_id}/simulate-dispense")
def simulate_dispense(bath_id: uuid.UUID, db: Session = Depends(get_db),
                      user: User = Depends(require_permission("dispensing.operate"))):
    """DEV ONLY: run the Color Service simulator for a bath (send + ingest)."""
    bath = db.get(DyeBath, bath_id)
    if bath is None or bath.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bath not found")
    adapter = ColorServiceSimulator()
    try:
        _ack, order = cs.send_dose_order(db, bath, adapter)
        summary = cs.ingest_results(db, user.company_id, bath, adapter.fetch_results(order),
                                    created_by=user.id)
        db.commit()
    except (ValueError, RecipeNotApprovedError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return {"processed": summary["processed"], "skipped": summary["skipped"],
            "failed": summary["failed"], "total_cost": str(summary["total_cost"])}


@router.get("/dyeing/baths/{bath_id}/consumption-register")
def consumption_register(bath_id: uuid.UUID, db: Session = Depends(get_db),
                         user: User = Depends(require_permission("report.read"))):
    bath = db.get(DyeBath, bath_id)
    if bath is None or bath.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bath not found")
    reg = bath_consumption_register(db, bath)
    reg["fabric_kg"] = str(reg["fabric_kg"])
    reg["total_actual_kg"] = str(reg["total_actual_kg"])
    reg["total_cost"] = str(reg["total_cost"])
    reg["cost_per_kg_fabric"] = str(reg["cost_per_kg_fabric"])
    for r in reg["rows"]:
        for k in ("recipe_target_kg", "actual_kg", "variance_kg", "cost", "consumption_per_kg"):
            r[k] = str(r[k])
    return reg
