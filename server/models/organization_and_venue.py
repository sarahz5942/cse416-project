"""Organization, OrganizationMember, and Venue models for CampusReserve."""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    String,
    Table,
    Text,
    CheckConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .base import Base


# --- Many-to-many: a venue host can manage several venues,
#     and a venue can have several hosts. No extra columns needed on the
#     join itself, so a plain Table (not a mapped class) is enough. ---
venue_hosts = Table(
    "venue_hosts",
    Base.metadata,
    Column(
        "user_id",
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "venue_id",
        UUID(as_uuid=True),
        ForeignKey("venues.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Organization(Base):
    """A registered student organization (club)."""

    __tablename__ = "organizations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # CHANGED: was `leaders = relationship("User", back_populates="organization")`,
    # a direct one-to-many that only allowed one org per leader. Now goes
    # through OrganizationMember, which is a real row with its own data
    # (position, term, verification) rather than just a pointer.
    memberships = relationship(
        "OrganizationMember", back_populates="organization", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Organization {self.name}>"


class OrganizationMember(Base):
    """
    One person's membership in one organization.

    This is an "association object": a many-to-many join that carries its own
    data, not just two foreign keys. It answers three questions a plain M:M
    table couldn't:
      - What's their role in the club? (position_title)
      - For how long is their access valid? (term_start/term_end -- NFR-2)
      - Have they actually been verified as a real E-board member, by whom,
        and when? (verified_at/verified_by -- NFR-1). A leader with
        verified_at = NULL should NOT be allowed to submit booking requests;
        enforce that check in the API layer, not here.
    """

    __tablename__ = "organization_members"

    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )

    position_title = Column(String(100), nullable=True)  # "President", "Treasurer", ...
    term_start = Column(Date, nullable=False, default=date.today)
    term_end = Column(Date, nullable=True)  # NULL = current/ongoing

    verified_at = Column(DateTime, nullable=True)  # NULL = not yet verified
    verified_by_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    user = relationship(
        "User", back_populates="organization_memberships", foreign_keys=[user_id]
    )
    organization = relationship("Organization", back_populates="memberships")
    verified_by = relationship("User", foreign_keys=[verified_by_id])

    __table_args__ = (
        CheckConstraint(
            "term_end IS NULL OR term_end >= term_start", name="ck_membership_term_order"
        ),
    )

    def __repr__(self) -> str:
        return f"<OrganizationMember user={self.user_id} org={self.organization_id}>"


class Venue(Base):
    """A physical building that contains bookable rooms."""

    __tablename__ = "venues"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), unique=True, nullable=False, index=True)
    address = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)

    # CHANGED: removed venue-level `capacity`. Your design doc calls rooms
    # (not venues) the bookable unit, and capacity is only meaningful per
    # room -- see Room.capacity in room.py. If you later want "total seats
    # in this building," compute it as sum(room.capacity) rather than
    # storing a second number that can drift out of sync.

    # Maps this venue to its identifier in the 25Live system of record (FR-11).
    external_ref = Column(String(255), nullable=True, index=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Many hosts per venue, and a host can manage several venues.
    hosts = relationship("User", secondary=venue_hosts, back_populates="venues")

    rooms = relationship("Room", back_populates="venue", cascade="all, delete-orphan")
    closures = relationship(
        "RoomClosure", back_populates="venue", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Venue {self.name}>"
