"""Process Color Service dispensing results into immutable transactions,
costed inventory consumption and accounting vouchers - exactly once."""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.dispensing import (
    DispensingCorrection,
    DispensingTransaction,
    DosingStatus,
    IntegrationEvent,
)
from app.models.manufacturing import DyeBath
from app.models.masters import Chemical, ChemicalLot, Machine, RecipeVersion, Recipe, ShadeCode
from app.services import inventory
from app.services.accounting import posting_engine as pe
from app.services.accounting.posting_rules import resolve_account
from app.services.colorservice.adapter import ColorServiceAdapter
from app.services.colorservice.schemas import DispenseResult, DoseOrder, DoseOrderMaterial
from app.services.inventory import InsufficientStockError
from app.services.manufacturing.recipes import scale_recipe
from app.models.inventory import ItemType


def _chemical_by_code(db: Session, company_id: uuid.UUID, code: str) -> Chemical | None:
    return db.execute(
        select(Chemical).where(Chemical.company_id == company_id, Chemical.code == code)
    ).scalar_one_or_none()


def _lot_by_ref(db: Session, company_id: uuid.UUID, chemical_id: uuid.UUID, ref: str | None):
    if not ref:
        return None
    return db.execute(
        select(ChemicalLot).where(
            ChemicalLot.company_id == company_id,
            ChemicalLot.chemical_id == chemical_id,
            ChemicalLot.internal_lot_no == ref,
        )
    ).scalar_one_or_none()


def build_dose_order(db: Session, bath: DyeBath) -> DoseOrder:
    """Scale the bath's APPROVED recipe into a dose order (ERP -> Color Service)."""
    if bath.recipe_version_id is None:
        raise ValueError("Bath has no recipe version assigned")
    version = db.get(RecipeVersion, bath.recipe_version_id)
    scaled = scale_recipe(db, version, bath.fabric_kg, require_approved=True)
    recipe = db.get(Recipe, version.recipe_id)
    shade = db.get(ShadeCode, recipe.shade_id) if recipe and recipe.shade_id else None
    machine = db.get(Machine, bath.machine_id)
    materials = [
        DoseOrderMaterial(
            material_code=m["material_code"], target_qty=m["required_kg"],
            unit="KG", dosing_sequence=m["sequence"], is_dye=m["is_dye"],
        )
        for m in scaled
    ]
    return DoseOrder(
        batch_code=bath.batch.batch_code if bath.batch else "",
        bath_code=bath.bath_code, machine_code=machine.code if machine else "",
        fabric_kg=Decimal(str(bath.fabric_kg)),
        shade=shade.code if shade else None,
        recipe_code=recipe.code if recipe else None,
        recipe_revision=version.version_no, materials=materials,
    )


def send_dose_order(db: Session, bath: DyeBath, adapter: ColorServiceAdapter):
    """Build + send a dose order, logging an OUTBOUND integration event."""
    order = build_dose_order(db, bath)
    # Idempotent: one OUTBOUND event per bath; re-sending updates the payload.
    evt = db.execute(
        select(IntegrationEvent).where(
            IntegrationEvent.company_id == bath.company_id,
            IntegrationEvent.direction == "OUTBOUND",
            IntegrationEvent.external_txn_id == bath.bath_code,
        )
    ).scalar_one_or_none()
    payload = json.dumps(asdict(order), default=str)
    if evt is None:
        db.add(IntegrationEvent(
            company_id=bath.company_id, direction="OUTBOUND", external_txn_id=bath.bath_code,
            payload=payload, status="PROCESSED", processed_at=datetime.now(timezone.utc),
        ))
    else:
        evt.payload = payload
        evt.processed_at = datetime.now(timezone.utc)
    db.flush()
    ack = adapter.send_dose_order(order)
    return ack, order


