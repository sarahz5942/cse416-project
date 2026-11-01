"""Room comments: tips clubs leave for each other on a room's page.

Comments show the club's name (not the person's). Verified club leaders post
as one of their clubs; admins post as "Administrator". The author, an admin,
or a venue host can delete a comment.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Security, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session, selectinload

from authentication import get_current_user
from database import get_db
from models.room_comment import RoomComment
from models.user import User
from services.permissions import as_uuid, has_scope, require_member

router = APIRouter(prefix="/rooms/{room_id}/comments", tags=["room comments"])

LIST_LIMIT = 200
MAX_PER_MINUTE = 3   # a simple spam brake per user


class CommentOut(BaseModel):
    id: str
    body: str
    posted_by: str                  # the club's name, or "Administrator"
    created_at: dt.datetime
    can_delete: bool


class CommentIn(BaseModel):
    body: str = Field(max_length=1000)
    organization_id: str | None = Field(None, description="Comment as this club (you must be a verified member)")

    @field_validator("body")
    @classmethod
    def _body(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("A comment can't be empty.")
        return v


def _room_ref(room_id: str) -> str:
    if not room_id or len(room_id) > 64:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found.")
    return room_id


def _moderator(user: User) -> bool:
    return has_scope(user, "handle:requests") or has_scope(user, "manage:rooms")


def _out(c: RoomComment, user: User, moderator: bool) -> CommentOut:
    return CommentOut(id=str(c.id), body=c.body,
                      posted_by=c.organization.name if c.organization else "Administrator",
                      created_at=c.created_at,
                      can_delete=moderator or c.author_id == user.id)


@router.get("", response_model=list[CommentOut])
def list_comments(
    room_id: str,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["read:rooms"]),
):
    """A room's comments, newest first."""
    rows = (db.query(RoomComment).filter(RoomComment.room_ref == _room_ref(room_id))
            .options(selectinload(RoomComment.organization))
            .order_by(RoomComment.created_at.desc()).limit(LIST_LIMIT).all())
    mod = _moderator(user)
    return [_out(c, user, mod) for c in rows]


@router.post("", response_model=CommentOut, status_code=status.HTTP_201_CREATED)
def add_comment(
    room_id: str,
    body: CommentIn,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["post:forum"]),
):
    """Leave a comment. Club leaders must say which club they're commenting as."""
    ref = _room_ref(room_id)
    is_admin = has_scope(user, "handle:requests")
    if body.organization_id:
        org_id = require_member(db, user, body.organization_id)
    elif is_admin:
        org_id = None
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Choose which club you're commenting as.")

    recent = (db.query(RoomComment).filter(RoomComment.author_id == user.id,
              RoomComment.created_at > dt.datetime.utcnow() - dt.timedelta(minutes=1)).count())
    if recent >= MAX_PER_MINUTE:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="You're commenting quickly. Try again in a minute.")

    c = RoomComment(room_ref=ref, author_id=user.id, organization_id=org_id, body=body.body)
    db.add(c)
    db.commit()
    db.refresh(c)
    return _out(c, user, _moderator(user))


@router.delete("/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_comment(
    room_id: str,
    comment_id: str,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["read:rooms"]),
):
    """Delete a comment: its author, an admin, or a venue host."""
    c = (db.query(RoomComment).filter(RoomComment.id == as_uuid(comment_id, "comment"),
         RoomComment.room_ref == _room_ref(room_id)).first())
    if c is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found.")
    if not (_moderator(user) or c.author_id == user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only delete your own comments.")
    db.delete(c)
    db.commit()
