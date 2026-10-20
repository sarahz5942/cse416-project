"""Room endpoints: list, detail, and schedule.

Each endpoint combines two sources:
- 25Live (official rooms and bookings), via services.live25
- our database (rooms we've configured, requests, waitlist, closures)

The merging itself lives in services.room_merge.

Room ids in URLs are 25Live space_ids (e.g. "1810"), matched to our rooms via
Room.external_ref. Rooms that exist only in our database use their UUID.
"""

import asyncio
import datetime as dt
import inspect
import logging
import uuid
from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Security, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from authentication import get_current_user
from database import get_db
from models.room import Request, RequestStatus, Room, RoomClosure, SyncStatus
from models.user import User
from services import live25, room_features
from services.room_features import RoomFilters
from services.room_merge import (
    booking_window_open,
    build_schedule,
    campus_now,
    is_free_in_25live,
    merge_rooms,
    to_local_naive,
    window_from_parts,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rooms", tags=["rooms"])


# =============================================================================
# Response models
# =============================================================================

class RoomResponse(BaseModel):
    id: str                        # 25Live space_id, or our UUID for local-only rooms
    db_id: str | None = None       # our Room.id, if we have this room in our DB
    venue_id: str | None = None    # our Venue.id, if we have this room in our DB
    building: str | None = None    # from 25Live, e.g. "Administration"
    name: str
    short_name: str | None = None  # 25Live's ALL-CAPS code
    capacity: int | None = None
    room_type: str | None = None
    features: list[str] = []       # our tags (in /search results: all derived tags)
    live25_features: list[str] = []    # 25Live's names, e.g. "Food Permitted"
    live25_categories: list[str] = []  # e.g. "Type - Meeting Room", "Campus - West"
    media_url: str | None = None
    club_bookable: bool = True
    is_active: bool = True
    booking_opens_days_ahead: int | None = None
    source: str                    # "25live", "campusreserve", or "both"
    pending_requests: int = 0
    waitlist_count: int = 0


class AvailableRoomResponse(RoomResponse):
    # Pending CampusReserve requests for this window. They don't block the
    # room, but tell the user they may be competing for it.
    overlapping_pending_requests: int = 0


class Live25Feature(BaseModel):
    id: int | None = None
    name: str | None = None        # None until listed in data/live25_lookups.json
    quantity: int | None = None


class OpeningHours(BaseModel):
    day: str | None = None
    open: str | None = None
    close: str | None = None


class RoomDetailResponse(BaseModel):
    id: str
    name: str | None = None
    short_name: str | None = None
    capacity: int | None = None
    building: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    room_type: str | None = None       # from the "Type - ..." category
    live25_features: list[str] = []    # names, e.g. "Food Permitted"
    live25_categories: list[str] = []  # names, e.g. "Type - Meeting Room"
    features: list[Live25Feature] = []     # ids and quantities (e.g. 75 chairs)
    categories: list[Live25Feature] = []
    comments: str | None = None        # room notes from 25Live
    instructions: str | None = None    # setup rules, e.g. "reset the furniture"
    hours: list[OpeningHours] = []
    layout_capacity: int | None = None
    layout_photo_id: int | None = None
    layout_diagram_id: int | None = None
    tags: list[str] = []               # our feature tags derived from the above


class ScheduleItem(BaseModel):
    source: str                    # "25live" or "campusreserve"
    kind: str                      # "event", "closed", "request", or "waitlist"
    status: str | None = None      # 25Live: Confirmed/Tentative; ours: pending/approved/...
    name: str                      # event name, or the closure reason
    start: dt.datetime             # room blocked from (includes setup)
    end: dt.datetime               # room blocked until (includes cleanup)
    event_start: dt.datetime
    event_end: dt.datetime
    expected_headcount: int | None = None
    request_id: str | None = None
    waitlist_position: int | None = None
    waitlist_count: int = 0
    conflicts_with_25live: bool = False


# =============================================================================
# Database queries (sync SQLAlchemy, so the routes run them in a threadpool)
# =============================================================================

def _public_id(external_ref: str | None, room_id) -> str:
    return external_ref or str(room_id)


def _db_rooms(db: Session) -> dict[str, dict]:
    return {
        _public_id(r.external_ref, r.id): {
            "db_id": str(r.id),
            "venue_id": str(r.venue_id),
            "name": r.name,
            "capacity": r.capacity,
            "room_type": r.room_type,
            "features": list(r.features or []),
            "media_url": r.media_url,
            "club_bookable": r.club_bookable,
            "is_active": r.is_active,
            "booking_opens_days_ahead": r.booking_opens_days_ahead,
        }
        for r in db.query(Room).all()
    }


def _count_by_room(db: Session, *conditions) -> dict[str, int]:
    rows = (
        db.query(Room.external_ref, Room.id, func.count(Request.id))
        .join(Request, Request.room_id == Room.id)
        .filter(Request.end_time >= campus_now(), *conditions)
        .group_by(Room.id, Room.external_ref)
        .all()
    )
    return {_public_id(ref, rid): n for ref, rid, n in rows}


def _room_list_data(db: Session):
    return (
        _db_rooms(db),
        _count_by_room(db, Request.status == RequestStatus.PENDING),
        _count_by_room(db, Request.status == RequestStatus.WAITLISTED),
    )


def _find_room(db: Session, public_id: str) -> Room | None:
    room = db.query(Room).filter(Room.external_ref == public_id).first()
    if room is None:
        try:
            room = db.get(Room, uuid.UUID(public_id))
        except ValueError:
            pass
    return room


def _request_dict(r: Request) -> dict:
    return {
        "id": r.id,
        "name": r.event_name,
        "status": r.status.value,
        # Blocked window (with setup/cleanup) vs. the event itself.
        "start": to_local_naive(r.setup_start or r.start_time),
        "end": to_local_naive(r.cleanup_end or r.end_time),
        "event_start": to_local_naive(r.start_time),
        "event_end": to_local_naive(r.end_time),
        "expected_headcount": r.expected_attendance,
        "waitlist_position": r.waitlist_position,
    }


def _schedule_data(db: Session, public_id: str, start: dt.datetime, end: dt.datetime):
    room = _find_room(db, public_id)
    if room is None:
        return None, [], [], []

    # Requests whose blocked window (start/end widened by setup/cleanup)
    # overlaps [start, end).
    in_window = (
        func.coalesce(Request.setup_start, Request.start_time) < end,
        func.coalesce(Request.cleanup_end, Request.end_time) > start,
    )
    rows = (
        db.query(Request)
        .filter(Request.room_id == room.id, *in_window,
                or_(
                    Request.status.in_([RequestStatus.PENDING, RequestStatus.WAITLISTED]),
                    # Approved but not in 25Live yet: show it, or it would vanish
                    # from the schedule until the sync succeeds.
                    (Request.status == RequestStatus.APPROVED)
                    & (Request.sync_status != SyncStatus.SYNCED),
                ))
        .order_by(Request.waitlist_position.nulls_last(), Request.created_at)
        .all()
    )
    requests = [_request_dict(r) for r in rows if r.status != RequestStatus.WAITLISTED]
    waitlisted = [_request_dict(r) for r in rows if r.status == RequestStatus.WAITLISTED]

    closures = (
        db.query(RoomClosure)
        .filter(or_(RoomClosure.room_id == room.id, RoomClosure.venue_id == room.venue_id),
                RoomClosure.closed_from < end, RoomClosure.closed_until > start)
        .all()
    )
    closure_dicts = [{"name": c.reason, "start": to_local_naive(c.closed_from),
                      "end": to_local_naive(c.closed_until)} for c in closures]

    return room, requests, waitlisted, closure_dicts


def _availability_db_data(db: Session, start: dt.datetime, end: dt.datetime):
    """What our DB says about every room for [start, end).

    Returns (blocked, pending_counts), keyed by public room id:
      blocked:        rooms with an approved-but-not-yet-in-25Live request or a
                      closure overlapping the window
      pending_counts: number of pending requests overlapping the window
    """
    blocked: set[str] = set()

    request_rows = (
        db.query(Room.external_ref, Room.id, Request.status, Request.sync_status)
        .join(Request, Request.room_id == Room.id)
        .filter(func.coalesce(Request.setup_start, Request.start_time) < end,
                func.coalesce(Request.cleanup_end, Request.end_time) > start,
                Request.status.in_([RequestStatus.PENDING, RequestStatus.APPROVED]))
        .all()
    )
    pending_counts: dict[str, int] = {}
    for ref, rid, req_status, sync in request_rows:
        pid = _public_id(ref, rid)
        if req_status == RequestStatus.APPROVED and sync != SyncStatus.SYNCED:
            # Approved here but 25Live doesn't know yet, so 25Live still shows it free.
            blocked.add(pid)
        elif req_status == RequestStatus.PENDING:
            pending_counts[pid] = pending_counts.get(pid, 0) + 1

    closures = (
        db.query(RoomClosure)
        .filter(RoomClosure.closed_from < end, RoomClosure.closed_until > start)
        .all()
    )
    closed_room_ids = {c.room_id for c in closures if c.room_id}
    closed_venue_ids = {c.venue_id for c in closures if c.venue_id}
    if closed_room_ids or closed_venue_ids:
        for ref, rid in (
            db.query(Room.external_ref, Room.id)
            .filter(or_(Room.id.in_(closed_room_ids), Room.venue_id.in_(closed_venue_ids)))
            .all()
        ):
            blocked.add(_public_id(ref, rid))

    return blocked, pending_counts


# =============================================================================
# Routes
# =============================================================================

def _upstream_error(e: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                         detail=f"Couldn't reach 25Live: {e}")


async def _location_info() -> dict[str, dict]:
    """25Live features/categories by room. Optional: if it fails, rooms just
    have no 25Live features rather than the whole request failing."""
    try:
        return await live25.list_location_info()
    except live25.Live25Error as e:
        logger.warning("Couldn't load 25Live room features: %s", e)
        return {}


async def _merged_rooms(db: Session) -> list[dict]:
    # Fetch 25Live and query our DB at the same time.
    try:
        live_rooms, info, (db_rooms, pending, waitlist) = await asyncio.gather(
            live25.list_spaces(),
            _location_info(),
            run_in_threadpool(_room_list_data, db),
        )
    except live25.Live25Error as e:
        raise _upstream_error(e)
    for r in live_rooms:
        extra = info.get(r["id"])
        if extra:
            r["live25_features"] = extra["features"]
            r["live25_categories"] = extra["categories"]
            r["room_type"] = r.get("room_type") or extra["room_type"]
    return merge_rooms(live_rooms, db_rooms, pending, waitlist)


def _live25_names(room: dict) -> dict:
    """A room's 25Live feature/category names, in the shape tags_for() takes."""
    return {"features": [{"name": n} for n in room.get("live25_features") or []],
            "categories": [{"name": n} for n in room.get("live25_categories") or []]}


def _filter_rooms(rooms: list[dict], building: str | None, min_capacity: int | None,
                  club_bookable_only: bool, include_inactive: bool) -> list[dict]:
    if not include_inactive:
        rooms = [r for r in rooms if r["is_active"]]
    if club_bookable_only:
        rooms = [r for r in rooms if r["club_bookable"]]
    if building:
        b = building.lower()
        rooms = [r for r in rooms
                 if b in (r.get("building") or "").lower() or r.get("venue_id") == building]
    if min_capacity is not None:
        rooms = [r for r in rooms if (r.get("capacity") or 0) >= min_capacity]
    return rooms


@router.get("", response_model=list[RoomResponse])
async def list_rooms(
    building: str | None = None,
    min_capacity: int | None = None,
    club_bookable_only: bool = False,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Security(get_current_user, scopes=["read:rooms"]),
):
    """List rooms, optionally filtered by building, minimum capacity, or bookability."""
    rooms = _filter_rooms(await _merged_rooms(db), building, min_capacity,
                          club_bookable_only, include_inactive)
    return [RoomResponse(**r) for r in rooms]


def _parse_window(year: int, month: int, day: int,
                  start_time: str, end_time: str) -> tuple[dt.datetime, dt.datetime]:
    try:
        start, end = window_from_parts(year, month, day, start_time, end_time)
    except ValueError as e:
        msg = str(e) if '"' in str(e) else f"{month}/{day}/{year} isn't a valid date."
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
    if end <= campus_now():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="That time has already passed.")
    return start, end


