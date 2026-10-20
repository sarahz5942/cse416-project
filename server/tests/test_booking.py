"""Tests for the booking service — submission, approval, and the overlap check."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import os

from models.base import Base
from models.room import Room, Request, RequestStatus
from models.organization_and_venue import Organization, Venue
from models.user import User, UserRole
from services import booking


TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/campusreserve_test",
)
engine = create_engine(TEST_DATABASE_URL)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def db():
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        conn.commit()
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


# --- shared fixtures: the FK chain a Request needs ---
@pytest.fixture
def venue(db):
    v = Venue(name="Test Venue")
    db.add(v)
    db.commit()
    db.refresh(v)
    return v

@pytest.fixture
def room(db, venue):
    r = Room(name="Test Room", venue_id=venue.id)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r

@pytest.fixture
def org(db):
    o = Organization(name="Test Org")
    db.add(o)
    db.commit()
    db.refresh(o)
    return o

@pytest.fixture
def user(db):
    u = User(email="req@stonybrook.edu", name="Requester",
             password_hash="x", role=UserRole.CLUB_LEADER)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


BASE = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)

def window(start_h, end_h):
    return BASE + timedelta(hours=start_h), BASE + timedelta(hours=end_h)


def _submit(db, room, org, user, start_h, end_h):
    start, end = window(start_h, end_h)
    return booking.submit_request(
        db, room_id=room.id, organization_id=org.id, requester_id=user.id,
        event_name="Test Event", start_time=start, end_time=end,
    )


# --- Submission ---
def test_submit_creates_pending_request(db, room, org, user):
    req = _submit(db, room, org, user, 0, 2)
    assert req.status == RequestStatus.PENDING
    assert req.room_id == room.id


def test_submit_rejects_end_before_start(db, room, org, user):
    start, end = window(2, 0)
    with pytest.raises(HTTPException) as exc:
        booking.submit_request(
            db, room_id=room.id, organization_id=org.id, requester_id=user.id,
            event_name="Test Event", start_time=start, end_time=end,
        )
    assert exc.value.status_code == 400


def test_submit_rejects_unknown_room(db, org, user):
    start, end = window(0, 2)
    with pytest.raises(HTTPException) as exc:
        booking.submit_request(
            db, room_id=uuid.uuid4(), organization_id=org.id, requester_id=user.id,
            event_name="Test Event", start_time=start, end_time=end,
        )
    assert exc.value.status_code == 404


# --- Approval + the overlap check ---
def test_approve_sets_approved_with_reason(db, room, org, user):
    req = _submit(db, room, org, user, 0, 2)
    approved = booking.approve_request(db, request_id=req.id, admin_id=user.id, reason="ok")
    assert approved.status == RequestStatus.APPROVED
    assert approved.decision_reason == "ok"


def test_two_overlapping_requests_cannot_both_be_approved(db, room, org, user):
    first = _submit(db, room, org, user, 0, 2)    # 9–11
    second = _submit(db, room, org, user, 1, 3)   # 10–12 (overlaps)
    booking.approve_request(db, request_id=first.id, admin_id=user.id, reason="ok")
    with pytest.raises(Exception):   # HTTPException (service) or IntegrityError (DB constraint)
        booking.approve_request(db, request_id=second.id, admin_id=user.id, reason="ok")


def test_adjacent_non_overlapping_requests_both_approve(db, room, org, user):
    first = _submit(db, room, org, user, 0, 2)    # 9–11
    second = _submit(db, room, org, user, 2, 4)   # 11–13 (touches, no overlap)
    booking.approve_request(db, request_id=first.id, admin_id=user.id, reason="ok")
    approved2 = booking.approve_request(db, request_id=second.id, admin_id=user.id, reason="ok")
    assert approved2.status == RequestStatus.APPROVED


def test_same_time_different_rooms_both_approve(db, room, venue, org, user):
    other = Room(name="Room 2", venue_id=venue.id)
    db.add(other)
    db.commit()
    db.refresh(other)
    r1 = _submit(db, room, org, user, 0, 2)
    start, end = window(0, 2)
    r2 = booking.submit_request(
        db, room_id=other.id, organization_id=org.id, requester_id=user.id,
        event_name="Test Event", start_time=start, end_time=end,
    )
    booking.approve_request(db, request_id=r1.id, admin_id=user.id, reason="ok")
    approved2 = booking.approve_request(db, request_id=r2.id, admin_id=user.id, reason="ok")
    assert approved2.status == RequestStatus.APPROVED


def test_deny_sets_denied_with_reason(db, room, org, user):
    req = _submit(db, room, org, user, 0, 2)
    denied = booking.deny_request(db, request_id=req.id, admin_id=user.id, reason="conflict")
    assert denied.status == RequestStatus.DENIED
    assert denied.decision_reason == "conflict"
