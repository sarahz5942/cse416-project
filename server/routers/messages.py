"""Private messages between users (FR-8), and finding people to message.

A conversation is a thread with a list of participants (models.communication).
Only its participants can read or write in it, admins included.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, Response, Security, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, aliased, selectinload

from authentication import get_current_user
from database import get_db
from models.communication import Conversation, ConversationParticipant, Message, NotificationType
from models.user import User
from services.notify import notify
from services.pagination import Page, paged
from services.permissions import as_uuid

router = APIRouter(tags=["messages"])

# New messages are emailed only when the conversation has been quiet this long, so a
# back-and-forth chat sends one email instead of one per message.
EMAIL_QUIET_PERIOD = dt.timedelta(minutes=30)


# =============================================================================
# Schemas
# =============================================================================

class PersonOut(BaseModel):
    id: str
    name: str
    email: str


class ParticipantOut(BaseModel):
    id: str
    name: str


class MessageOut(BaseModel):
    id: str
    sender_id: str | None       # None if the sender's account was deleted
    sender_name: str | None
    body: str
    created_at: dt.datetime


class ConversationOut(BaseModel):
    id: str
    title: str | None
    request_id: str | None      # set when the thread was created for a booking
    participants: list[ParticipantOut]
    messages: list[MessageOut]  # oldest first


class ConversationIn(BaseModel):
    participant_ids: list[str] = Field(min_length=1, max_length=50,
                                       description="Everyone else in the thread (you are added automatically)")
    title: str | None = Field(None, max_length=255)


class MessageIn(BaseModel):
    body: str = Field(max_length=5000)

    @field_validator("body")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("A message can't be empty.")
        return v


def message_out(m: Message) -> MessageOut:
    return MessageOut(id=str(m.id), sender_id=str(m.sender_id) if m.sender_id else None,
                      sender_name=m.sender.name if m.sender else None,
                      body=m.body, created_at=m.created_at)


def conversation_out(c: Conversation) -> ConversationOut:
    people = sorted((p.user for p in c.participants if p.user), key=lambda u: u.name.lower())
    return ConversationOut(
        id=str(c.id), title=c.title, request_id=str(c.request_id) if c.request_id else None,
        participants=[ParticipantOut(id=str(u.id), name=u.name) for u in people],
        messages=[message_out(m) for m in c.messages])


def _load_options():
    return (selectinload(Conversation.participants).selectinload(ConversationParticipant.user),
            selectinload(Conversation.messages).selectinload(Message.sender))


def _recently_active(conv: Conversation, now: dt.datetime) -> bool:
    """True if the conversation's newest message is less than EMAIL_QUIET_PERIOD old."""
    if not conv.messages:
        return False
    last = conv.messages[-1].created_at          # messages are loaded oldest first
    if last is None:
        return False
    if last.tzinfo is None:                      # stored without a time zone: it's UTC
        last = last.replace(tzinfo=dt.timezone.utc)
    return now - last < EMAIL_QUIET_PERIOD


def _my_conversation(db: Session, user: User, conversation_id: str) -> Conversation:
    """The conversation, or 404 if it doesn't exist or you aren't in it (so a
    stranger can't tell which ids exist)."""
    conv = (db.query(Conversation)
            .join(ConversationParticipant, ConversationParticipant.conversation_id == Conversation.id)
            .filter(Conversation.id == as_uuid(conversation_id, "conversation"),
                    ConversationParticipant.user_id == user.id)
            .options(*_load_options()).first())
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    return conv


# =============================================================================
# Find people
# =============================================================================

@router.get("/users/search", response_model=list[PersonOut])
def search_users(
    response: Response,
    q: str = Query(..., min_length=1, max_length=100, description="Name or email contains"),
    page: Page = Depends(paged(default_limit=10, max_limit=50)),
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["read:messages"]),
):
    """Active users whose name or email contains `q`, for starting a chat. Never includes you."""
    term = q.strip().lower()
    if not term:
        return []
    rows = (db.query(User)
            .filter(User.is_active.is_(True), User.id != user.id,
                    or_(func.lower(User.name).contains(term, autoescape=True),
                        func.lower(User.email).contains(term, autoescape=True)))
            .order_by(User.name, User.id))
    rows = page.query(rows, response).all()
    return [PersonOut(id=str(u.id), name=u.name, email=u.email) for u in rows]


# =============================================================================
# Conversations
# =============================================================================

@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    response: Response,
    page: Page = Depends(paged()),
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["read:messages"]),
):
    """Your conversations with all their messages, most recently active first."""
    convs = (db.query(Conversation)
             .join(ConversationParticipant, ConversationParticipant.conversation_id == Conversation.id)
             .filter(ConversationParticipant.user_id == user.id)
             .options(*_load_options()).all())
    convs.sort(key=lambda c: c.messages[-1].created_at if c.messages else c.created_at, reverse=True)
    return [conversation_out(c) for c in page.items(convs, response)]


@router.post("/conversations", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
def start_conversation(
    body: ConversationIn,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["send:messages"]),
):
    """Start a chat with one or more people. A chat with a single person you
    already talk to returns the existing thread (200) instead of a second one."""
    ids = {as_uuid(i, "user") for i in body.participant_ids} - {user.id}
    if not ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pick at least one other person.")
    others = db.query(User).filter(User.id.in_(ids), User.is_active.is_(True)).all()
    if len(others) != len(ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="One of those people wasn't found.")

    if len(others) == 1:
        mine, theirs = aliased(ConversationParticipant), aliased(ConversationParticipant)
        existing = (db.query(Conversation)
                    .join(mine, mine.conversation_id == Conversation.id)
                    .join(theirs, theirs.conversation_id == Conversation.id)
                    .filter(mine.user_id == user.id, theirs.user_id == others[0].id,
                            Conversation.request_id.is_(None))
                    .options(*_load_options()).all())
        for conv in existing:
            if len(conv.participants) == 2:       # a 1:1, not a bigger group that includes both
                response.status_code = status.HTTP_200_OK
                return conversation_out(conv)

    conv = Conversation(title=body.title.strip() if body.title and body.title.strip() else None)
    db.add(conv)
    db.flush()
    for person in [user, *others]:
        db.add(ConversationParticipant(conversation_id=conv.id, user_id=person.id))
    db.commit()
    return conversation_out(_my_conversation(db, user, str(conv.id)))


@router.post("/conversations/{conversation_id}/messages", response_model=MessageOut,
             status_code=status.HTTP_201_CREATED)
def send_message(
    conversation_id: str,
    body: MessageIn,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["send:messages"]),
):
    """Add a message to a conversation you're in. The others get an in-app notification,
    and an email too unless the conversation already had a message in the last 30 minutes."""
    conv = _my_conversation(db, user, conversation_id)
    email_them = not _recently_active(conv, dt.datetime.now(dt.timezone.utc))  # check before adding this message
    msg = Message(conversation_id=conv.id, sender_id=user.id, body=body.body)
    db.add(msg)
    preview = body.body if len(body.body) <= 100 else body.body[:97] + "..."
    notify(db, [p.user for p in conv.participants if p.user_id != user.id],
           NotificationType.NEW_MESSAGE, f"{user.name}: {preview}",
           email=email_them, email_subject=f"New message from {user.name} on CampusReserve")
    db.commit()
    db.refresh(msg)
    return message_out(msg)