async def _available_rooms(db: Session, start: dt.datetime, end: dt.datetime,
                           pick: Callable[[list[dict]], Any]) -> list[dict]:
    """Rooms chosen by `pick` (sync or async) that are free for [start, end).

    Shared by /available and /search so both use the same availability rules.
    """
    now = campus_now()
    (all_rooms, (blocked, pending_counts)) = await asyncio.gather(
        _merged_rooms(db),
        run_in_threadpool(_availability_db_data, db, start, end),
    )
    picked = pick(all_rooms)
    if inspect.isawaitable(picked):
        picked = await picked
    candidates = [r for r in picked
                  if r["id"] not in blocked and booking_window_open(r, start, now)]

    # Check every candidate's 25Live bookings. With the background refresh
    # running (live25.run_refresh_loop), dates in the next month come from
    # the cache; otherwise this fetches them, a few at a time.
    live_ids = [r["id"] for r in candidates if r["id"].isdigit()]
    last_day = (end - dt.timedelta(microseconds=1)).date()

    async def free_in_25live(space_id: str) -> bool | None:
        try:
            bookings = await live25.get_bookings(space_id, start.date(), last_day)
        except live25.Live25Error as e:
            logger.warning("Availability check failed for room %s: %s", space_id, e)
            return None
        return is_free_in_25live(bookings, start, end)

    results = dict(zip(live_ids, await asyncio.gather(*(free_in_25live(i) for i in live_ids))))
    if live_ids and all(v is None for v in results.values()):
        raise _upstream_error(live25.Live25Error("every availability check failed"))

    available = []
    for r in candidates:
        # Rooms only in our DB have no 25Live schedule; our own data decides.
        if r["id"] in results and results[r["id"]] is not True:
            continue  # booked in 25Live, or we couldn't check it
        available.append({**r, "overlapping_pending_requests": pending_counts.get(r["id"], 0)})
    return available