def ingest_results(
    db: Session,
    company_id: uuid.UUID,
    bath: DyeBath,
    results: list[DispenseResult],
    *,
    created_by: uuid.UUID | None = None,
) -> dict:
    summary = {"processed": 0, "skipped": 0, "failed": 0,
               "total_cost": quantize(0, MONEY_SCALE), "transactions": []}

    # The dyeing machine is a work centre: tag the WIP line so dye/chemical cost
    # accumulates per machine in the ledger.
    from app.services import work_centres
    cc_id = work_centres.machine_cost_centre_id(db, company_id, bath.machine_id)

    for r in results:
        evt = db.execute(
            select(IntegrationEvent).where(
                IntegrationEvent.company_id == company_id,
                IntegrationEvent.direction == "INBOUND",
                IntegrationEvent.external_txn_id == r.external_txn_id,
            )
        ).scalar_one_or_none()
        if evt is not None and evt.status == "PROCESSED":
            summary["skipped"] += 1
            continue
        if evt is None:
            evt = IntegrationEvent(
                company_id=company_id, direction="INBOUND", external_txn_id=r.external_txn_id,
                payload=json.dumps(asdict(r), default=str), status="PENDING",
            )
            db.add(evt)
            db.flush()

        chem = _chemical_by_code(db, company_id, r.material_code)
        if chem is None:
            evt.status = "FAILED"
            evt.error = f"Unknown material code {r.material_code}"
            summary["failed"] += 1
            continue

        txn = db.execute(
            select(DispensingTransaction).where(
                DispensingTransaction.company_id == company_id,
                DispensingTransaction.external_txn_id == r.external_txn_id,
            )
        ).scalar_one_or_none()
        if txn is not None and txn.consumed:
            evt.status = "PROCESSED"
            summary["skipped"] += 1
            continue

        lot = _lot_by_ref(db, company_id, chem.id, r.chemical_lot)
        is_corr = r.is_correction or r.dosing_status == DosingStatus.ADDITIONAL.value
        if txn is None:
            txn = DispensingTransaction(
                company_id=company_id, bath_id=bath.id, chemical_id=chem.id,
                chemical_lot_id=lot.id if lot else None, external_txn_id=r.external_txn_id,
                material_code=r.material_code, target_weight=r.target_weight,
                actual_weight=r.actual_weight, unit=r.unit, dosing_status=r.dosing_status,
                is_correction=is_corr, operator=r.operator, scale_id=r.scale_id,
                started_at=r.started_at, completed_at=r.completed_at,
            )
            db.add(txn)
            db.flush()

        # FAILED dosing is recorded but never consumes stock or posts cost.
        if r.dosing_status == DosingStatus.FAILED.value:
            txn.consumed = False
            evt.status = "PROCESSED"
            evt.processed_at = datetime.now(timezone.utc)
            summary["processed"] += 1
            continue

        ref = f"BATH:{bath.bath_code}:consume:{r.external_txn_id}"
        try:
            cons = inventory.consume(
                db, company_id=company_id, item_type=ItemType.CHEMICAL, item_id=chem.id,
                qty=r.actual_weight, lot_id=lot.id if lot else None, event_type="CONSUME",
                source_module="dispensing", source_id=str(txn.id), posting_reference=ref,
            )
        except InsufficientStockError as exc:
            evt.status = "FAILED"
            evt.error = str(exc)
            summary["failed"] += 1
            continue

        cost = cons["total_cost"]
        event_type = "DYE_CONSUMPTION" if chem.is_dye else "CHEMICAL_CONSUMPTION"
        voucher = pe.post_voucher(db, pe.PostingRequest(
            company_id=company_id, voucher_type_code="PJ", voucher_date=bath.actual_start.date()
            if bath.actual_start else datetime.now(timezone.utc).date(),
            posting_reference=ref,
            narration=f"{'Correction ' if is_corr else ''}dispensing {r.material_code} into {bath.bath_code}",
            source_module="dispensing", source_id=str(txn.id), created_by=created_by,
            lines=[
                pe.PostingLine(resolve_account(db, company_id, event_type, "wip"), debit=cost,
                               cost_centre_id=cc_id, narration=f"{r.material_code} to WIP"),
                pe.PostingLine(resolve_account(db, company_id, event_type, "inventory"), credit=cost,
                               narration=f"{r.material_code} consumed"),
            ],
        ))

        qty = Decimal(str(r.actual_weight))
        txn.consumed = True
        txn.consumption_cost = cost
        txn.unit_cost = quantize(cost / qty, MONEY_SCALE) if qty > 0 else quantize(0, MONEY_SCALE)
        txn.posting_reference = ref

        if is_corr:
            db.add(DispensingCorrection(
                company_id=company_id, bath_id=bath.id, dispensing_txn_id=txn.id,
                chemical_id=chem.id, qty=r.actual_weight,
                reason="Color Service additional/correction dosing", operator=r.operator,
            ))

        evt.status = "PROCESSED"
        evt.processed_at = datetime.now(timezone.utc)
        summary["processed"] += 1
        summary["total_cost"] += cost
        summary["transactions"].append(txn)

    db.flush()
    return summary
