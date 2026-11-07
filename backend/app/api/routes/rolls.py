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
def list_rolls(kind: str | None = None, unbatched: bool = False,
               db: Session = Depends(get_db), user: User = Depends(require_permission("report.read"))):
    stmt = select(FabricRoll).where(FabricRoll.company_id == user.company_id)
    if kind:
        stmt = stmt.where(FabricRoll.kind == kind)
    if unbatched:
        stmt = stmt.where(FabricRoll.dye_batch_id.is_(None))
    rows = db.execute(stmt.order_by(FabricRoll.created_at.desc())).scalars().all()
    return [{"id": str(r.id), "roll_no": r.roll_no, "kind": r.kind, "weight_kg": str(r.weight_kg),
             "quality_status": r.quality_status, "dye_batch_id": str(r.dye_batch_id) if r.dye_batch_id else None,
             "barcode": r.barcode or r.roll_no} for r in rows[:200]]


@router.get("/{roll_id}/label")
def roll_label(roll_id: uuid.UUID, db: Session = Depends(get_db),
               user: User = Depends(require_permission("report.read"))) -> Response:
    try:
        pdf = roll_label_pdf(db, user.company_id, roll_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="roll-{roll_id}.pdf"'})
