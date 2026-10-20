"""Booking service: the rules and workflow for requests, approvals and the waitlist.

Endpoints call these functions; the transactional correctness lives here,
not in the route handlers. These are synchronous (SQLAlchemy sessions);
anything that needs 25Live is done by the caller first (see request_flow.py).

Times are naive campus local time throughout, matching 25Live.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from fastapi import HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.communication import NotificationType
from models.room import Request, RequestStatus, Room, RoomClosure, SyncStatus
from models.user import User
from services.live25_sync import SyncResult, booking_details
from services.notify import notify
from services.permissions import admins, as_uuid, org_leaders
from services.request_checks import RoomRules, Window

BLOCK_START = func.coalesce(Request.setup_start, Request.start_time)
BLOCK_END = func.coalesce(Request.cleanup_end, Request.end_time)

# Requests that still hold (or are waiting for) a slot.
OPEN_STATUSES = (RequestStatus.PENDING, RequestStatus.APPROVED, RequestStatus.WAITLISTED)


# ------------------------------------------------------------------ helpers

def window_of(req: Request) -> Window:
    setup = int((req.start_time - req.setup_start).total_seconds() // 60) if req.setup_start else 0
    cleanup = int((req.cleanup_end - req.end_time).total_seconds() // 60) if req.cleanup_end else 0
    return Window(req.start_time, req.end_time, setup, cleanup)


def rules_for(room: Room) -> RoomRules:
    return RoomRules(capacity=room.capacity, is_active=room.is_active,
                     club_bookable=room.club_bookable,
                     booking_opens_days_ahead=room.booking_opens_days_ahead)


def describe(req: Request) -> str:
    """'"Chess Night" in SAC 302 on Fri Oct 9, 6:00 PM'"""
    room = req.room.name if req.room else "a room"
    return f"\"{req.event_name}\" in {room} on {req.start_time.strftime('%a %b %-d, %-I:%M %p')}"


def get_request(db: Session, request_id) -> Request:
    req = db.get(Request, as_uuid(request_id, "request"))
    if req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")
    return req


def overlaps_in_db(db: Session, room: Room, start: dt.datetime, end: dt.datetime,
                   exclude_id=None) -> dict:
    """Our own approved/pending requests and closures overlapping [start, end)."""
    q = db.query(Request).filter(Request.room_id == room.id, BLOCK_START < end, BLOCK_END > start,
                                 Request.status.in_([RequestStatus.APPROVED, RequestStatus.PENDING]))
    if exclude_id is not None:
        q = q.filter(Request.id != exclude_id)
    approved, pending = [], []
    for r in q.all():
        w = window_of(r)
        item = {"id": r.id, "name": r.event_name, "start": w.blocked_start, "end": w.blocked_end}
        (approved if r.status == RequestStatus.APPROVED else pending).append(item)

    closures = [
        {"reason": c.reason, "start": c.closed_from, "end": c.closed_until}
        for c in db.query(RoomClosure).filter(
            or_(RoomClosure.room_id == room.id, RoomClosure.venue_id == room.venue_id),
            RoomClosure.closed_from < end, RoomClosure.closed_until > start)
    ]
    return {"approved": approved, "pending": pending, "closures": closures}


# ------------------------------------------------------------------ waitlist positions

def _overlapping_waitlist(db: Session, room_id, start, end, exclude_id=None):
    q = db.query(Request).filter(Request.room_id == room_id,
                                 Request.status == RequestStatus.WAITLISTED,
                                 BLOCK_START < end, BLOCK_END > start)
    if exclude_id is not None:
        q = q.filter(Request.id != exclude_id)
    return q.order_by(Request.waitlist_position, Request.created_at).all()


def next_waitlist_position(db: Session, room_id, start, end, exclude_id=None) -> int:
    """Last in line among waitlisted requests overlapping this time."""
    return 1 + max((r.waitlist_position or 0 for r in
                    _overlapping_waitlist(db, room_id, start, end, exclude_id)), default=0)


def renumber_waitlist(db: Session, room_id, start, end) -> None:
    """Close gaps after someone leaves the line: positions become 1, 2, 3..."""
    for i, r in enumerate(_overlapping_waitlist(db, room_id, start, end), start=1):
        r.waitlist_position = i


def _waitlist(db: Session, req: Request) -> None:
    w = window_of(req)
    req.waitlist_position = next_waitlist_position(db, req.room_id, w.blocked_start,
                                                   w.blocked_end, exclude_id=req.id)
    req.status = RequestStatus.WAITLISTED


# ------------------------------------------------------------------ create / edit

def create_request(db: Session, *, room: Room, organization_id, requester: User | None,
                   event_name: str, window: Window, attendance: int | None,
                   waitlisted: bool = False) -> Request:
    req = Request(
        room_id=room.id,
        organization_id=organization_id,
        requester_id=requester.id if requester else None,
        event_name=event_name,
        expected_attendance=attendance,
        start_time=window.start,
        end_time=window.end,
        setup_start=window.blocked_start if window.setup_minutes else None,
        cleanup_end=window.blocked_end if window.cleanup_minutes else None,
        status=RequestStatus.PENDING,
        sync_status=SyncStatus.NOT_SYNCED,
    )
    db.add(req)
    db.flush()  # assigns req.id
    if waitlisted:
        _waitlist(db, req)
    db.flush()
    db.refresh(req)

    if waitlisted:
        notify(db, org_leaders(db, organization_id) + [requester],
               NotificationType.REQUEST_WAITLISTED,
               f"{describe(req)} is on the waitlist at position {req.waitlist_position}. "
               f"We'll let you know if the room opens up.",
               request_id=req.id, email_subject="You're on the waitlist")
    else:
        notify(db, admins(db), NotificationType.REQUEST_SUBMITTED,
               f"New request to review: {describe(req)}.",
               request_id=req.id, email_subject="New booking request to review")
    return req


def update_request(db: Session, req: Request, *, event_name: str | None, window: Window,
                   attendance: int | None) -> Request:
    if req.status not in (RequestStatus.PENDING, RequestStatus.WAITLISTED):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Only pending or waitlisted requests can be edited.")
    old = window_of(req)
    if event_name is not None:
        req.event_name = event_name
    req.expected_attendance = attendance
    req.start_time, req.end_time = window.start, window.end
    req.setup_start = window.blocked_start if window.setup_minutes else None
    req.cleanup_end = window.blocked_end if window.cleanup_minutes else None
    if req.status == RequestStatus.WAITLISTED and (
            (old.blocked_start, old.blocked_end) != (window.blocked_start, window.blocked_end)):
        # A new time is a new place in a (possibly different) line.
        _waitlist(db, req)
        renumber_waitlist(db, req.room_id, old.blocked_start, old.blocked_end)
    db.flush()
    return req


# ------------------------------------------------------------------ decisions

def approve(db: Session, req: Request, *, admin: User, reason: str | None) -> list[Request]:
    """Approve a pending request IF no approved booking overlaps its room and
    blocked time (setup to cleanup). Overlapping pending requests can no longer
    be approved, so they move to the waitlist. Returns those.

    The overlap check and the approval are committed together, and Postgres's
    ck_no_double_booking constraint backs it up: two concurrent approvals of
    conflicting requests cannot both succeed.
    """
    if req.status != RequestStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Request is not pending.")
    w = window_of(req)
    found = overlaps_in_db(db, req.room, w.blocked_start, w.blocked_end, exclude_id=req.id)
    if found["approved"]:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="That room is already booked for an overlapping time.")

    req.status = RequestStatus.APPROVED
    req.decision_reason = reason
    req.decided_by_id = admin.id
    req.decided_at = dt.datetime.utcnow()
    req.waitlist_position = None
    req.sync_status = SyncStatus.NOT_SYNCED
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="That room was just approved for an overlapping time.")

    bumped = [get_request(db, p["id"]) for p in found["pending"]]
    for other in bumped:
        _waitlist(db, other)
    db.flush()

    notify(db, org_leaders(db, req.organization_id) + [req.requester],
           NotificationType.REQUEST_APPROVED,
           f"Approved: {describe(req)}." + (f" Note: {reason}" if reason else ""),
           request_id=req.id, email_subject="Your booking request was approved")
    for other in bumped:
        notify(db, org_leaders(db, other.organization_id) + [other.requester],
               NotificationType.REQUEST_WAITLISTED,
               f"{describe(other)} overlaps a booking that was just approved, so it moved "
               f"to the waitlist (position {other.waitlist_position}).",
               request_id=other.id, email_subject="Your request moved to the waitlist")
    return bumped


def deny(db: Session, req: Request, *, admin: User, reason: str) -> Request:
    if req.status not in (RequestStatus.PENDING, RequestStatus.WAITLISTED):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Only pending or waitlisted requests can be denied.")
    if not reason or not reason.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="A reason is required to deny a request.")
    was_waitlisted = req.status == RequestStatus.WAITLISTED
    w = window_of(req)
    req.status = RequestStatus.DENIED
    req.decision_reason = reason.strip()
    req.decided_by_id = admin.id
    req.decided_at = dt.datetime.utcnow()
    req.waitlist_position = None
    db.flush()
    if was_waitlisted:
        renumber_waitlist(db, req.room_id, w.blocked_start, w.blocked_end)
    notify(db, org_leaders(db, req.organization_id) + [req.requester],
           NotificationType.REQUEST_DENIED,
           f"Denied: {describe(req)}. Reason: {req.decision_reason}",
           request_id=req.id, email_subject="Your booking request was denied")
    return req


def cancel_request(db: Session, req: Request, *, by: User) -> RequestStatus:
    """Cancel/withdraw. Returns the status it had, so callers know whether a
    slot opened up (APPROVED) or a 25Live booking needs cancelling."""
    if req.status not in OPEN_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"This request is already {req.status.value}.")
    previous = req.status
    w = window_of(req)
    req.status = RequestStatus.CANCELLED
    req.waitlist_position = None
    db.flush()
    if previous == RequestStatus.WAITLISTED:
        renumber_waitlist(db, req.room_id, w.blocked_start, w.blocked_end)
    others = [u for u in org_leaders(db, req.organization_id) + [req.requester]
              if u is not None and u.id != by.id]
    notify(db, others, NotificationType.REQUEST_CANCELLED,
           f"Cancelled by {by.name}: {describe(req)}.",
           request_id=req.id, email_subject="A booking was cancelled")
    return previous


def join_waitlist(db: Session, req: Request) -> Request:
    if req.status != RequestStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Only a pending request can join the waitlist.")
    _waitlist(db, req)
    db.flush()
    return req


def promote_from_waitlist(db: Session, req: Request) -> Request:
    """The slot opened up: back to pending (in the review queue), and tell them."""
    w = window_of(req)
    req.status = RequestStatus.PENDING
    req.waitlist_position = None
    db.flush()
    renumber_waitlist(db, req.room_id, w.blocked_start, w.blocked_end)
    notify(db, org_leaders(db, req.organization_id) + [req.requester],
           NotificationType.WAITLIST_PROMOTED,
           f"Good news: the room opened up for {describe(req)}. Your request is back in "
           f"review.", request_id=req.id, email_subject="A room you wanted opened up")
    notify(db, admins(db), NotificationType.REQUEST_SUBMITTED,
           f"Off the waitlist, ready for review: {describe(req)}.",
           request_id=req.id, email=False)
    return req


# ------------------------------------------------------------------ 25Live sync

def sync_details(req: Request) -> dict:
    return booking_details(
        event_name=req.event_name,
        organization=req.organization.name if req.organization else "",
        space_id=req.room.external_ref if req.room else None,
        room_name=req.room.name if req.room else "",
        start=req.start_time, end=req.end_time,
        setup_start=req.setup_start, cleanup_end=req.cleanup_end,
        attendance=req.expected_attendance, request_id=str(req.id),
    )


def apply_sync_result(db: Session, req: Request, result: SyncResult) -> None:
    req.sync_status = result.status
    if result.external_ref:
        req.external_booking_ref = result.external_ref
    if result.status == SyncStatus.FAILED:
        notify(db, admins(db), NotificationType.SYNC_FAILED,
               f"Couldn't add {describe(req)} to 25Live: {result.message}",
               request_id=req.id, email_subject="25Live sync failed")
    elif result.status == SyncStatus.NOT_SYNCED and result.instructions:
        notify(db, admins(db), NotificationType.SYNC_NEEDED,
               f"Enter in 25Live: {describe(req)}. See GET /admin/sync for the details.",
               request_id=req.id, email_subject="A booking needs entering in 25Live")
    db.flush()


def confirm_manual_sync(db: Session, req: Request, external_ref: str) -> Request:
    if req.status != RequestStatus.APPROVED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Only approved requests are synced to 25Live.")
    if not external_ref or not external_ref.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Enter the 25Live event reference.")
    req.external_booking_ref = external_ref.strip()
    req.sync_status = SyncStatus.SYNCED
    db.flush()
    return req


# ------------------------------------------------------------------ original API
# These keep the original booking-service functions (and their signatures)
# working, for existing callers and tests. They run on the new logic above.
# New code should use create_request / approve / deny directly.

def _user_or_id(db: Session, user_id):
    """The User for an id, or a stand-in carrying just the id."""
    if user_id is None:
        return None
    uid = as_uuid(user_id, "user")
    return db.get(User, uid) or SimpleNamespace(id=uid, name="", email=None)


def submit_request(db: Session, *, room_id, organization_id, requester_id,
                   event_name, start_time, end_time) -> Request:
    """Original API: create a pending request (no availability check; the
    API's POST /requests does that before calling create_request)."""
    if end_time <= start_time:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="End time must be after start time.")
    room = db.get(Room, as_uuid(room_id, "room"))
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found.")
    req = create_request(db, room=room, organization_id=as_uuid(organization_id, "organization"),
                         requester=_user_or_id(db, requester_id), event_name=event_name,
                         window=Window(start_time, end_time), attendance=None)
    db.commit()
    db.refresh(req)
    return req


def approve_request(db: Session, *, request_id, admin_id, reason: str | None = None) -> Request:
    """Original API: approve by id (409 if not pending or it overlaps an
    approved booking). Overlapping pending requests move to the waitlist."""
    req = get_request(db, request_id)
    approve(db, req, admin=_user_or_id(db, admin_id), reason=reason)
    db.commit()
    db.refresh(req)
    return req


def deny_request(db: Session, *, request_id, admin_id, reason: str) -> Request:
    """Original API: deny by id with a required reason."""
    req = get_request(db, request_id)
    deny(db, req, admin=_user_or_id(db, admin_id), reason=reason)
    db.commit()
    db.refresh(req)
    return req
