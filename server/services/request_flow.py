"""Glue between 25Live (async, network) and the booking service (sync, DB).

`check_booking` answers "can this room be booked then?" using every source:
25Live bookings and closed hours, our approved requests and closures, the
room's rules. `promote_waitlisted` re-checks waitlisted requests and moves
the first one in line back to pending when its slot opens up.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session, joinedload
from starlette.concurrency import run_in_threadpool

from models.room import Request, RequestStatus, Room
from services import live25
from services.booking import overlaps_in_db, promote_from_waitlist, rules_for, window_of
from services.request_checks import CheckResult, Problem, RoomRules, Window, evaluate
from services.room_merge import campus_now

logger = logging.getLogger(__name__)

WAITLIST_CHECK_SECONDS = 10 * 60


async def load_25live_rooms() -> tuple[list[dict], dict[str, dict]]:
    """25Live's room list and features (cached), for importing rooms."""
    spaces = await live25.list_spaces()
    try:
        info = await live25.list_location_info()
    except live25.Live25Error as e:
        logger.warning("Couldn't load 25Live room features: %s", e)
        info = {}
    return spaces, info


async def _live_bookings(space_id: str | None, window: Window) -> tuple[list[dict], str | None]:
    if not space_id or not space_id.isdigit():
        return [], None
    try:
        return await live25.get_bookings(space_id, window.blocked_start.date(),
                                         window.blocked_end.date()), None
    except live25.Live25Error as e:
        return [], str(e)


async def check_booking(db: Session, *, room: Room | None, space_id: str | None,
                        window: Window, attendance: int | None, is_admin: bool,
                        exclude_id=None, strict: bool = False,
                        fallback_rules: RoomRules | None = None) -> CheckResult:
    """Every rule for one booking. `strict`: if 25Live can't be reached,
    block (used when approving) instead of just warning (submitting)."""
    if room is not None:
        found_task = run_in_threadpool(overlaps_in_db, db, room, window.blocked_start,
                                       window.blocked_end, exclude_id)
    else:
        async def nothing():
            return {"approved": [], "pending": [], "closures": []}
        found_task = nothing()

    (live, live_error), found = await asyncio.gather(_live_bookings(space_id, window), found_task)
    result = evaluate(
        window=window,
        rules=rules_for(room) if room is not None else (fallback_rules or RoomRules()),
        now=campus_now(),
        live_bookings=live,
        approved_overlaps=found["approved"],
        closures=found["closures"],
        pending_overlaps=found["pending"],
        attendance=attendance,
        is_admin=is_admin,
    )
    if live_error:
        result.problems.append(Problem(
            "25live_unreachable",
            "Couldn't check 25Live just now" + (", so this can't be approved yet. Try again shortly."
                                                if strict else "; an admin will re-check it."),
            blocking=strict))
    return result


async def promote_waitlisted(db: Session) -> int:
    """Promote waitlisted requests whose slot has opened. Returns how many."""
    now = campus_now()

    def load():
        return (db.query(Request).options(joinedload(Request.room))
                .filter(Request.status == RequestStatus.WAITLISTED, Request.start_time > now)
                .order_by(Request.room_id, Request.waitlist_position, Request.created_at)
                .all())

    promoted = 0
    taken: dict = {}  # room_id -> windows promoted this pass (don't promote two that overlap)
    for req in await run_in_threadpool(load):
        w = window_of(req)
        if any(w.blocked_start < e and s < w.blocked_end for s, e in taken.get(req.room_id, [])):
            continue
        result = await check_booking(db, room=req.room, space_id=req.room.external_ref,
                                     window=w, attendance=req.expected_attendance,
                                     is_admin=True, exclude_id=req.id, strict=True)
        if not result.ok:
            continue
        await run_in_threadpool(promote_from_waitlist, db, req)
        await run_in_threadpool(db.commit)
        taken.setdefault(req.room_id, []).append((w.blocked_start, w.blocked_end))
        promoted += 1
    return promoted


async def run_waitlist_loop(get_db) -> None:
    """Every WAITLIST_CHECK_SECONDS, promote waitlisted requests whose slot
    opened (e.g. the 25Live event they were waiting on was cancelled).
    `get_db` is your database.get_db generator function."""
    await asyncio.sleep(60)  # let the 25Live cache warm up first
    while True:
        gen = get_db()
        db = next(gen)
        try:
            n = await promote_waitlisted(db)
            if n:
                logger.info("Promoted %d waitlisted request(s)", n)
        except Exception:
            logger.exception("Waitlist check failed")
            await run_in_threadpool(db.rollback)
        finally:
            gen.close()
        await asyncio.sleep(WAITLIST_CHECK_SECONDS)