# Must be declared before "/{room_id}", or FastAPI would treat "available"
# as a room id.
@router.get("/available", response_model=list[AvailableRoomResponse])
async def list_available_rooms(
    year: int = Query(..., ge=2000, le=2100, examples=[2026]),
    month: int = Query(..., ge=1, le=12, examples=[10]),
    day: int = Query(..., ge=1, le=31, examples=[9]),
    start_time: str = Query(..., description='e.g. "6:00 PM" or "6pm"', examples=["6:00 PM"]),
    end_time: str = Query(..., description='e.g. "8:00 PM". If it is at or before '
                          'start_time, the window runs past midnight.', examples=["8:00 PM"]),
    building: str | None = None,
    min_capacity: int | None = None,
    club_bookable_only: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Security(get_current_user, scopes=["read:rooms"]),
):
    """Rooms that are free for the whole time window.

    Times are campus local time, e.g.
    ?year=2026&month=10&day=9&start_time=6:00 PM&end_time=8:00 PM

    A room is unavailable if any of these overlap the window: a 25Live event
    (confirmed or tentative, including its setup/teardown), 25Live closed
    hours, one of our approved requests not yet synced to 25Live, or one of
    our RoomClosures. Rooms whose booking window hasn't opened yet are also
    excluded. Pending requests don't block a room; they're reported in
    `overlapping_pending_requests`.

    Checks every matching room. Fast when the background refresh
    (live25.run_refresh_loop) is running; without it, the first search for a
    date fetches each room from 25Live and can take a while.
    """
    start, end = _parse_window(year, month, day, start_time, end_time)
    rooms = await _available_rooms(
        db, start, end,
        lambda all_rooms: _filter_rooms(all_rooms, building, min_capacity,
                                        club_bookable_only, include_inactive=False),
    )
    return [AvailableRoomResponse(**r) for r in rooms]


