"""Notifications: an in-app Notification row plus an email, per recipient.

Call `notify(db, ...)` inside your normal unit of work. The rows are added to
the session (you commit as usual), and the emails go out only AFTER that
commit succeeds, so nobody gets emailed about a change that was rolled back.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import event
from sqlalchemy.orm import Session

from models.communication import Notification, NotificationType
from models.user import User
from services.mailer import app_url, send_email

MAX_MESSAGE = 512  # Notification.message is String(512)


def notify(
    db: Session,
    users: Iterable[User],
    type_: NotificationType,
    message: str,
    *,
    request_id=None,
    email_subject: str | None = None,
    email: bool = True,
) -> None:
    """Notify each user once (duplicates are skipped)."""
    seen = set()
    for user in users:
        if user is None or user.id in seen:
            continue
        seen.add(user.id)
        db.add(Notification(user_id=user.id, type=type_, message=message[:MAX_MESSAGE],
                            request_id=request_id))
        if email and user.email:
            body = f"Hi {user.name},\n\n{message}\n\nOpen CampusReserve: {app_url()}\n"
            db.info.setdefault("pending_emails", []).append(
                (user.email, email_subject or "CampusReserve update", body))


@event.listens_for(Session, "after_commit")
def _send_after_commit(session: Session) -> None:
    for to, subject, body in session.info.pop("pending_emails", []):
        send_email(to, subject, body)


@event.listens_for(Session, "after_rollback")
def _drop_after_rollback(session: Session) -> None:
    session.info.pop("pending_emails", None)
