"""Admin endpoints: review queue, 25Live sync, rooms, closures, users, organizations.

Approve/deny themselves live at /requests/{id}/approve and /deny.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, Security, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from authentication import get_current_user
from database import get_db
from models.communication import NotificationType
from models.organization_and_venue import Organization, OrganizationMember, Venue
from models.room import Request, RequestStatus, Room, RoomClosure, SyncStatus
from models.user import User, UserRole
from routers.requests import CheckOut, RequestOut, SyncOut, request_out
from services import booking, room_features
from services.live25_sync import get_provider, manual_steps
from services.notify import notify
from services.permissions import admins, as_uuid, can_manage_venue
from services.request_flow import check_booking, load_25live_rooms
from services.room_import import ensure_room, find_room, import_rooms
from services.room_merge import campus_now, to_local_naive

router = APIRouter(prefix="/admin", tags=["admin"])


# =============================================================================
# Review queue
# =============================================================================

class QueueItem(BaseModel):
    request: RequestOut
    check: CheckOut | None = None   # for pending requests: what's in the way now


@router.get("/requests", response_model=list[QueueItem])
async def review_queue(
    status_: RequestStatus = Query(RequestStatus.PENDING, alias="status"),
    room_id: str | None = None,
    organization_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["handle:requests"]),
):
    """Requests to review, oldest first. Pending ones include a fresh check,
    so you can see conflicts (25Live bookings, other approvals, closures,
    competing requests) before approving."""

    def load():
        q = db.query(Request).filter(Request.status == status_)
        if room_id:
            room = find_room(db, room_id)
            if room is None:
                return []
            q = q.filter(Request.room_id == room.id)
        if organization_id:
            q = q.filter(Request.organization_id == as_uuid(organization_id, "organization"))
        return q.order_by(Request.created_at).limit(limit).all()

    items = []
    for req in await run_in_threadpool(load):
        check = None
        if req.status == RequestStatus.PENDING:
            result = await check_booking(db, room=req.room, space_id=req.room.external_ref,
                                         window=booking.window_of(req),
                                         attendance=req.expected_attendance, is_admin=True,
                                         exclude_id=req.id)
            check = CheckOut(**result.to_dict())
        items.append(QueueItem(request=await run_in_threadpool(request_out, req), check=check))
    return items


# =============================================================================
# 25Live sync
# =============================================================================

class SyncItem(BaseModel):
    request: RequestOut
    details: dict
    steps: list[str]


class ConfirmSyncBody(BaseModel):
    external_booking_ref: str = Field(min_length=1, description="The 25Live event reference")


@router.get("/sync", response_model=list[SyncItem])
def sync_queue(
    include_past: bool = False,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["sync:external-bookings"]),
):
    """Approved bookings not yet in 25Live (not_synced or failed), soonest
    first, with what to enter in 25Live."""
    q = db.query(Request).filter(Request.status == RequestStatus.APPROVED,
                                 Request.sync_status != SyncStatus.SYNCED)
    if not include_past:
        q = q.filter(Request.end_time >= campus_now())
    out = []
    for req in q.order_by(Request.start_time).all():
        details = booking.sync_details(req)
        out.append(SyncItem(request=request_out(req), details=details,
                            steps=manual_steps(details)))
    return out


@router.post("/requests/{request_id}/sync", response_model=SyncOut)
async def push_to_25live(
    request_id: str,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["sync:external-bookings"]),
):
    """(Re)send an approved booking to 25Live. In manual mode this returns
    the instructions again; in api mode it retries the automatic push."""

    def load():
        req = booking.get_request(db, request_id)
        if req.status != RequestStatus.APPROVED:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="Only approved requests are synced to 25Live.")
        if req.sync_status == SyncStatus.SYNCED:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail=f"Already in 25Live ({req.external_booking_ref}).")
        return req, booking.sync_details(req)

    req, details = await run_in_threadpool(load)
    provider = get_provider()
    result = await provider.push(details)

    def save():
        booking.apply_sync_result(db, req, result)
        db.commit()

    await run_in_threadpool(save)
    return SyncOut(mode=provider.mode, status=result.status.value, message=result.message,
                   instructions=result.instructions)


@router.post("/requests/{request_id}/sync/confirm", response_model=RequestOut)
def confirm_sync(
    request_id: str,
    body: ConfirmSyncBody,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["sync:external-bookings"]),
):
    """Manual mode: record that you entered the booking in 25Live, with its
    25Live reference. Marks it synced."""
    req = booking.get_request(db, request_id)
    booking.confirm_manual_sync(db, req, body.external_booking_ref)
    db.commit()
    db.refresh(req)
    return request_out(req)


# =============================================================================
# Rooms
# =============================================================================

class RoomUpdate(BaseModel):
    """Only the fields you send are changed. Send null to clear a field."""
    name: str | None = Field(None, min_length=1, max_length=255)
    capacity: int | None = Field(None, ge=0)
    room_type: str | None = Field(None, max_length=100)
    features: list[str] | None = Field(None, description="Feature tags, see GET /rooms/features")
    media_url: str | None = Field(None, max_length=512)
    club_bookable: bool | None = None
    is_active: bool | None = None
    booking_opens_days_ahead: int | None = Field(None, ge=0, le=730)


class RoomAdminOut(BaseModel):
    id: str
    db_id: str
    venue_id: str
    venue_name: str | None
    name: str
    capacity: int | None
    room_type: str | None
    features: list[str]
    media_url: str | None
    club_bookable: bool
    is_active: bool
    booking_opens_days_ahead: int | None


def room_out(room: Room) -> RoomAdminOut:
    return RoomAdminOut(
        id=room.external_ref or str(room.id), db_id=str(room.id), venue_id=str(room.venue_id),
        venue_name=room.venue.name if room.venue else None, name=room.name,
        capacity=room.capacity, room_type=room.room_type, features=list(room.features or []),
        media_url=room.media_url, club_bookable=room.club_bookable, is_active=room.is_active,
        booking_opens_days_ahead=room.booking_opens_days_ahead)


class ImportBody(BaseModel):
    building: str | None = Field(None, description="Only rooms in buildings matching this")
    space_ids: list[str] | None = Field(None, description="Only these 25Live rooms")


@router.post("/rooms/import")
async def import_from_25live(
    body: ImportBody | None = None,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["manage:rooms"]),
):
    """Create Room/Venue rows for 25Live rooms we don't have yet. Safe to run
    again: existing rooms (and admins' edits) are left alone. Admins only."""
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only admins can import rooms.")
    spaces, info = await load_25live_rooms()

    def run():
        counts = import_rooms(db, spaces, info,
                              only_ids=set(body.space_ids) if body and body.space_ids else None,
                              building=body.building if body else None)
        db.commit()
        return counts

    return await run_in_threadpool(run)


@router.patch("/rooms/{room_id}", response_model=RoomAdminOut)
async def update_room(
    room_id: str,
    body: RoomUpdate,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["manage:rooms"]),
):
    """Set a room's tags (food, crafts, ...), bookability, booking window,
    and details. Venue hosts can edit only rooms in their venues."""
    spaces, info = await load_25live_rooms() if room_id.isdigit() else ([], {})
    changes = body.model_dump(exclude_unset=True)
    if "features" in changes:
        tags = sorted({room_features.normalize(t) for t in changes["features"] or []})
        unknown = [t for t in tags if t not in room_features.FEATURES]
        if unknown:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail={"message": f"Unknown feature tags: {', '.join(unknown)}",
                                        "valid_tags": sorted(room_features.FEATURES)})
        changes["features"] = tags
    for required in ("name", "club_bookable", "is_active"):
        if required in changes and changes[required] is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail=f"{required} can't be empty.")

    def save():
        room = ensure_room(db, room_id, spaces, info)
        if room is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found.")
        if not can_manage_venue(db, user, room.venue_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="You can only edit rooms in venues you host.")
        for field, value in changes.items():
            setattr(room, field, value)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="Another room in this venue already has that name.")
        db.refresh(room)
        return room_out(room)

    return await run_in_threadpool(save)


