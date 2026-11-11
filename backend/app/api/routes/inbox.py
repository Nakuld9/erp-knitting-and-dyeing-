"""In-app notifications and document attachments."""
from __future__ import annotations

import base64
import binascii
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_permission
from app.models.core import User
from app.models.notifications import Attachment
from app.services import notifications as nsvc

router = APIRouter(prefix="/api", tags=["inbox"])

MAX_ATTACHMENT = 8 * 1024 * 1024   # 8 MB


# ------------------------------ notifications -------------------------------

@router.get("/notifications")
def list_notifications(db: Session = Depends(get_db),
                       user: User = Depends(require_permission("report.read"))):
    rows = nsvc.list_notifications(db, user.company_id, user.id)
    return [{"id": str(n.id), "kind": n.kind, "title": n.title, "message": n.message,
             "is_read": n.is_read, "created_at": n.created_at.isoformat() if n.created_at else None,
             "link_doc_type": n.link_doc_type,
             "link_doc_id": str(n.link_doc_id) if n.link_doc_id else None} for n in rows]


@router.get("/notifications/unread-count")
def unread_count(db: Session = Depends(get_db),
                 user: User = Depends(require_permission("report.read"))):
    return {"count": nsvc.unread_count(db, user.company_id, user.id)}


class MarkReadIn(BaseModel):
    notification_id: uuid.UUID | None = None   # omit to mark all read


@router.post("/notifications/mark-read")
def mark_read(payload: MarkReadIn, db: Session = Depends(get_db),
              user: User = Depends(require_permission("report.read"))):
    n = nsvc.mark_read(db, user.company_id, user.id, payload.notification_id)
    db.commit()
    return {"marked": n}


# ------------------------------- attachments --------------------------------

class AttachmentIn(BaseModel):
    doc_type: str
    doc_id: uuid.UUID
    filename: str
    content_type: str = "application/octet-stream"
    data_base64: str = Field(..., description="Base64-encoded file bytes")


@router.post("/attachments", status_code=status.HTTP_201_CREATED)
def upload_attachment(payload: AttachmentIn, db: Session = Depends(get_db),
                      user: User = Depends(require_permission("master.write"))):
    try:
        raw = base64.b64decode(payload.data_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "data_base64 is not valid base64")
    if not raw:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Empty file")
    if len(raw) > MAX_ATTACHMENT:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "File exceeds 8 MB")
    att = Attachment(company_id=user.company_id, doc_type=payload.doc_type, doc_id=payload.doc_id,
                     filename=payload.filename, content_type=payload.content_type, size=len(raw),
                     data=raw, uploaded_by=user.id)
    db.add(att)
    db.commit()
    return {"id": str(att.id), "filename": att.filename, "size": att.size}


@router.get("/attachments")
def list_attachments(doc_type: str, doc_id: uuid.UUID, db: Session = Depends(get_db),
                     user: User = Depends(require_permission("report.read"))):
    rows = db.execute(select(Attachment).where(
        Attachment.company_id == user.company_id, Attachment.doc_type == doc_type,
        Attachment.doc_id == doc_id).order_by(Attachment.created_at.desc())).scalars().all()
    return [{"id": str(a.id), "filename": a.filename, "content_type": a.content_type,
             "size": a.size} for a in rows]


@router.get("/attachments/{attachment_id}/download")
def download_attachment(attachment_id: uuid.UUID, db: Session = Depends(get_db),
                        user: User = Depends(require_permission("report.read"))) -> Response:
    a = db.get(Attachment, attachment_id)
    if a is None or a.company_id != user.company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    return Response(content=a.data, media_type=a.content_type,
                    headers={"Content-Disposition": f'attachment; filename="{a.filename}"'})
