"""25Live adapter interface (design doc, section 7).

The rest of CampusReserve talks to 25Live ONLY through `TwentyFiveLiveAdapter`.
Implementations: `MockTwentyFiveLive` (development/testing) and, later, a real
API or scraping client. Swapping one for the other must not touch callers.

Time convention: windows are half-open, [start, end). A booking that ends at
10:00 does not conflict with one that starts at 10:00. Pass the *padded*
window (setup_start .. cleanup_end) when the request has buffers, so the
whole blocked time is reserved.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class BookingSource(str, Enum):
    """Who created a booking in 25Live."""

    REGISTRAR = "registrar"  # academic scheduling, closures; we can never modify these
    CAMPUSRESERVE = "campusreserve"  # pushed by us


@dataclass(frozen=True)
class ExternalBooking:
    room_ref: str  # matches Room.external_ref
    start: datetime
    end: datetime
    title: str
    source: BookingSource = BookingSource.CAMPUSRESERVE
    booking_ref: str | None = None  # assigned by 25Live; stored as Request.external_booking_ref


class SyncError(Exception):
    """Base class for any failure talking to 25Live. Callers should mark the
    request SyncStatus.FAILED when they catch this."""


class ConflictError(SyncError):
    """The requested window overlaps an existing 25Live booking."""


class NotFoundError(SyncError):
    """Unknown room or booking reference."""


class TwentyFiveLiveAdapter(ABC):
    @abstractmethod
    async def get_availability(
        self, room_ref: str, start: datetime, end: datetime
    ) -> list[ExternalBooking]:
        """Return existing bookings overlapping [start, end). Empty list = free."""

    @abstractmethod
    async def push_booking(self, booking: ExternalBooking) -> str:
        """Write a booking to 25Live and return its booking_ref.

        Raises ConflictError on overlap, SyncError on any other failure."""

    @abstractmethod
    async def cancel_booking(self, booking_ref: str) -> None:
        """Remove a CampusReserve-created booking (used on cancellation / reconciliation)."""