# =============================================================================
# Closures
# =============================================================================

class ClosureIn(BaseModel):
    room_id: str | None = Field(None, description="Close one room (25Live id or room UUID)")
    venue_id: str | None = Field(None, description="Or close a whole building (venue UUID)")
    closed_from: dt.datetime
    closed_until: dt.datetime
    reason: str = Field(min_length=1, description="Shown to users, e.g. 'HVAC maintenance'")


class ClosureOut(BaseModel):
    id: str
    room_id: str | None
    room_name: str | None
    venue_id: str | None
    venue_name: str | None
    closed_from: dt.datetime
    closed_until: dt.datetime
    reason: str
    affected_requests: list[RequestOut] = []   # bookings/requests inside the closure


def closure_out(c: RoomClosure, affected: list[Request] = ()) -> ClosureOut:
    return ClosureOut(
        id=str(c.id),
        room_id=(c.room.external_ref or str(c.room.id)) if c.room else None,
        room_name=c.room.name if c.room else None,
        venue_id=str(c.venue_id) if c.venue_id else None,
        venue_name=c.venue.name if c.venue else None,
        closed_from=c.closed_from, closed_until=c.closed_until, reason=c.reason,
        affected_requests=[request_out(r) for r in affected])


def _affected(db: Session, c: RoomClosure) -> list[Request]:
    q = db.query(Request).join(Room, Room.id == Request.room_id).filter(
        Request.status.in_(booking.OPEN_STATUSES),
        booking.BLOCK_START < c.closed_until, booking.BLOCK_END > c.closed_from)
    q = q.filter(Request.room_id == c.room_id) if c.room_id else q.filter(Room.venue_id == c.venue_id)
    return q.order_by(Request.start_time).all()


