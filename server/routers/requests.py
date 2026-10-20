"""Booking request endpoints: check, submit, view, edit, cancel, approve, deny, waitlist.
Room ids are public room ids: a 25Live space_id ("1810") or our Room UUID.
Times are campus local time (America/New_York); a timezone offset is
accepted and converted.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, Security, status
from pydantic import BaseModel, Field
from sqlalchemy import or_
from datetime import date
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from authentication import get_current_user
from database import get_db
from models.room import Request, RequestStatus
from models.user import User
from models.organization_and_venue import OrganizationMember
from services import booking
from services.live25_sync import get_provider
from services.permissions import (
    as_uuid,
    can_manage_venue,
    has_scope,
    require_member,
    require_scope,
    verified_org_ids,
)
from services.request_checks import RoomRules, Window
from services.request_flow import check_booking, load_25live_rooms, promote_waitlisted
from services.room_import import ensure_room, find_room
from services.room_merge import to_local_naive

router = APIRouter(prefix="/requests", tags=["requests"])


# =============================================================================
# Schemas
# =============================================================================

class RequestIn(BaseModel):
    organization_id: str
    room_id: str = Field(description='25Live space id like "1810", or our room UUID')
    event_name: str = Field(min_length=1, max_length=255)
    start_time: dt.datetime = Field(description="Event start, e.g. 2026-10-09T18:00")
    end_time: dt.datetime = Field(description="Event end, e.g. 2026-10-09T20:00")
    setup_minutes: int = Field(0, ge=0, le=240, description="Room needed this long before")
    cleanup_minutes: int = Field(0, ge=0, le=240, description="Room needed this long after")
    expected_attendance: int | None = Field(None, ge=1)
    join_waitlist_if_taken: bool = Field(
        False, description="If the room is already booked then, join the waitlist "
                           "instead of failing.")

    def window(self) -> Window:
        return Window(to_local_naive(self.start_time), to_local_naive(self.end_time),
                      self.setup_minutes, self.cleanup_minutes)


class RequestUpdate(BaseModel):
    event_name: str | None = Field(None, min_length=1, max_length=255)
    start_time: dt.datetime
    end_time: dt.datetime
    setup_minutes: int = Field(0, ge=0, le=240)
    cleanup_minutes: int = Field(0, ge=0, le=240)
    expected_attendance: int | None = Field(None, ge=1)
    join_waitlist_if_taken: bool = False

    def window(self) -> Window:
        return Window(to_local_naive(self.start_time), to_local_naive(self.end_time),
                      self.setup_minutes, self.cleanup_minutes)


class ApproveBody(BaseModel):
    reason: str | None = None


class DecisionBody(BaseModel):
    reason: str = Field(min_length=1)


class RequestOut(BaseModel):
    id: str
    room_id: str                    # public room id (25Live space id when known)
    room_db_id: str
    room_name: str | None = None
    organization_id: str
    organization_name: str | None = None
    requester_id: str | None = None
    requester_name: str | None = None
    event_name: str
    expected_attendance: int | None = None
    start_time: dt.datetime
    end_time: dt.datetime
    setup_start: dt.datetime | None = None
    cleanup_end: dt.datetime | None = None
    status: str
    decision_reason: str | None = None
    decided_at: dt.datetime | None = None
    waitlist_position: int | None = None
    sync_status: str
    external_booking_ref: str | None = None
    created_at: dt.datetime
    warnings: list[dict] = []       # e.g. other pending requests for the same time


class CheckOut(BaseModel):
    ok: bool
    can_waitlist: bool
    problems: list[dict]
    warnings: list[dict]


class SyncOut(BaseModel):
    mode: str
    status: str
    message: str
    instructions: dict = {}


class ApproveOut(BaseModel):
    request: RequestOut
    moved_to_waitlist: list[str]    # ids of overlapping requests that were waitlisted
    sync: SyncOut


class CancelOut(BaseModel):
    request: RequestOut
    sync: SyncOut | None = None     # what to do in 25Live, if it was booked there


def request_out(req: Request, warnings: list[dict] | None = None) -> RequestOut:
    room = req.room
    return RequestOut(
        id=str(req.id),
        room_id=(room.external_ref or str(room.id)) if room else str(req.room_id),
        room_db_id=str(req.room_id),
        room_name=room.name if room else None,
        organization_id=str(req.organization_id),
        organization_name=req.organization.name if req.organization else None,
        requester_id=str(req.requester_id) if req.requester_id else None,
        requester_name=req.requester.name if req.requester else None,
        event_name=req.event_name,
        expected_attendance=req.expected_attendance,
        start_time=req.start_time,
        end_time=req.end_time,
        setup_start=req.setup_start,
        cleanup_end=req.cleanup_end,
        status=req.status.value,
        decision_reason=req.decision_reason,
        decided_at=req.decided_at,
        waitlist_position=req.waitlist_position,
        sync_status=req.sync_status.value,
        external_booking_ref=req.external_booking_ref,
        created_at=req.created_at,
        warnings=warnings or [],
    )


# =============================================================================
# Access rules
# =============================================================================

def _is_member(db: Session, user: User, req: Request) -> bool:
    return req.organization_id in verified_org_ids(db, user)


def ensure_can_view(db: Session, user: User, req: Request) -> None:
    if (req.requester_id == user.id or has_scope(user, "handle:requests")
            or _is_member(db, user, req) or can_manage_venue(db, user, req.room.venue_id)):
        return
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")


def ensure_can_act(db: Session, user: User, req: Request) -> None:
    """Edit/cancel/waitlist: the organization's verified members, or admins."""
    if has_scope(user, "handle:requests") or _is_member(db, user, req):
        return
    ensure_can_view(db, user, req)  # 404 if they can't even see it
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                        detail="Only the organization's verified members can change this request.")


