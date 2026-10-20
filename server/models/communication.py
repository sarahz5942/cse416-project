"""Conversation, Message, ForumPost, and Notification models for CampusReserve."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, ForeignKey, String, Text, Boolean, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .base import Base


class Conversation(Base):
    """
    A message thread between two or more users.

    ADDED: previously Message just carried a bare `conversation_id` UUID with
    no table behind it. That meant two things you actually want couldn't be
    built: FR-8's "automatically create a chat when two clubs book back to
    back" (there was nowhere to link a conversation to the Request that
    triggered it), and a group thread with more than 2 people (the old
    Message had a single `recipient_id`, so only 1-on-1 worked). A real
    Conversation table plus a participants join table fixes both.
    """

    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Optional: set when this thread was auto-created because two
    # organizations are booked back-to-back in the same room (FR-8). NULL for
    # a conversation someone started manually (e.g. from the forum, FR-9).
    request_id = Column(
        UUID(as_uuid=True), ForeignKey("requests.id", ondelete="SET NULL"), nullable=True
    )
    title = Column(String(255), nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    request = relationship("Request")
    participants = relationship(
        "ConversationParticipant", back_populates="conversation", cascade="all, delete-orphan"
    )
    messages = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )

    def __repr__(self) -> str:
        return f"<Conversation {self.id}>"


class ConversationParticipant(Base):
    """Who is in a conversation. A join table so a thread can hold any number of people."""

    __tablename__ = "conversation_participants"

    conversation_id = Column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )

    conversation = relationship("Conversation", back_populates="participants")
    user = relationship("User")


class Message(Base):
    """One message inside a conversation (FR-8)."""

    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # CHANGED: now a real foreign key into `conversations`, instead of a bare
    # UUID with nothing behind it.
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    sender_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # REMOVED: `recipient_id`. A message now belongs to a conversation, and
    # the conversation's participants (via ConversationParticipant) are who
    # can read it -- that supports a group thread, not just 1-on-1.

    body = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="messages")
    sender = relationship("User")

    __table_args__ = (
        # Fetch a conversation's messages in order.
        Index("ix_message_conversation_time", "conversation_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Message {self.id} conv={self.conversation_id}>"


class ForumCategory(str, enum.Enum):
    """What kind of forum post this is (FR-9)."""

    COLLABORATION = "collaboration"
    RESOURCE_SHARE = "resource_share"
    GENERAL = "general"


class ForumPost(Base):
    """A collaboration-forum post or reply (FR-9). Self-referential for threading."""

    __tablename__ = "forum_posts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    author_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    organization_id = Column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )

    # Null parent = a top-level post; a set parent = a reply to that post.
    parent_id = Column(
        UUID(as_uuid=True), ForeignKey("forum_posts.id", ondelete="CASCADE"), nullable=True, index=True
    )

    # ADDED: lets the forum distinguish "I want to collaborate on an event"
    # from "I have storage space to share" (FR-9's two examples) so the UI
    # can filter/tab by category instead of showing one undifferentiated feed.
    category = Column(
        Enum(
            ForumCategory,
            name="forum_category",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=ForumCategory.GENERAL,
    )

    title = Column(String(255), nullable=True)  # top-level posts have a title; replies usually don't
    body = Column(Text, nullable=False)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    author = relationship("User")
    organization = relationship("Organization")
    replies = relationship(
        "ForumPost",
        backref="parent",
        remote_side=[id],
        cascade="all, delete-orphan",
        single_parent=True,
    )

    def __repr__(self) -> str:
        kind = "reply" if self.parent_id else "post"
        return f"<ForumPost {self.id} ({kind})>"


class NotificationType(str, enum.Enum):
    """What a notification is about (FR-7)."""

    REQUEST_APPROVED = "request_approved"
    REQUEST_DENIED = "request_denied"
    WAITLIST_PROMOTED = "waitlist_promoted"
    NEW_MESSAGE = "new_message"
    # ADDED for the request/admin workflow:
    REQUEST_SUBMITTED = "request_submitted"      # to admins: a request needs review
    REQUEST_WAITLISTED = "request_waitlisted"    # to the org: you're in line for a slot
    REQUEST_CANCELLED = "request_cancelled"      # to the org: a booking was cancelled
    SYNC_NEEDED = "sync_needed"                  # to admins: enter an approval in 25Live
    SYNC_FAILED = "sync_failed"                  # to admins: pushing to 25Live failed
    MEMBERSHIP_REQUESTED = "membership_requested"  # to admins: someone wants verifying
    MEMBERSHIP_VERIFIED = "membership_verified"    # to the user: you can now book


class Notification(Base):
    """An in-app notification for a user (FR-7). services/notify.py also emails it."""

    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    type = Column(
        Enum(
            NotificationType,
            name="notification_type",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
    )
    message = Column(String(512), nullable=False)  # human-readable text shown to the user

    request_id = Column(
        UUID(as_uuid=True), ForeignKey("requests.id", ondelete="CASCADE"), nullable=True
    )

    is_read = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    user = relationship("User")
    request = relationship("Request")

    __table_args__ = (
        Index("ix_notification_user_read", "user_id", "is_read"),
    )

    def __repr__(self) -> str:
        return f"<Notification {self.type.value} -> {self.user_id}>"
