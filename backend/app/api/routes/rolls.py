"""Fabric roll listing and printable labels."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_permission
from app.models.core import User
from app.models.manufacturing import FabricRoll
from app.services.labels import roll_label_pdf

router = APIRouter(prefix="/api/rolls", tags=["rolls"])


@router.get("")
def list_rolls(kind: str | None = None, unbatched: bool = False, undelivered: bool = False,
               db: Session = Depends(get_db), user: User = Depends(require_permission("report.read"))):
    stmt = select(FabricRoll).where(FabricRoll.company_id == user.company_id)
    if kind:
        stmt = stmt.where(FabricRoll.kind == kind)
    if unbatched:
        stmt = stmt.where(FabricRoll.dye_batch_id.is_(None))
    if undelivered:
        stmt = stmt.where(FabricRoll.delivery_note_id.is_(None))
    # Pickers only offer live rolls — exclude ones superseded by split/merge.
    if unbatched or undelivered:
        stmt = stmt.where(FabricRoll.status == "ACTIVE")
    from app.models.masters import FabricType, Machine, YarnMaster
    rows = db.execute(stmt.order_by(FabricRoll.created_at.desc())).scalars().all()
    out = []
    for r in rows[:200]:
        machine = db.get(Machine, r.machine_id) if r.machine_id else None
        yarn = db.get(YarnMaster, r.yarn_id) if r.yarn_id else None
        fabric = db.get(FabricType, r.fabric_type_id) if r.fabric_type_id else None
        out.append({"id": str(r.id), "roll_no": r.roll_no, "kind": r.kind, "weight_kg": str(r.weight_kg),
                    "quality_status": r.quality_status,
                    "machine": machine.code if machine else None,
                    "yarn": yarn.name if yarn else None,
                    "fabric": fabric.name if fabric else None,
                    "dye_batch_id": str(r.dye_batch_id) if r.dye_batch_id else None,
                    "delivered": r.delivery_note_id is not None,
                    "status": r.status,
                    "barcode": r.barcode or r.roll_no})
    return out


@router.get("/{roll_id}/label")
def roll_label(roll_id: uuid.UUID, db: Session = Depends(get_db),
               user: User = Depends(require_permission("report.read"))) -> Response:
    try:
        pdf = roll_label_pdf(db, user.company_id, roll_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="roll-{roll_id}.pdf"'})