def _conflict(result, message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT,
                         detail={"message": message, **result.to_dict()})


async def _room_for(db: Session, public_id: str, *, create: bool):
    """(our Room or None, 25Live space id or None, fallback rules for unimported rooms)."""
    room = await run_in_threadpool(find_room, db, public_id)
    fallback = None
    if room is None and public_id.isdigit():
        spaces, info = await load_25live_rooms()
        space = next((s for s in spaces if s["id"] == public_id), None)
        if space is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found.")
        if create:
            room = await run_in_threadpool(ensure_room, db, public_id, spaces, info)
        else:
            fallback = RoomRules(capacity=space.get("capacity"))
    if room is None and fallback is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found.")
    space_id = room.external_ref if room is not None else public_id
    return room, space_id, fallback


def _primary_organization_id(user: User, db: Session) -> str:
    """
    CHANGED: `user.organization_id` was removed when OrganizationMember was
    introduced -- a user can belong to several organizations now, not just
    one, so there's no single column to read. This looks up their
    membership instead.

    While we're here, this also enforces NFR-1 (only a *verified* E-board
    member can book) and the term dates from NFR-2 -- neither was being
    checked anywhere before. An unverified "leader," or one whose term has
    ended, gets a clear 403 instead of silently being allowed to book.

    Assumes one active org per user for now, matching the original scope.
    Letting a user pick which org when they lead more than one is a good
    follow-up, not needed for the MVP.
    """
    membership = (
        db.query(OrganizationMember)
        .filter(
            OrganizationMember.user_id == user.id,
            OrganizationMember.verified_at.isnot(None),
        )
        .filter(
            (OrganizationMember.term_end.is_(None))
            | (OrganizationMember.term_end >= date.today())
        )
        .first()
    )
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a verified, active member of any organization.",
        )
    return str(membership.organization_id)


@router.post("/check", response_model=CheckOut)
async def check_request(
    body: RequestIn,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["read:rooms"]),
):
    """Dry run: would this request work? Shows every problem without
    submitting anything, so a form can warn as the user fills it in."""
    room, space_id, fallback = await _room_for(db, body.room_id, create=False)
    result = await check_booking(db, room=room, space_id=space_id, window=body.window(),
                                 attendance=body.expected_attendance,
                                 is_admin=has_scope(user, "handle:requests"),
                                 fallback_rules=fallback)
    return CheckOut(**result.to_dict())


