"""In-app notifications (FR-7). Each one is also emailed; see services/notify.py."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, Security, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from authentication import get_current_user
from database import get_db
from models.communication import Notification
from models.user import User
from services.permissions import as_uuid

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationOut(BaseModel):
    id: str
    type: str
    message: str
    request_id: str | None
    is_read: bool
    created_at: dt.datetime


def _out(n: Notification) -> NotificationOut:
    return NotificationOut(id=str(n.id), type=n.type.value, message=n.message,
                           request_id=str(n.request_id) if n.request_id else None,
                           is_read=n.is_read, created_at=n.created_at)


@router.get("", response_model=list[NotificationOut])
def list_notifications(
    unread_only: bool = False,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Security(get_current_user),
):
    """Your notifications, newest first."""
    q = db.query(Notification).filter(Notification.user_id == user.id)
    if unread_only:
        q = q.filter(Notification.is_read.is_(False))
    return [_out(n) for n in q.order_by(Notification.created_at.desc()).limit(limit).all()]


@router.get("/unread-count")
def unread_count(db: Session = Depends(get_db), user: User = Security(get_current_user)) -> dict:
    """For the badge on the bell icon."""
    n = (db.query(Notification)
         .filter(Notification.user_id == user.id, Notification.is_read.is_(False)).count())
    return {"unread": n}


@router.post("/read-all")
def mark_all_read(db: Session = Depends(get_db), user: User = Security(get_current_user)) -> dict:
    n = (db.query(Notification)
         .filter(Notification.user_id == user.id, Notification.is_read.is_(False))
         .update({Notification.is_read: True}, synchronize_session=False))
    db.commit()
    return {"marked_read": n}


@router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_read(
    notification_id: str,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user),
):
    n = db.get(Notification, as_uuid(notification_id, "notification"))
    if n is None or n.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found.")
    n.is_read = True
    db.commit()
    db.refresh(n)
    return _out(n)
