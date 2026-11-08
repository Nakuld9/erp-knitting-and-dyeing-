"""Finishing and quality services."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.types import QTY_SCALE, quantize
from app.models.inventory import ItemType
from app.models.manufacturing import FabricRoll, RollKind
from app.models.quality import (
    ComplaintStatus,
    CustomerComplaint,
    FINISHING_PROCESSES,
    FinishingProcess,
    FinishingTransaction,
    InspectionResult,
    QualityInspection,
)
from app.services import inventory
from app.services.accounting import posting_engine as pe
from app.services.accounting.posting_engine import fiscal_year_for
from app.services.accounting.posting_rules import resolve_account
from app.services.sequences import next_document_number

# Default Delta E thresholds (configurable per deployment later).
DELTA_E_ACCEPT = Decimal("1.0")
DELTA_E_REWORK = Decimal("2.0")


def _number(db, company_id, doc_type, d: date, prefix):
    return next_document_number(db, company_id=company_id, doc_type=doc_type,
                                fiscal_year=fiscal_year_for(d), prefix=prefix)


def record_finishing(db: Session, company_id: uuid.UUID, *, process_type, process_date: date,
                     fabric_roll_id=None, dye_batch_id=None, machine_id=None,
                     input_weight_kg=None, output_weight_kg=None, width_cm=None, gsm=None,
                     shrinkage_pct=None, operator_id=None, remarks=None,
                     created_by=None) -> FinishingTransaction:
    pt = process_type if isinstance(process_type, FinishingProcess) else FinishingProcess(process_type)
    if shrinkage_pct is None and input_weight_kg and output_weight_kg:
        iw, ow = Decimal(str(input_weight_kg)), Decimal(str(output_weight_kg))
        if iw > 0:
            shrinkage_pct = quantize((iw - ow) / iw * Decimal("100"), QTY_SCALE)

    ft = FinishingTransaction(
        company_id=company_id, number=_number(db, company_id, "FIN", process_date, "FIN"),
        process_type=pt.value, fabric_roll_id=fabric_roll_id, dye_batch_id=dye_batch_id,
        machine_id=machine_id, process_date=process_date, input_weight_kg=input_weight_kg,
        output_weight_kg=output_weight_kg, width_cm=width_cm, gsm=gsm, shrinkage_pct=shrinkage_pct,
        operator_id=operator_id, remarks=remarks, created_by=created_by,
    )
    db.add(ft)

    if fabric_roll_id and pt in FINISHING_PROCESSES:
        roll = db.get(FabricRoll, fabric_roll_id)
        if roll is not None:
            was_greige = (roll.kind == RollKind.GREIGE or roll.kind == RollKind.GREIGE.value)
            roll.kind = RollKind.FINISHED
            if width_cm is not None:
                roll.width_cm = width_cm
            if gsm is not None:
                roll.gsm = gsm
            if output_weight_kg is not None:
                roll.weight_kg = output_weight_kg
            if was_greige:
                _transfer_greige_to_fg(db, company_id, roll, process_date, created_by)
    db.flush()
    return ft


def _transfer_greige_to_fg(db: Session, company_id, roll: FabricRoll, process_date: date, created_by):
    """On finishing, move the roll's carried fabric cost greige -> finished in
    the GL (Dr Finished Fabric Inventory / Cr Greige Fabric Inventory), so the
    finished-goods account carries the roll and later dispatch credits it.
    Idempotent per roll; a no-op when the roll carries no stock value."""
    ref = f"FGXFER:{roll.id}"
    if pe.find_existing(db, company_id, ref) is not None:
        return
    bal = inventory.balance(db, company_id=company_id, item_type=ItemType.FABRIC, item_id=roll.id)
    value = bal["value"]
    if value <= 0:
        return
    pe.post_voucher(db, pe.PostingRequest(
        company_id=company_id, voucher_type_code="SJ", voucher_date=process_date,
        posting_reference=ref, narration=f"Finish roll {roll.roll_no} (greige -> FG)",
        source_module="finishing", source_id=str(roll.id), created_by=created_by,
        lines=[
            pe.PostingLine(resolve_account(db, company_id, "FINISHING_FG", "fg"), debit=value,
                           narration="Finished fabric inventory"),
            pe.PostingLine(resolve_account(db, company_id, "FINISHING_FG", "greige"), credit=value,
                           narration="Greige fabric inventory"),
        ],
    ))


def _derive_result(delta_e, shade_match) -> InspectionResult:
    if shade_match is False:
        return InspectionResult.REJECT
    if delta_e is None:
        return InspectionResult.ACCEPT
    de = Decimal(str(delta_e))
    if de <= DELTA_E_ACCEPT:
        return InspectionResult.ACCEPT
    if de <= DELTA_E_REWORK:
        return InspectionResult.REWORK
    return InspectionResult.REJECT


_ROLL_STATUS = {InspectionResult.ACCEPT: "OK", InspectionResult.REWORK: "REWORK",
                InspectionResult.REJECT: "REJECT"}


def record_inspection(db: Session, company_id: uuid.UUID, *, inspection_date: date,
                      fabric_roll_id=None, dye_batch_id=None, delta_e=None, l_star=None,
                      a_star=None, b_star=None, shade_match=None, colour_fastness=None,
                      result=None, inspected_qty=None, rejection_qty=None, inspector_id=None,
                      remarks=None, created_by=None) -> QualityInspection:
    res = (InspectionResult(result) if result else _derive_result(delta_e, shade_match))
    qi = QualityInspection(
        company_id=company_id, number=_number(db, company_id, "QC", inspection_date, "QC"),
        fabric_roll_id=fabric_roll_id, dye_batch_id=dye_batch_id, inspection_date=inspection_date,
        inspector_id=inspector_id, delta_e=delta_e, l_star=l_star, a_star=a_star, b_star=b_star,
        shade_match=shade_match, colour_fastness=colour_fastness, result=res.value,
        inspected_qty=inspected_qty, rejection_qty=rejection_qty, remarks=remarks, created_by=created_by,
    )
    db.add(qi)
    if fabric_roll_id:
        roll = db.get(FabricRoll, fabric_roll_id)
        if roll is not None:
            roll.quality_status = _ROLL_STATUS[res]
    db.flush()
    return qi


def raise_complaint(db: Session, company_id: uuid.UUID, *, customer_id, complaint_date: date,
                    description: str, severity="MEDIUM", reference=None, created_by=None) -> CustomerComplaint:
    c = CustomerComplaint(
        company_id=company_id, number=_number(db, company_id, "CMP", complaint_date, "CMP"),
        customer_id=customer_id, reference=reference, complaint_date=complaint_date,
        severity=severity, description=description, status=ComplaintStatus.OPEN.value, created_by=created_by,
    )
    db.add(c)
    db.flush()
    return c


def resolve_complaint(db: Session, complaint_id: uuid.UUID, *, resolution: str,
                      created_by=None) -> CustomerComplaint:
    c = db.get(CustomerComplaint, complaint_id)
    if c is None:
        raise ValueError("Complaint not found")
    c.status = ComplaintStatus.RESOLVED.value
    c.resolution = resolution
    c.resolved_at = datetime.now(timezone.utc)
    db.flush()
    return c


def quality_summary(db: Session, company_id: uuid.UUID, date_from: date, date_to: date) -> dict:
    rows = db.execute(
        select(QualityInspection).where(
            QualityInspection.company_id == company_id,
            QualityInspection.inspection_date >= date_from,
            QualityInspection.inspection_date <= date_to)
    ).scalars().all()

    counts = {"ACCEPT": 0, "REWORK": 0, "REJECT": 0}
    inspected = rejected_qty = Decimal("0")
    de_sum = Decimal("0"); de_n = 0
    for r in rows:
        counts[r.result] = counts.get(r.result, 0) + 1
        inspected += Decimal(str(r.inspected_qty or 0))
        rejected_qty += Decimal(str(r.rejection_qty or 0))
        if r.delta_e is not None:
            de_sum += Decimal(str(r.delta_e)); de_n += 1

    rejection_rate = quantize(rejected_qty / inspected * Decimal("100"), QTY_SCALE) if inspected > 0 else quantize(0, QTY_SCALE)
    avg_de = quantize(de_sum / de_n, QTY_SCALE) if de_n else None

    finishing = db.execute(
        select(func.count()).select_from(FinishingTransaction).where(
            FinishingTransaction.company_id == company_id,
            FinishingTransaction.process_date >= date_from,
            FinishingTransaction.process_date <= date_to)
    ).scalar_one()
    open_complaints = db.execute(
        select(func.count()).select_from(CustomerComplaint).where(
            CustomerComplaint.company_id == company_id,
            CustomerComplaint.status == ComplaintStatus.OPEN.value)
    ).scalar_one()
    total_complaints = db.execute(
        select(func.count()).select_from(CustomerComplaint).where(
            CustomerComplaint.company_id == company_id)
    ).scalar_one()

    return {
        "date_from": date_from, "date_to": date_to,
        "inspections": len(rows), "accepted": counts["ACCEPT"], "rework": counts["REWORK"],
        "rejected": counts["REJECT"],
        "inspected_qty": quantize(inspected, QTY_SCALE), "rejection_qty": quantize(rejected_qty, QTY_SCALE),
        "rejection_rate_pct": rejection_rate, "avg_delta_e": avg_de,
        "finishing_transactions": finishing,
        "open_complaints": open_complaints, "total_complaints": total_complaints,
    }