@router.get("/closures", response_model=list[ClosureOut])
def list_closures(
    room_id: str | None = None,
    venue_id: str | None = None,
    include_past: bool = False,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["manage:rooms"]),
):
    q = db.query(RoomClosure)
    if room_id:
        room = find_room(db, room_id)
        if room is None:
            return []
        q = q.filter(or_(RoomClosure.room_id == room.id, RoomClosure.venue_id == room.venue_id))
    if venue_id:
        q = q.filter(RoomClosure.venue_id == as_uuid(venue_id, "venue"))
    if not include_past:
        q = q.filter(RoomClosure.closed_until >= campus_now())
    return [closure_out(c) for c in q.order_by(RoomClosure.closed_from).all()]


@router.post("/closures", response_model=ClosureOut, status_code=status.HTTP_201_CREATED)
async def create_closure(
    body: ClosureIn,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["manage:rooms"]),
):
    """Close a room or a whole building for a period (maintenance, holidays).
    New requests for that time are refused. Existing bookings inside it are
    NOT cancelled automatically; they're listed in `affected_requests` so you
    can contact or cancel them."""
    if (body.room_id is None) == (body.venue_id is None):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Give exactly one of room_id or venue_id.")
    start, end = to_local_naive(body.closed_from), to_local_naive(body.closed_until)
    if end <= start:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="closed_until must be after closed_from.")
    spaces, info = (await load_25live_rooms()) if body.room_id and body.room_id.isdigit() \
        else ([], {})

    def save():
        room = venue = None
        if body.room_id:
            room = ensure_room(db, body.room_id, spaces, info)
            if room is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found.")
            venue_id = room.venue_id
        else:
            venue = db.get(Venue, as_uuid(body.venue_id, "venue"))
            if venue is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Venue not found.")
            venue_id = venue.id
        if not can_manage_venue(db, user, venue_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="You can only close rooms in venues you host.")
        c = RoomClosure(room_id=room.id if room else None, venue_id=venue.id if venue else None,
                        closed_from=start, closed_until=end, reason=body.reason.strip(),
                        created_by_id=user.id)
        db.add(c)
        db.commit()
        db.refresh(c)
        return closure_out(c, _affected(db, c))

    return await run_in_threadpool(save)


@router.delete("/closures/{closure_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_closure(
    closure_id: str,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["manage:rooms"]),
):
    c = db.get(RoomClosure, as_uuid(closure_id, "closure"))
    if c is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Closure not found.")
    venue_id = c.venue_id or (c.room.venue_id if c.room else None)
    if not can_manage_venue(db, user, venue_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="You can only change closures in venues you host.")
    db.delete(c)
    db.commit()


# =============================================================================
# Users and organizations
# =============================================================================

class MembershipOut(BaseModel):
    organization_id: str
    organization_name: str | None
    user_id: str
    user_name: str | None
    user_email: str | None
    position_title: str | None
    term_start: dt.date
    term_end: dt.date | None
    verified: bool
    verified_at: dt.datetime | None


def membership_out(m: OrganizationMember) -> MembershipOut:
    return MembershipOut(
        organization_id=str(m.organization_id),
        organization_name=m.organization.name if m.organization else None,
        user_id=str(m.user_id), user_name=m.user.name if m.user else None,
        user_email=m.user.email if m.user else None, position_title=m.position_title,
        term_start=m.term_start, term_end=m.term_end, verified=m.verified_at is not None,
        verified_at=m.verified_at)


class UserAdminOut(BaseModel):
    id: str
    email: str
    name: str
    role: str
    is_active: bool
    memberships: list[MembershipOut]