@router.post("", response_model=RequestOut, status_code=status.HTTP_201_CREATED)
async def create_request(
    body: RequestIn,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user),
):
    """Submit a booking request for one of your organizations.

    You must be a verified, current member of the organization (admins can
    book for any). If the room is taken, this fails with 409 and the reasons,
    unless `join_waitlist_if_taken` is true, in which case the request joins
    the waitlist. Closed hours, closures and room rules can't be waitlisted.
    """
    require_scope(user, "create:requests", "handle:requests")
    org_id = await run_in_threadpool(require_member, db, user, body.organization_id)
    room, space_id, _ = await _room_for(db, body.room_id, create=True)
    window = body.window()
    is_admin = has_scope(user, "handle:requests")
    result = await check_booking(db, room=room, space_id=space_id, window=window,
                                 attendance=body.expected_attendance, is_admin=is_admin)

    waitlisted = False
    if not result.ok:
        if result.can_waitlist and body.join_waitlist_if_taken:
            require_scope(user, "join:waitlist", "handle:requests")
            waitlisted = True
        else:
            await run_in_threadpool(db.rollback)  # undo any room import
            raise _conflict(result, "This room can't be booked for that time."
                            + (" You can join the waitlist instead." if result.can_waitlist else ""))

    def create():
        req = booking.create_request(db, room=room, organization_id=org_id, requester=user,
                                     event_name=body.event_name.strip(), window=window,
                                     attendance=body.expected_attendance, waitlisted=waitlisted)
        db.commit()
        db.refresh(req)
        return request_out(req, [w.to_dict() for w in result.warnings])

    return await run_in_threadpool(create)


@router.get("", response_model=list[RequestOut])
@router.get("/mine", response_model=list[RequestOut])
def list_my_requests(
    status_: RequestStatus | None = Query(None, alias="status"),
    organization_id: str | None = None,
    upcoming_only: bool = False,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["read:requests"]),
):
    """Requests from your organizations, plus any you submitted. Newest first."""
    orgs = verified_org_ids(db, user)
    q = db.query(Request).filter(or_(Request.organization_id.in_(orgs),
                                     Request.requester_id == user.id))
    if organization_id:
        q = q.filter(Request.organization_id == as_uuid(organization_id, "organization"))
    if status_:
        q = q.filter(Request.status == status_)
    if upcoming_only:
        q = q.filter(Request.end_time >= dt.datetime.now())
    return [request_out(r) for r in q.order_by(Request.created_at.desc()).all()]



@router.get("/{request_id}", response_model=RequestOut)
def get_request(
    request_id: str,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["read:requests"]),
):
    req = booking.get_request(db, request_id)
    ensure_can_view(db, user, req)
    return request_out(req)


@router.patch("/{request_id}", response_model=RequestOut)
async def update_request(
    request_id: str,
    body: RequestUpdate,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user),
):
    """Edit a pending or waitlisted request. The new time is re-checked."""
    require_scope(user, "create:requests", "handle:requests")

    def load():
        req = booking.get_request(db, request_id)
        ensure_can_act(db, user, req)
        return req

    req = await run_in_threadpool(load)
    window = body.window()
    result = await check_booking(db, room=req.room, space_id=req.room.external_ref,
                                 window=window, attendance=body.expected_attendance,
                                 is_admin=has_scope(user, "handle:requests"), exclude_id=req.id)
    move_to_waitlist = False
    if not result.ok:
        if not result.can_waitlist:
            raise _conflict(result, "The new time doesn't work.")
        if req.status == RequestStatus.PENDING:
            if not body.join_waitlist_if_taken:
                raise _conflict(result, "The room is taken at the new time. "
                                        "Set join_waitlist_if_taken to join the waitlist.")
            move_to_waitlist = True

    def save():
        booking.update_request(db, req, event_name=body.event_name, window=window,
                               attendance=body.expected_attendance)
        if move_to_waitlist:
            booking.join_waitlist(db, req)
        db.commit()
        db.refresh(req)
        return request_out(req, [w.to_dict() for w in result.warnings])

    return await run_in_threadpool(save)


