import asyncio
from datetime import date, datetime

import pytest

from integrations.twentyfive_live import (
    BookingSource,
    ConflictError,
    ExternalBooking,
    MockTwentyFiveLive,
    NotFoundError,
    SyncError,
)

MONDAY = date(2026, 10, 5)
ROOM = "MOCK-ROOM-101"


def run(coro):
    return asyncio.run(coro)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute)


@pytest.fixture
def mock():
    m = MockTwentyFiveLive(seed=1)
    m.seed_demo_data(MONDAY)
    return m


def test_availability_shows_registrar_class(mock):
    found = run(mock.get_availability(ROOM, at(5, 9), at(5, 12)))
    assert len(found) == 1 and found[0].source is BookingSource.REGISTRAR


def test_free_window_is_empty(mock):
    assert run(mock.get_availability(ROOM, at(5, 12), at(5, 13))) == []


def test_push_success_returns_ref_and_blocks_room(mock):
    ref = run(mock.push_booking(ExternalBooking(ROOM, at(5, 12), at(5, 13), "Club meeting")))
    assert ref.startswith("MOCK-BKG-")
    assert len(run(mock.get_availability(ROOM, at(5, 12), at(5, 13)))) == 1


def test_push_conflicts_with_registrar(mock):
    with pytest.raises(ConflictError):
        run(mock.push_booking(ExternalBooking(ROOM, at(5, 10, 30), at(5, 12), "Club")))



# At the moment, back to back booking is allowed. May need to add time in between for clean up/set up
def test_back_to_back_is_not_a_conflict(mock):
    run(mock.push_booking(ExternalBooking(ROOM, at(5, 11, 20), at(5, 12), "Club")))



def test_forced_failure_then_recovery(mock):
    b = ExternalBooking(ROOM, at(5, 12), at(5, 13), "Club")
    mock.fail_next()
    with pytest.raises(SyncError):
        run(mock.push_booking(b))
    assert run(mock.get_availability(ROOM, at(5, 12), at(5, 13))) == []
    assert run(mock.push_booking(b))  # retry works


def test_cancel_own_booking_frees_room(mock):
    ref = run(mock.push_booking(ExternalBooking(ROOM, at(5, 12), at(5, 13), "Club")))
    run(mock.cancel_booking(ref))
    assert run(mock.get_availability(ROOM, at(5, 12), at(5, 13))) == []


def test_cannot_cancel_registrar_booking(mock):
    ref = run(mock.get_availability(ROOM, at(5, 10), at(5, 11)))[0].booking_ref
    with pytest.raises(SyncError):
        run(mock.cancel_booking(ref))


def test_unknown_room_and_booking(mock):
    with pytest.raises(NotFoundError):
        run(mock.get_availability("NOPE", at(5, 9), at(5, 10)))
    with pytest.raises(NotFoundError):
        run(mock.cancel_booking("MOCK-BKG-999999"))


def test_invalid_window_rejected(mock):
    with pytest.raises(ValueError):
        run(mock.get_availability(ROOM, at(5, 10), at(5, 9)))


def test_closure_blocks_room(mock):
    with pytest.raises(ConflictError):
        run(mock.push_booking(ExternalBooking("MOCK-ROOM-201", at(12, 9), at(12, 10), "Club")))