def user_out(u: User) -> UserAdminOut:
    return UserAdminOut(id=str(u.id), email=u.email, name=u.name, role=u.role.value,
                        is_active=u.is_active,
                        memberships=[membership_out(m) for m in u.organization_memberships])


class UserUpdate(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None


class VerifyBody(BaseModel):
    position_title: str | None = Field(None, max_length=100)
    term_end: dt.date | None = Field(None, description="When their access should end (NFR-2)")


class OrganizationIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


@router.get("/users", response_model=list[UserAdminOut])
def list_users(
    role: UserRole | None = None,
    awaiting_verification: bool = Query(False, description="Only users with an unverified membership"),
    search: str | None = Query(None, description="Name or email contains"),
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["manage:users"]),
):
    q = db.query(User)
    if role:
        q = q.filter(User.role == role)
    if search:
        like = f"%{search.lower()}%"
        q = q.filter(or_(func.lower(User.name).like(like), func.lower(User.email).like(like)))
    if awaiting_verification:
        q = q.join(OrganizationMember, OrganizationMember.user_id == User.id).filter(
            OrganizationMember.verified_at.is_(None)).distinct()
    return [user_out(u) for u in q.order_by(User.name).limit(500).all()]


@router.patch("/users/{user_id}", response_model=UserAdminOut)
def update_user(
    user_id: str,
    body: UserUpdate,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["manage:users"]),
):
    """Change a user's role or deactivate them. A role change takes effect
    the next time they log in (their current token keeps its old scopes)."""
    target = db.get(User, as_uuid(user_id, "user"))
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    if target.id == user.id and (body.is_active is False or
                                 (body.role and body.role != UserRole.ADMIN)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="You can't remove your own admin access.")
    if body.role is not None:
        target.role = body.role
    if body.is_active is not None:
        target.is_active = body.is_active
    db.commit()
    db.refresh(target)
    return user_out(target)


@router.post("/organizations", status_code=status.HTTP_201_CREATED)
def create_organization(
    body: OrganizationIn,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["manage:users"]),
):
    org = Organization(name=body.name.strip(), description=body.description)
    db.add(org)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="An organization with that name already exists.")
    return {"id": str(org.id), "name": org.name, "description": org.description}


@router.post("/organizations/{organization_id}/members/{user_id}/verify",
             response_model=MembershipOut)
def verify_member(
    organization_id: str,
    user_id: str,
    body: VerifyBody | None = None,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["manage:users"]),
):
    """Verify someone as a current E-board member (NFR-1), so they can book
    for the organization. Creates the membership if they hadn't asked to join."""
    org = db.get(Organization, as_uuid(organization_id, "organization"))
    target = db.get(User, as_uuid(user_id, "user"))
    if org is None or target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Organization or user not found.")
    m = db.get(OrganizationMember, (target.id, org.id))
    if m is None:
        m = OrganizationMember(user_id=target.id, organization_id=org.id,
                               term_start=dt.date.today())
        db.add(m)
    if body and body.position_title is not None:
        m.position_title = body.position_title
    if body and body.term_end is not None:
        if body.term_end < (m.term_start or dt.date.today()):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail="term_end can't be before the term starts.")
        m.term_end = body.term_end
    m.verified_at = dt.datetime.utcnow()
    m.verified_by_id = user.id
    notify(db, [target], NotificationType.MEMBERSHIP_VERIFIED,
           f"You're verified as a member of {org.name} and can now book rooms for it.",
           email_subject=f"Verified for {org.name}")
    db.commit()
    db.refresh(m)
    return membership_out(m)


@router.delete("/organizations/{organization_id}/members/{user_id}",
               status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    organization_id: str,
    user_id: str,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["manage:users"]),
):
    """End someone's membership. Verified members' terms end today (their
    history stays); unverified join requests are simply removed."""
    m = db.get(OrganizationMember, (as_uuid(user_id, "user"),
                                    as_uuid(organization_id, "organization")))
    if m is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found.")
    if m.verified_at is None:
        db.delete(m)
    else:
        m.term_end = max(dt.date.today(), m.term_start)
    db.commit()


def notify_admins_of_join(db: Session, member: User, org: Organization) -> None:
    notify(db, admins(db), NotificationType.MEMBERSHIP_REQUESTED,
           f"{member.name} ({member.email}) asked to be verified for {org.name}. "
           f"Verify at POST /admin/organizations/{org.id}/members/{member.id}/verify.",
           email_subject="Membership waiting for verification")