# Must also come before "/{room_id}".
@router.get("/search", response_model=list[AvailableRoomResponse])
async def search_rooms(
    year: int = Query(..., ge=2000, le=2100, examples=[2026]),
    month: int = Query(..., ge=1, le=12, examples=[10]),
    day: int = Query(..., ge=1, le=31, examples=[9]),
    start_time: str = Query(..., description='e.g. "6:00 PM" or "6pm"', examples=["6:00 PM"]),
    end_time: str = Query(..., description='e.g. "8:00 PM". If it is at or before '
                          'start_time, the window runs past midnight.', examples=["8:00 PM"]),
    attendance: int | None = Query(None, ge=1, description=(
        "Expected headcount. Only rooms that fit are returned, smallest first.")),
    building: str | None = None,
    min_capacity: int | None = Query(None, ge=0),
    max_capacity: int | None = Query(None, ge=0),
    room_type: str | None = Query(None, description='e.g. "Meeting room"'),
    setting: Literal["indoor", "outdoor"] | None = None,
    food_allowed: bool | None = None,
    crafts_allowed: bool | None = None,
    amplified_sound_allowed: bool | None = None,
    wheelchair_accessible: bool | None = None,
    projector: bool | None = None,
    whiteboard: bool | None = None,
    sound_system: bool | None = None,
    computers: bool | None = None,
    piano: bool | None = None,
    stage: bool | None = None,
    mirrors: bool | None = None,
    movable_furniture: bool | None = None,
    sink: bool | None = None,
    kitchen: bool | None = None,
    features: list[str] = Query(default=[], description=(
        "Any other feature tags the room must have. Repeat for several: "
        "&features=projector&features=sink")),
    club_bookable_only: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Security(get_current_user, scopes=["read:rooms"]),
):
    """Rooms that are free for the whole time window AND match the filters.

    Availability works exactly like /rooms/available.

    Yes/no filters: true = the room must have it, false = it must not,
    blank = don't care. Rooms count as not having a feature unless they're
    tagged with it, and as indoor unless tagged "outdoor". See
    services/room_features.py for the tags and where they come from.
    """
    start, end = _parse_window(year, month, day, start_time, end_time)

    yes_no = {
        "food_allowed": food_allowed,
        "crafts_allowed": crafts_allowed,
        "amplified_sound_allowed": amplified_sound_allowed,
        "wheelchair_accessible": wheelchair_accessible,
        "projector": projector,
        "whiteboard": whiteboard,
        "sound_system": sound_system,
        "computers": computers,
        "piano": piano,
        "stage": stage,
        "mirrors": mirrors,
        "movable_furniture": movable_furniture,
        "sink": sink,
        "kitchen": kitchen,
    }
    if attendance is not None:
        min_capacity = max(min_capacity or 0, attendance)
    filters = RoomFilters(
        setting=setting,
        yes_no={tag: v for tag, v in yes_no.items() if v is not None},
        required_tags={room_features.normalize(t) for t in features},
        room_type=room_type,
        min_capacity=min_capacity,
        max_capacity=max_capacity,
    )

    def pick(all_rooms: list[dict]) -> list[dict]:
        out = []
        for r in _filter_rooms(all_rooms, building, None, club_bookable_only,
                               include_inactive=False):
            # Tags from 25Live's feature/category names, our DB, and the file.
            tags = room_features.tags_for(r, _live25_names(r))
            if filters.matches(r, tags):
                out.append({**r, "features": sorted(tags)})
        return out

    rooms = await _available_rooms(db, start, end, pick)
    if attendance is not None:
        # Best fit first: the smallest room that holds everyone.
        rooms.sort(key=lambda r: (r.get("capacity") or 0, r["name"]))
    return [AvailableRoomResponse(**r) for r in rooms]


