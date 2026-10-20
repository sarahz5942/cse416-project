"""
Importing this package registers every model with Base.metadata.

Without this, `Base.metadata.create_all(engine)` would create zero tables --
SQLAlchemy only knows about a class once its file has actually been run.
"""

from .base import Base

from .user import User, UserRole

from .organization_and_venue import (
    Organization,
    OrganizationMember,
    Venue,
    venue_hosts,
)

from .room import Room, RoomClosure, Request, RequestStatus, SyncStatus

from .communication import (
    Conversation,
    ConversationParticipant,
    Message,
    ForumPost,
    ForumCategory,
    Notification,
    NotificationType,
)

__all__ = [
    "Base",
    "User",
    "UserRole",
    "Organization",
    "OrganizationMember",
    "Venue",
    "venue_hosts",
    "Room",
    "RoomClosure",
    "Request",
    "RequestStatus",
    "SyncStatus",
    "Conversation",
    "ConversationParticipant",
    "Message",
    "ForumPost",
    "ForumCategory",
    "Notification",
    "NotificationType",
]
