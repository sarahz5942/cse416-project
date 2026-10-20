import asyncio
import random
from datetime import date, datetime, time, timedelta

from .base import (
    BookingSource,
    ConflictError,
    ExternalBooking,
    NotFoundError,
    SyncError,
    TwentyFiveLiveAdapter,
)


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


class MockTwentyFiveLive(TwentyFiveLiveAdapter):
    def __init__(self, latency: float = 0.0, failure_rate: float = 0.0, seed: int | None = None):
        self.latency = latency  # seconds added to delay a call
        self.failure_rate = failure_rate  # rate that a push/cancel fails randomly
        self._rng = random.Random(seed)
        self._forced_failures = 0
        self._bookings: dict[str, ExternalBooking] = {}
        self._counter = 0
        self.known_rooms: set[str] = set()  # if not empty, unknown rooms raise NotFoundError

    # For testing
    def fail_next(self, n: int = 1) -> None:
        """Make the next push/cancel call raise SyncError."""
        self._forced_failures = n

    def reset(self) -> None:
        self._bookings.clear()
        self._forced_failures = 0
        self._counter = 0

    def all_bookings(self) -> list[ExternalBooking]:
        return list(self._bookings.values())

    # Fake Registrar Data
    def add_registrar_booking(
        self, room_ref: str, start: datetime, end: datetime, title: str
    ) -> str:
        """Insert a booking that CampusReserve is not allowed to cancel."""
        return self._store(ExternalBooking(room_ref, start, end, title, BookingSource.REGISTRAR))

    def add_weekly_class(
        self,
        room_ref: str,
        first_day: date,
        start: time,
        end: time,
        weeks: int,
        title: str = "Academic class",
    ) -> None:
        """Sets a recurring class mimicking registrar data"""
        for w in range(weeks):
            day = first_day + timedelta(weeks=w)
            self.add_registrar_booking(
                room_ref, datetime.combine(day, start), datetime.combine(day, end), title
            )

    def add_closure(self, room_ref: str, start: datetime, end: datetime, reason: str) -> str:
        return self.add_registrar_booking(room_ref, start, end, f"CLOSED: {reason}")

    def seed_demo_data(self, anchor: date) -> None:
        self.known_rooms |= {"MOCK-ROOM-101", "MOCK-ROOM-102", "MOCK-ROOM-201"}
        self.add_weekly_class("MOCK-ROOM-101", anchor, time(10, 0), time(11, 20), weeks=15)
        self.add_weekly_class("MOCK-ROOM-102", anchor + timedelta(days=2), time(14, 0), time(15, 20), weeks=15)
        self.add_closure(
            "MOCK-ROOM-201",
            datetime.combine(anchor + timedelta(days=7), time(0, 0)),
            datetime.combine(anchor + timedelta(days=9), time(0, 0)),
            "building maintenance",
        )

    # Adapter interface
    async def get_availability(
        self, room_ref: str, start: datetime, end: datetime
    ) -> list[ExternalBooking]:
        self._validate(room_ref, start, end)
        await self._delay()
        return sorted(
            (b for b in self._bookings.values()
             if b.room_ref == room_ref and _overlaps(start, end, b.start, b.end)),
            key=lambda b: b.start,
        )

    async def push_booking(self, booking: ExternalBooking) -> str:
        self._validate(booking.room_ref, booking.start, booking.end)
        await self._delay()
        self._maybe_fail("push")
        clashes = [
            b for b in self._bookings.values()
            if b.room_ref == booking.room_ref and _overlaps(booking.start, booking.end, b.start, b.end)
        ]
        if clashes:
            raise ConflictError(f"{booking.room_ref} conflicts with '{clashes[0].title}'")
        return self._store(
            ExternalBooking(
                booking.room_ref, booking.start, booking.end, booking.title,
                BookingSource.CAMPUSRESERVE,
            )
        )

    async def cancel_booking(self, booking_ref: str) -> None:
        await self._delay()
        self._maybe_fail("cancel")
        existing = self._bookings.get(booking_ref)
        if existing is None:
            raise NotFoundError(f"Unknown booking {booking_ref}")
        if existing.source is BookingSource.REGISTRAR:
            raise SyncError("Registrar bookings cannot be cancelled from CampusReserve")
        del self._bookings[booking_ref]

    def _store(self, booking: ExternalBooking) -> str:
        self._counter += 1
        ref = f"MOCK-BKG-{self._counter:06d}"
        self._bookings[ref] = ExternalBooking(
            booking.room_ref, booking.start, booking.end, booking.title, booking.source, ref
        )
        return ref

    def _validate(self, room_ref: str, start: datetime, end: datetime) -> None:
        if end <= start:
            raise ValueError("end must be after start")
        if self.known_rooms and room_ref not in self.known_rooms:
            raise NotFoundError(f"Unknown room {room_ref}")

    async def _delay(self) -> None:
        if self.latency:
            await asyncio.sleep(self.latency)

    def _maybe_fail(self, op: str) -> None:
        if self._forced_failures > 0:
            self._forced_failures -= 1
            raise SyncError(f"Simulated 25Live {op} failure")
        if self.failure_rate and self._rng.random() < self.failure_rate:
            raise SyncError(f"Random simulated 25Live {op} failure")