@router.get("/cache-status")
async def cache_status(current_user: User = Security(get_current_user, scopes=["read:rooms"])) -> dict:
    """Whether the 25Live cache is warm. Searches are slow until "ready" is true."""
    return live25.cache_status()


@router.get("/features")
async def list_features(current_user: User = Security(get_current_user, scopes=["read:rooms"])) -> dict[str, str]:
    """The feature tags rooms can have, with descriptions (for filter UIs)."""
    return room_features.FEATURES


class BuildingOut(BaseModel):
    name: str
    room_count: int
    venue_id: str | None = None        # our Venue, once its rooms are imported
    latitude: float | None = None
    longitude: float | None = None


class MapRoom(BaseModel):
    id: str
    name: str
    building: str | None
    capacity: int | None
    latitude: float | None
    longitude: float | None


async def _coordinates(space_ids: list[str]) -> dict[str, tuple]:
    """(lat, lon) per room, from 25Live room details (cached for a day)."""
    details = await live25.get_space_details(space_ids)
    return {i: (d.get("latitude"), d.get("longitude")) for i, d in details.items()}


@router.get("/buildings", response_model=list[BuildingOut])
async def list_buildings(
    include_location: bool = Query(False, description=(
        "Add coordinates (one 25Live lookup per building the first time; cached after)")),
    db: Session = Depends(get_db),
    current_user: User = Security(get_current_user, scopes=["read:rooms"]),
):
    """Buildings with active rooms, for a building picker or a campus map."""
    groups: dict[str, list[dict]] = {}
    for r in await _merged_rooms(db):
        if r["is_active"]:
            groups.setdefault(r.get("building") or "Other locations", []).append(r)
    coords = {}
    if include_location:
        firsts = {name: next((r["id"] for r in rooms if r["id"].isdigit()), None)
                  for name, rooms in groups.items()}
        found = await _coordinates([i for i in firsts.values() if i])
        coords = {name: found.get(i, (None, None)) for name, i in firsts.items() if i}
    return [BuildingOut(name=name, room_count=len(rooms),
                        venue_id=next((r["venue_id"] for r in rooms if r.get("venue_id")), None),
                        latitude=coords.get(name, (None, None))[0],
                        longitude=coords.get(name, (None, None))[1])
            for name, rooms in sorted(groups.items())]