@router.post("/{request_id}/cancel", response_model=CancelOut)
async def cancel_request(
    request_id: str,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user),
):
    """Withdraw a request (pending, waitlisted or approved). Cancelling an
    approved booking frees the slot for the waitlist, and cancels it in 25Live
    (or tells an admin how, in manual sync mode)."""
    require_scope(user, "cancel:requests", "handle:requests")

    def cancel():
        req = booking.get_request(db, request_id)
        ensure_can_act(db, user, req)
        previous = booking.cancel_request(db, req, by=user)
        details = booking.sync_details(req)
        db.commit()
        return req, previous, details

    req, previous, details = await run_in_threadpool(cancel)

    sync_out = None
    if previous == RequestStatus.APPROVED and req.external_booking_ref:
        provider = get_provider()
        result = await provider.cancel(details, req.external_booking_ref)
        sync_out = SyncOut(mode=provider.mode, status=result.status.value,
                           message=result.message, instructions=result.instructions)

        def mark():
            req.sync_status = result.status
            db.commit()
        await run_in_threadpool(mark)

    if previous == RequestStatus.APPROVED:
        await promote_waitlisted(db)  # someone may be waiting for this slot

    def reload():
        db.refresh(req)
        return request_out(req)

    return CancelOut(request=await run_in_threadpool(reload), sync=sync_out)


@router.post("/{request_id}/approve", response_model=ApproveOut)
async def approve(
    request_id: str,
    body: ApproveBody | None = None,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["handle:requests"]),
):
    """Approve a pending request (reason optional).

    Re-checks 25Live and our bookings first. Overlapping pending requests move
    to the waitlist. Then the booking is sent to 25Live: automatically in api
    sync mode, or as instructions for an admin in manual mode (see `sync`).
    """
    req = await run_in_threadpool(booking.get_request, db, request_id)
    result = await check_booking(db, room=req.room, space_id=req.room.external_ref,
                                 window=booking.window_of(req),
                                 attendance=req.expected_attendance, is_admin=True,
                                 exclude_id=req.id, strict=True)
    blocking = [p for p in result.blocking if p.code not in ("in_past", "not_open_yet")]
    if blocking:
        raise _conflict(result, "This request can't be approved right now.")

    def decide():
        bumped = booking.approve(db, req, admin=user, reason=body.reason if body else None)
        db.commit()
        return [str(b.id) for b in bumped], booking.sync_details(req)

    bumped, details = await run_in_threadpool(decide)

    provider = get_provider()
    sync = await provider.push(details)

    def record():
        booking.apply_sync_result(db, req, sync)
        db.commit()
        db.refresh(req)
        return request_out(req)

    out = await run_in_threadpool(record)
    return ApproveOut(request=out, moved_to_waitlist=bumped,
                      sync=SyncOut(mode=provider.mode, status=sync.status.value,
                                   message=sync.message, instructions=sync.instructions))


@router.post("/{request_id}/deny", response_model=RequestOut)
def deny(
    request_id: str,
    body: DecisionBody,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["handle:requests"]),
):
    """Deny a pending or waitlisted request. A reason is required (FR-5)."""
    req = booking.get_request(db, request_id)
    booking.deny(db, req, admin=user, reason=body.reason)
    db.commit()
    db.refresh(req)
    return request_out(req)


@router.post("/{request_id}/waitlist", response_model=RequestOut)
async def join_waitlist(
    request_id: str,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user),
):
    """Move a pending request whose slot is taken onto the waitlist."""
    require_scope(user, "join:waitlist", "handle:requests")

    def load():
        req = booking.get_request(db, request_id)
        ensure_can_act(db, user, req)
        return req

    req = await run_in_threadpool(load)
    result = await check_booking(db, room=req.room, space_id=req.room.external_ref,
                                 window=booking.window_of(req),
                                 attendance=req.expected_attendance,
                                 is_admin=has_scope(user, "handle:requests"), exclude_id=req.id)
    if result.ok:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="The room is free at that time, so there's no line to join.")
    if not result.ok and not result.can_waitlist:
        raise _conflict(result, "This slot can't be waitlisted (closed, or against the room's rules).")

    def save():
        booking.join_waitlist(db, req)
        db.commit()
        db.refresh(req)
        return request_out(req)

    return await run_in_threadpool(save)


@router.delete("/{request_id}/waitlist", response_model=RequestOut)
def leave_waitlist(
    request_id: str,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user),
):
    """Leave the waitlist. This withdraws the request."""
    require_scope(user, "join:waitlist", "handle:requests")
    req = booking.get_request(db, request_id)
    ensure_can_act(db, user, req)
    if req.status != RequestStatus.WAITLISTED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="This request isn't on the waitlist.")
    booking.cancel_request(db, req, by=user)
    db.commit()
    db.refresh(req)
    return request_out(req)
