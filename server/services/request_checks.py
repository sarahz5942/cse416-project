"""Can this room be booked for this time? The rules, as pure functions.

No database or network access here: callers gather the inputs (25Live
bookings, our approved requests and closures) and `evaluate` decides. That
keeps every rule testable on its own.

All datetimes are naive campus local time (America/New_York).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

MAX_WINDOW = dt.timedelta(hours=24)


@dataclass
class Window:
    """An event's time plus optional setup/cleanup padding."""

    start: dt.datetime
    end: dt.datetime
    setup_minutes: int = 0
    cleanup_minutes: int = 0

    @property
    def blocked_start(self) -> dt.datetime:
        return self.start - dt.timedelta(minutes=self.setup_minutes)

    @property
    def blocked_end(self) -> dt.datetime:
        return self.end + dt.timedelta(minutes=self.cleanup_minutes)


@dataclass
class RoomRules:
    capacity: int | None = None
    is_active: bool = True
    club_bookable: bool = True
    booking_opens_days_ahead: int | None = None


@dataclass
class Problem:
    code: str
    message: str
    blocking: bool = True
    # True if joining the waitlist makes sense (the slot is taken, but could
    # open up). False for things that won't change, like closed hours.
    waitlistable: bool = False

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "blocking": self.blocking,
                "waitlistable": self.waitlistable}


@dataclass
class CheckResult:
    problems: list[Problem] = field(default_factory=list)

    @property
    def blocking(self) -> list[Problem]:
        return [p for p in self.problems if p.blocking]

    @property
    def warnings(self) -> list[Problem]:
        return [p for p in self.problems if not p.blocking]

    @property
    def ok(self) -> bool:
        return not self.blocking

    @property
    def can_waitlist(self) -> bool:
        """The only things in the way are other bookings."""
        return bool(self.blocking) and all(p.waitlistable for p in self.blocking)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "can_waitlist": self.can_waitlist,
            "problems": [p.to_dict() for p in self.blocking],
            "warnings": [p.to_dict() for p in self.warnings],
        }


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


def _fmt(t: dt.datetime) -> str:
    """6:30 PM on Oct 9"""
    return t.strftime("%-I:%M %p on %b %-d")


def evaluate(
    *,
    window: Window,
    rules: RoomRules,
    now: dt.datetime,
    live_bookings: list[dict] = (),
    approved_overlaps: list[dict] = (),
    closures: list[dict] = (),
    pending_overlaps: list[dict] = (),
    attendance: int | None = None,
    is_admin: bool = False,
) -> CheckResult:
    """Check one booking against every rule.

    live_bookings:     25Live bookings for the room (from live25.get_bookings)
    approved_overlaps: our approved requests overlapping the blocked window,
                       as {"name", "start", "end"}
    closures:          our RoomClosures overlapping it, as {"reason", "start", "end"}
    pending_overlaps:  our pending requests overlapping it (a warning, not a block)
    is_admin:          admins may book rooms that aren't club-bookable
    """
    r = CheckResult()
    add = r.problems.append
    bs, be = window.blocked_start, window.blocked_end

    # --- the request itself ---
    if window.end <= window.start:
        add(Problem("invalid_time", "The end time must be after the start time."))
        return r
    if window.setup_minutes < 0 or window.cleanup_minutes < 0:
        add(Problem("invalid_time", "Setup and cleanup time can't be negative."))
        return r
    if be - bs > MAX_WINDOW:
        add(Problem("too_long", "A booking, including setup and cleanup, can be at most 24 hours."))
    if bs < now:
        add(Problem("in_past", "That time has already passed."))

    # --- the room's rules ---
    if not rules.is_active:
        add(Problem("room_inactive", "This room isn't available for booking."))
    if not rules.club_bookable and not is_admin:
        add(Problem("not_club_bookable", "This room can't be booked by student organizations."))
    if rules.booking_opens_days_ahead is not None:
        opens = (window.start - dt.timedelta(days=rules.booking_opens_days_ahead)).date()
        if now.date() < opens:
            add(Problem("not_open_yet",
                        f"Bookings for this date open on {opens.strftime('%b %-d, %Y')} "
                        f"({rules.booking_opens_days_ahead} days ahead)."))
    if attendance is not None and rules.capacity is not None and attendance > rules.capacity:
        add(Problem("over_capacity",
                    f"Expected attendance ({attendance}) is more than the room holds "
                    f"({rules.capacity})."))

    # --- other bookings and closures ---
    for b in live_bookings:
        if not _overlaps(bs, be, b["start"], b["end"]):
            continue
        if b.get("kind") == "closed":
            add(Problem("room_closed",
                        f"The room is closed from {_fmt(b['start'])} to {_fmt(b['end'])}."))
        elif b.get("state") not in ("Cancelled", "Deleted"):
            add(Problem("booked_in_25live",
                        f"Already booked in 25Live: \"{b.get('name') or 'an event'}\" "
                        f"({_fmt(b['start'])} to {_fmt(b['end'])}).",
                        waitlistable=True))
    for a in approved_overlaps:
        add(Problem("booked_in_campusreserve",
                    f"Already approved for another organization "
                    f"({_fmt(a['start'])} to {_fmt(a['end'])}).",
                    waitlistable=True))
    for c in closures:
        add(Problem("room_closure",
                    f"Closed from {_fmt(c['start'])} to {_fmt(c['end'])}: {c['reason']}"))
    if pending_overlaps:
        n = len(pending_overlaps)
        add(Problem("competing_requests",
                    f"{n} other pending request{'s' if n != 1 else ''} overlap{'' if n != 1 else 's'} "
                    f"this time. Only one can be approved.",
                    blocking=False))
    return r
