"""RoomComment: a short tip a club leaves on a room's page."""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .base import Base


class RoomComment(Base):
    __tablename__ = "room_comments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # The room's public id: a 25Live space_id (e.g. "1810") or our Room UUID.
    # Not a foreign key, because most rooms only exist in 25Live.
    room_ref = Column(String(64), nullable=False, index=True)

    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True)

    body = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    author = relationship("User")
    organization = relationship("Organization")
