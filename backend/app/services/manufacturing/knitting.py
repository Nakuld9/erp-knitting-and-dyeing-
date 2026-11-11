"""Knitting production: issue yarn (FIFO) and produce traceable greige rolls,
posting the matching inventory and accounting movements."""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, QTY_SCALE, quantize
from app.models.inventory import ItemType
from app.models.manufacturing import FabricRoll, KnittingProduction, RollKind
from app.models.masters import Machine
from app.services import inventory
from app.services.accounting import posting_engine as pe
from app.services.accounting.posting_engine import fiscal_year_for
from app.services.accounting.posting_rules import resolve_account
from app.services.sequences import next_document_number


def record_production(
    db: Session,
    kp: KnittingProduction,
    roll_specs: list[dict],
    *,
    created_by: uuid.UUID | None = None,
) -> dict:
    """Consume yarn and create greige rolls. ``roll_specs`` items:
    {roll_no, weight_kg, length_m?, gsm?, width_cm?, warehouse_id?}.
    Idempotent on the knitting document number."""
    issue_ref = f"KNIT:{kp.number}:issue"
    existing = pe.find_existing(db, kp.company_id, issue_ref)
    if existing is not None:
        return {"already_posted": True, "issue_voucher": existing}

    # 1) Issue yarn from stock (FIFO) -> actual cost. If a specific yarn lot is
    #    set, draw only from that lot so the rolls are traceable to the lot.
    result = inventory.consume(
        db, company_id=kp.company_id, item_type=ItemType.YARN, item_id=kp.yarn_id,
        qty=kp.yarn_consumed_kg, lot_id=kp.yarn_lot_id, event_type="YARN_ISSUE",
        source_module="knitting", source_id=str(kp.id), posting_reference=issue_ref,
    )
    yarn_cost = result["total_cost"]

    # The machine is a work centre: its cost-centre tags the WIP/greige lines so
    # production cost accumulates per machine in the ledger.
    from app.services import work_centres
    cc_id = work_centres.machine_cost_centre_id(db, kp.company_id, kp.machine_id)

    # 2) Accounting: yarn -> WIP, then WIP -> greige inventory.
    issue_voucher = pe.post_voucher(db, pe.PostingRequest(
        company_id=kp.company_id, voucher_type_code="PJ", voucher_date=kp.production_date,
        posting_reference=issue_ref, narration=f"Yarn issue for knitting {kp.number}",
        source_module="knitting", source_id=str(kp.id), created_by=created_by,
        lines=[
            pe.PostingLine(resolve_account(db, kp.company_id, "YARN_ISSUE", "wip"), debit=yarn_cost, cost_centre_id=cc_id),
            pe.PostingLine(resolve_account(db, kp.company_id, "YARN_ISSUE", "inventory"), credit=yarn_cost),
        ],
    ))
    produce_ref = f"KNIT:{kp.number}:produce"
    produce_voucher = pe.post_voucher(db, pe.PostingRequest(
        company_id=kp.company_id, voucher_type_code="PJ", voucher_date=kp.production_date,
        posting_reference=produce_ref, narration=f"Greige production {kp.number}",
        source_module="knitting", source_id=str(kp.id), created_by=created_by,
        lines=[
            pe.PostingLine(resolve_account(db, kp.company_id, "KNITTING_PRODUCTION", "greige"), debit=yarn_cost, cost_centre_id=cc_id),
            pe.PostingLine(resolve_account(db, kp.company_id, "KNITTING_PRODUCTION", "wip"), credit=yarn_cost, cost_centre_id=cc_id),
        ],
    ))

    # 3) Create greige rolls and receive them into the fabric sub-ledger at cost.
    total_kg = sum((Decimal(str(s["weight_kg"])) for s in roll_specs), Decimal("0"))
    if total_kg <= 0:
        total_kg = Decimal(str(kp.produced_kg)) or Decimal("1")
    per_kg_cost = quantize(yarn_cost / total_kg, MONEY_SCALE)

    machine = db.get(Machine, kp.machine_id)
    mcode = (machine.code if machine else "KN")
    fy = fiscal_year_for(kp.production_date)

    rolls = []
    for spec in roll_specs:
        # Auto-number when no roll number is supplied: GR-<machine>-<seq>.
        roll_no = (spec.get("roll_no") or "").strip()
        if not roll_no:
            seq = next_document_number(db, company_id=kp.company_id, doc_type="ROLL",
                                       fiscal_year=fy, prefix=f"GR-{mcode}-")
            roll_no = seq
        roll = FabricRoll(
            company_id=kp.company_id, roll_no=roll_no, kind=RollKind.GREIGE,
            knitting_production_id=kp.id, fabric_type_id=kp.fabric_type_id,
            machine_id=kp.machine_id, yarn_id=kp.yarn_id, yarn_lot_id=kp.yarn_lot_id,
            sales_order_id=kp.sales_order_id, weight_kg=spec["weight_kg"],
            length_m=spec.get("length_m"), gsm=spec.get("gsm"), width_cm=spec.get("width_cm"),
            warehouse_id=spec.get("warehouse_id"), barcode=spec.get("barcode") or roll_no,
        )
        db.add(roll)
        db.flush()
        inventory.receive(
            db, company_id=kp.company_id, item_type=ItemType.FABRIC, item_id=roll.id,
            qty=spec["weight_kg"], unit_cost=per_kg_cost, warehouse_id=spec.get("warehouse_id"),
            event_type="PRODUCE", source_module="knitting", source_id=str(kp.id),
            posting_reference=produce_ref,
        )
        rolls.append(roll)

    kp.posting_reference = issue_ref
    db.flush()
    return {
        "issue_voucher": issue_voucher,
        "produce_voucher": produce_voucher,
        "rolls": rolls,
        "yarn_cost": yarn_cost,
        "per_kg_cost": per_kg_cost,
    }
