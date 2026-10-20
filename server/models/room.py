"""Room, RoomClosure, and Request (booking) models for CampusReserve."""

import enum
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    Boolean,
    CheckConstraint,
    UniqueConstraint,
    Index,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, ExcludeConstraint, TSTZRANGE, UUID
from sqlalchemy.orm import relationship

from .base import Base

# All booking times are naive campus-local time, matching 25Live.
CAMPUS_TZ = ZoneInfo("America/New_York")


class RequestStatus(str, enum.Enum):
    """Lifecycle of a booking request (Section 3: submitted -> pending -> decided)."""

    PENDING = "pending"  # submitted, awaiting an admin decision
    APPROVED = "approved"
    DENIED = "denied"
    CANCELLED = "cancelled"  # withdrawn by the requesting organization
    WAITLISTED = "waitlisted"  # queued for a full slot (FR-6)


class SyncStatus(str, enum.Enum):
    """State of pushing an approved booking to 25Live (FR-11)."""

    NOT_SYNCED = "not_synced"  # not yet pushed (e.g. still pending)
    SYNCED = "synced"  # successfully written to 25Live
    FAILED = "failed"  # push attempted but errored -- needs reconciliation


class Room(Base):
    """A bookable room within a venue."""

    __tablename__ = "rooms"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    venue_id = Column(
        UUID(as_uuid=True),
        ForeignKey("venues.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name = Column(String(255), nullable=False)  # e.g. "Room 236"
    capacity = Column(Integer, nullable=True)
    room_type = Column(String(100), nullable=True)  # e.g. "Meeting room", "Rehearsal space"

    # CHANGED: was freeform Text. A real array lets you filter/search on it
    # ("rooms with a projector") instead of string-matching. Still fine to
    # leave empty ({}) for rooms you haven't tagged yet.
    features = Column(ARRAY(String), nullable=False, default=list)

    media_url = Column(String(512), nullable=True)  # room photo / panorama (FR-10)

    # ADDED: direct user quote -- "specify what is bookable by whom (club vs.
    # administrative spaces)." A room an admin has marked False never shows
    # as bookable to a club leader, regardless of its schedule.
    club_bookable = Column(Boolean, nullable=False, default=True)
    # ADDED: lets a venue host or admin retire a room without deleting its
    # history of past requests.
    is_active = Column(Boolean, nullable=False, default=True)
    # ADDED: direct user quote -- "clearly label when the room bookings for
    # each location open." NULL = no advance-booking limit.
    booking_opens_days_ahead = Column(Integer, nullable=True)

    # Maps this room to its identifier in the 25Live system of record (FR-11).
    external_ref = Column(String(255), nullable=True, index=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    venue = relationship("Venue", back_populates="rooms")
    requests = relationship("Request", back_populates="room")
    closures = relationship("RoomClosure", back_populates="room", cascade="all, delete-orphan")

    __table_args__ = (
        # ADDED: two rooms in the same building shouldn't share a name --
        # catches data-entry mistakes early.
        UniqueConstraint("venue_id", "name", name="uq_room_venue_name"),
    )

    def __repr__(self) -> str:
        return f"<Room {self.name} @ {self.venue_id}>"


class RoomClosure(Base):
    """
    A time window when a room -- or an entire venue -- cannot be booked
    (building closed, room under maintenance, holiday).

    ADDED: this table didn't exist before. Without it there was no way to
    satisfy FR-2's "unbookable, with the reason shown" -- your app had
    nothing to check against, and nothing to explain the reason.
    Exactly one of room_id / venue_id must be set: a venue-level closure
    (e.g. "building closed for winter break") blocks every room inside it
    without you having to insert one row per room.
    """

    __tablename__ = "room_closures"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    room_id = Column(UUID(as_uuid=True), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=True)
    venue_id = Column(UUID(as_uuid=True), ForeignKey("venues.id", ondelete="CASCADE"), nullable=True)

    closed_from = Column(DateTime, nullable=False)
    closed_until = Column(DateTime, nullable=False)
    reason = Column(Text, nullable=False)

    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    room = relationship("Room", back_populates="closures")
    venue = relationship("Venue", back_populates="closures")
    created_by = relationship("User")

    __table_args__ = (
        CheckConstraint("closed_until > closed_from", name="ck_closure_time_order"),
        # exactly one of room_id / venue_id, never both, never neither
        CheckConstraint(
            "(room_id IS NULL) <> (venue_id IS NULL)", name="ck_closure_single_target"
        ),
    )

    def __repr__(self) -> str:
        target = f"room={self.room_id}" if self.room_id else f"venue={self.venue_id}"
        return f"<RoomClosure {target} {self.closed_from}..{self.closed_until}>"


class Request(Base):
    """A booking request for a room over a time window."""

    __tablename__ = "requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    room_id = Column(
        UUID(as_uuid=True),
        ForeignKey("rooms.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Who submitted it, and on behalf of which organization.
    requester_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    event_name = Column(String(255), nullable=False)
    expected_attendance = Column(Integer, nullable=True)

    # Free start/end datetimes for the event itself...
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    # ...plus optional setup/cleanup buffers (FR-3), stored as the padded window.
    setup_start = Column(DateTime, nullable=True)
    cleanup_end = Column(DateTime, nullable=True)

    # ADDED: the actual window a room is blocked for -- start_time/end_time
    # widened by setup_start/cleanup_end when those are set. This column is
    # filled automatically (see the event listener below); the app never
    # writes to it directly. It exists so the exclusion constraint below has
    # a single range to compare, instead of four separate columns.
    blocked_during = Column(TSTZRANGE, nullable=False)

    status = Column(
        # values_callable: store "pending"/"approved"/... (the .value), not
        # "PENDING"/"APPROVED" (the member name, SQLAlchemy's default).
        # Without this, the raw-SQL CHECK/EXCLUDE constraints below -- which
        # compare against lowercase text -- would never match anything.
        Enum(
            RequestStatus,
            name="request_status",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=RequestStatus.PENDING,
        index=True,
    )

    # Admin decision (FR-5): a denial/approval must carry a reason.
    decision_reason = Column(Text, nullable=True)
    decided_by_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at = Column(DateTime, nullable=True)

    # Waitlist ordering (FR-6): position within a contested slot; null if not waitlisted.
    waitlist_position = Column(Integer, nullable=True)

    # 25Live sync bookkeeping (FR-11).
    sync_status = Column(
        Enum(
            SyncStatus,
            name="sync_status",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=SyncStatus.NOT_SYNCED,
    )
    external_booking_ref = Column(String(255), nullable=True)  # id returned by 25Live

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    room = relationship("Room", back_populates="requests")
    requester = relationship("User", back_populates="requests", foreign_keys=[requester_id])
    decided_by = relationship("User", foreign_keys=[decided_by_id])
    organization = relationship("Organization")

    __table_args__ = (
        CheckConstraint("end_time > start_time", name="ck_request_time_order"),
        # ADDED: enforces FR-5 -- "must provide a reason" -- at the database
        # level, not just as an app-code convention that a future bug could skip.
        CheckConstraint(
            "status != 'denied' OR decision_reason IS NOT NULL",
            name="ck_denial_requires_reason",
        ),
        CheckConstraint(
            "(status = 'waitlisted') = (waitlist_position IS NOT NULL)",
            name="ck_waitlist_position_matches_status",
        ),
        Index("ix_request_room_time", "room_id", "start_time", "end_time"),

        # THE FIX for issue #1: no two APPROVED requests for the same room
        # may have overlapping blocked_during ranges. This is checked by
        # Postgres itself on every insert and update, so it holds even if
        # application code has a bug, even under concurrent approvals, even
        # for a row written outside the API. Two admins clicking "approve"
        # on overlapping requests at the same instant: one commits, the
        # other gets an IntegrityError back from the database.
        #
        # Requires the `btree_gist` Postgres extension (lets a plain "="
        # comparison share a GiST index with the range "&&" comparison) --
        # add `op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")` in
        # your first Alembic migration.
        ExcludeConstraint(
            (room_id, "="),
            (blocked_during, "&&"),
            where=text("status = 'approved'"),
            using="gist",
            name="ck_no_double_booking",
        ),
    )

    def __repr__(self) -> str:
        return f"<Request {self.id} room={self.room_id} {self.status.value}>"


@event.listens_for(Request, "before_insert")
@event.listens_for(Request, "before_update")
def _fill_blocked_during(mapper, connection, target: Request) -> None:
    """
    Keep `blocked_during` in sync with start/end/setup/cleanup automatically,
    so the app never has to remember to set it (and can't forget to, and
    accidentally break the exclusion constraint above).
    """
    lower = target.setup_start or target.start_time
    upper = target.cleanup_end or target.end_time
    # FIXED: times are stored as naive campus-local time (same as 25Live), but
    # blocked_during is a TSTZRANGE. Without a timezone, Postgres would read
    # them in the server's own timezone, which can shift the range by hours.
    if lower.tzinfo is None:
        lower = lower.replace(tzinfo=CAMPUS_TZ)
    if upper.tzinfo is None:
        upper = upper.replace(tzinfo=CAMPUS_TZ)
    target.blocked_during = f"[{lower.isoformat()},{upper.isoformat()})"