@router.get("/map", response_model=list[MapRoom])
async def rooms_map(
    building: str = Query(..., description="Building name (or part of it)"),
    db: Session = Depends(get_db),
    current_user: User = Security(get_current_user, scopes=["read:rooms"]),
):
    """Rooms in a building with their coordinates, for a map view. One
    building at a time, since coordinates take a 25Live lookup per room
    (cached for a day)."""
    rooms = _filter_rooms(await _merged_rooms(db), building, None, False, False)
    coords = await _coordinates([r["id"] for r in rooms if r["id"].isdigit()])
    return [MapRoom(id=r["id"], name=r["name"], building=r.get("building"),
                    capacity=r.get("capacity"),
                    latitude=coords.get(r["id"], (None, None))[0],
                    longitude=coords.get(r["id"], (None, None))[1]) for r in rooms]


@router.get("/{room_id}/photo")
async def room_photo(
    room_id: str,
    current_user: User = Security(get_current_user, scopes=["read:rooms"]),
):
    """The room's layout photo from 25Live, served through our API, so the
    frontend can use it directly: <img src="/rooms/1810/photo">."""
    if not room_id.isdigit():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No photo for this room.")
    try:
        detail = await live25.get_space_detail(room_id)
        photo_id = (detail or {}).get("layout_photo_id")
        if not photo_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="This room has no photo in 25Live.")
        content, media_type = await live25.get_image(photo_id)
    except live25.Live25Error as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Couldn't load the photo from 25Live: {e}")
    return Response(content=content, media_type=media_type,
                    headers={"Cache-Control": "public, max-age=86400"})


