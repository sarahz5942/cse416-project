"""User model and role definitions for CampusReserve."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, String, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .base import Base  # your declarative Base lives here


class UserRole(str, enum.Enum):
    """The three user types in CampusReserve."""

    CLUB_LEADER = "club_leader"
    ADMIN = "admin"
    VENUE_HOST = "venue_host"


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=False)

    role = Column(
        # See room.py's `status` column for why values_callable is needed --
        # this stores "club_leader"/"admin"/... instead of "CLUB_LEADER".
        Enum(
            UserRole,
            name="user_role",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=UserRole.CLUB_LEADER,
    )

    # Access can be scoped / deactivated entirely (NFR-2, least privilege).
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # --- relationships ---

    # CHANGED: a single `organization_id` FK only allows one leader per org and
    # one org per leader, and had nowhere to put position/term/verification.
    # A club leader can now belong to several organizations (and an org has many
    # leaders) through OrganizationMember, which also carries per-org
    # verification (NFR-1) and term dates (NFR-2). See organization_and_venue.py.
    # foreign_keys is required here: OrganizationMember has TWO foreign keys
    # pointing at `users` (user_id, and verified_by_id -- who verified them),
    # so SQLAlchemy can't guess which one this relationship should follow
    # without being told explicitly.
    organization_memberships = relationship(
        "OrganizationMember",
        back_populates="user",
        foreign_keys="OrganizationMember.user_id",
        cascade="all, delete-orphan",
    )

    # Venue hosts <-> venues (many-to-many via the venue_hosts table). Unchanged.
    venues = relationship("Venue", secondary="venue_hosts", back_populates="hosts")

    requests = relationship(
        "Request", back_populates="requester", foreign_keys="Request.requester_id"
    )

    def __repr__(self) -> str:
        return f"<User {self.email} ({self.role.value})>"
