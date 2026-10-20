"""The current user's memberships, and a calendar feed of their bookings."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request as HttpRequest, Security, status
from fastapi.responses import Response
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from authentication import ALGORITHM, SECRET_KEY, get_current_user
from database import get_db
from models.organization_and_venue import OrganizationMember
from models.room import Request, RequestStatus
from models.user import User
from routers.admin import MembershipOut, membership_out
from services.ics import build_calendar
from services.permissions import as_uuid, verified_org_ids

router = APIRouter(tags=["me"])

CALENDAR_PURPOSE = "calendar"


class CalendarLink(BaseModel):
    url: str
    note: str


@router.get("/me/memberships", response_model=list[MembershipOut])
def my_memberships(db: Session = Depends(get_db), user: User = Security(get_current_user)):
    """Your organizations, and whether you're verified to book for each."""
    rows = db.query(OrganizationMember).filter(OrganizationMember.user_id == user.id).all()
    return [membership_out(m) for m in rows]


@router.get("/me/calendar-link", response_model=CalendarLink)
def calendar_link(request: HttpRequest, user: User = Security(get_current_user)):
    """A private link to subscribe to in Google/Apple Calendar.

    Calendar apps can't log in, so the link itself carries a key. Anyone with
    the link can see your organizations' bookings; don't share it. (Changing
    JWT_SECRET_KEY invalidates every calendar link.)"""
    token = jwt.encode({"sub": str(user.id), "purpose": CALENDAR_PURPOSE},
                       SECRET_KEY, algorithm=ALGORITHM)
    url = str(request.url_for("calendar_feed", token=token))
    return CalendarLink(url=url, note="Subscribe to this URL in your calendar app "
                                      "(Google Calendar: Other calendars > From URL).")


@router.get("/calendar/{token}.ics", name="calendar_feed")
def calendar_feed(token: str, db: Session = Depends(get_db)):
    """iCalendar feed of approved bookings for the link owner's organizations,
    from 30 days ago onward. No login; the token in the link is the key."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Calendar not found.")
    if payload.get("purpose") != CALENDAR_PURPOSE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Calendar not found.")
    try:
        user = db.get(User, as_uuid(payload.get("sub"), "calendar"))
    except HTTPException:
        user = None
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Calendar not found.")

    orgs = verified_org_ids(db, user)
    since = dt.datetime.now() - dt.timedelta(days=30)
    reqs = (db.query(Request)
            .filter(Request.status == RequestStatus.APPROVED, Request.end_time >= since,
                    or_(Request.organization_id.in_(orgs), Request.requester_id == user.id))
            .order_by(Request.start_time).all())
    events = [{
        "uid": str(r.id),
        "title": r.event_name,
        "start": r.start_time,
        "end": r.end_time,
        "location": r.room.name if r.room else "",
        "description": (f"{r.organization.name if r.organization else ''}"
                        + (f" | Room reserved {r.setup_start:%-I:%M %p} to "
                           f"{(r.cleanup_end or r.end_time):%-I:%M %p}"
                           if r.setup_start or r.cleanup_end else "")),
    } for r in reqs]
    return Response(content=build_calendar("CampusReserve bookings", events),
                    media_type="text/calendar; charset=utf-8")