@router.get("/{room_id}/details", response_model=RoomDetailResponse)
async def get_room_details(
    room_id: str,
    current_user: User = Security(get_current_user, scopes=["read:rooms"]),
):
    """25Live's details for a room: equipment, categories, hours, the room's
    rules/instructions, location, and the feature tags we derive from them."""
    if not room_id.isdigit():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Only 25Live rooms have 25Live details.")
    try:
        detail, info = await asyncio.gather(live25.get_space_detail(room_id), _location_info())
    except live25.Live25Error as e:
        raise _upstream_error(e)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found.")
    names = info.get(room_id) or {}
    room = {"id": room_id, "features": [],
            "live25_features": names.get("features", []),
            "live25_categories": names.get("categories", [])}
    tags = room_features.tags_for(room, _live25_names(room))
    return RoomDetailResponse(**detail, room_type=names.get("room_type"),
                              live25_features=room["live25_features"],
                              live25_categories=room["live25_categories"],
                              tags=sorted(tags))


@router.get("/{room_id}", response_model=RoomResponse)
async def get_room(
    room_id: str,
    db: Session = Depends(get_db),
    current_user: User = Security(get_current_user, scopes=["read:rooms"]),
):
    """Get a single room's details, with pending and waitlist counts."""
    room = next((r for r in await _merged_rooms(db) if r["id"] == room_id), None)
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found.")
    return RoomResponse(**room)


@router.get("/{room_id}/schedule", response_model=list[ScheduleItem])
async def get_room_schedule(
    room_id: str,
    start: dt.date | None = None,
    end: dt.date | None = None,
    include_closed: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Security(get_current_user, scopes=["read:rooms"]),
):
    """A room's 25Live bookings plus our requests, waitlist, and closures,
    from `start` (default today) through `end` (default a week later, max 30 days).

    `include_closed` adds 25Live's daily closed hours; our RoomClosures
    (maintenance, holidays) are always included.
    """
    start = start or campus_now().date()
    end = end or start + dt.timedelta(days=7)
    if end < start:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="end is before start.")
    end = min(end, start + dt.timedelta(days=30))
    window_start = dt.datetime.combine(start, dt.time())
    window_end = dt.datetime.combine(end + dt.timedelta(days=1), dt.time())

    # 25Live space_ids are numeric; anything else is a local-only room's UUID.
    is_25live_room = room_id.isdigit()

    async def fetch_live():
        return await live25.get_bookings(room_id, start, end) if is_25live_room else []

    try:
        live_bookings, (room, requests, waitlisted, closures) = await asyncio.gather(
            fetch_live(),
            run_in_threadpool(_schedule_data, db, room_id, window_start, window_end),
        )
    except live25.Live25Error as e:
        raise _upstream_error(e)

    if room is None and not is_25live_room:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found.")

    live_bookings = [b for b in live_bookings
                     if b["start"] < window_end and b["end"] > window_start
                     and (include_closed or b["kind"] != "closed")]
    items = build_schedule(live_bookings, requests, waitlisted, closures)
    return [ScheduleItem(**i) for i in items]
