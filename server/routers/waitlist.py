"""Waitlist views (FR-6). Joining/leaving is on /requests/{id}/waitlist."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Security, status
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from authentication import get_current_user
from database import get_db
from models.room import Request, RequestStatus
from models.user import User
from routers.requests import RequestOut, request_out
from services.booking import BLOCK_END, window_of
from services.permissions import verified_org_ids
from services.room_import import find_room
from services.room_merge import campus_now

router = APIRouter(prefix="/waitlist", tags=["waitlist"])


class MyWaitlistEntry(BaseModel):
    request: RequestOut
    position: int
    ahead_of_you: int


class LineEntry(BaseModel):
    position: int
    start: dt.datetime        # blocked window (with setup/cleanup)
    end: dt.datetime
    is_yours: bool            # one of your organizations' requests
    request_id: str | None    # only shown for your own


@router.get("/mine", response_model=list[MyWaitlistEntry])
def my_waitlist(
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["read:waitlist"]),
):
    """Your organizations' waitlisted requests and your place in each line."""
    orgs = verified_org_ids(db, user)
    reqs = (db.query(Request)
            .filter(Request.status == RequestStatus.WAITLISTED,
                    or_(Request.organization_id.in_(orgs), Request.requester_id == user.id),
                    BLOCK_END >= campus_now())
            .order_by(Request.start_time).all())
    return [MyWaitlistEntry(request=request_out(r), position=r.waitlist_position or 0,
                            ahead_of_you=max((r.waitlist_position or 1) - 1, 0))
            for r in reqs]


@router.get("/rooms/{room_id}", response_model=list[LineEntry])
def room_waitlist(
    room_id: str,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["read:waitlist"]),
):
    """Everyone waiting for a room, upcoming only. Other organizations'
    names and events stay private; you see positions and times."""
    room = find_room(db, room_id)
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found.")
    orgs = verified_org_ids(db, user)
    reqs = (db.query(Request)
            .filter(Request.room_id == room.id, Request.status == RequestStatus.WAITLISTED,
                    BLOCK_END >= campus_now())
            .order_by(Request.start_time, Request.waitlist_position).all())
    out = []
    for r in reqs:
        w = window_of(r)
        mine = r.organization_id in orgs or r.requester_id == user.id
        out.append(LineEntry(position=r.waitlist_position or 0, start=w.blocked_start,
                             end=w.blocked_end, is_yours=mine,
                             request_id=str(r.id) if mine else None))
    return out
