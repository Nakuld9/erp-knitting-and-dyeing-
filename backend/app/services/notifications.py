"""Create and read in-app notifications."""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.notifications import Notification


def notify(db: Session, *, company_id: uuid.UUID, title: str, message: str | None = None,
           kind: str = "INFO", user_id: uuid.UUID | None = None,
           link_doc_type: str | None = None, link_doc_id: uuid.UUID | None = None) -> Notification:
    n = Notification(company_id=company_id, user_id=user_id, kind=kind, title=title, message=message,
                     link_doc_type=link_doc_type, link_doc_id=link_doc_id, is_read=False)
    db.add(n)
    db.flush()
    return n


def unread_count(db: Session, company_id: uuid.UUID, user_id: uuid.UUID) -> int:
    return db.execute(
        select(func.count()).select_from(Notification).where(
            Notification.company_id == company_id, Notification.is_read.is_(False),
            ((Notification.user_id == user_id) | (Notification.user_id.is_(None))))
    ).scalar_one()


def list_notifications(db: Session, company_id: uuid.UUID, user_id: uuid.UUID, limit: int = 50):
    rows = db.execute(
        select(Notification).where(
            Notification.company_id == company_id,
            ((Notification.user_id == user_id) | (Notification.user_id.is_(None))))
        .order_by(Notification.created_at.desc())
    ).scalars().all()
    return rows[:limit]


def mark_read(db: Session, company_id: uuid.UUID, user_id: uuid.UUID,
              notification_id: uuid.UUID | None = None) -> int:
    stmt = select(Notification).where(
        Notification.company_id == company_id, Notification.is_read.is_(False),
        ((Notification.user_id == user_id) | (Notification.user_id.is_(None))))
    if notification_id is not None:
        stmt = stmt.where(Notification.id == notification_id)
    rows = db.execute(stmt).scalars().all()
    for n in rows:
        n.is_read = True
    db.flush()
    return len(rows)
