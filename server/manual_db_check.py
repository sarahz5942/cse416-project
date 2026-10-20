"""
NOT a pytest test -- run manually, on purpose, when you want to check the
database constraints. It drops and recreates every table, so never let it
run automatically as part of a test suite (that's why it isn't named
test_*.py).

Run with (from server/, venv active):
    python manual_db_check.py

What this does:
  1. Connects to Postgres using DATABASE_URL from .env
  2. Enables the btree_gist extension (needed for the exclusion constraint)
  3. Drops and recreates every table from scratch (dev only -- never do this
     against real data!)
  4. Inserts a venue, room, and organization
  5. Approves one booking -- should succeed
  6. Tries to approve an OVERLAPPING booking for the same room -- should FAIL
  7. Tries to deny a request with no reason -- should FAIL
"""

import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

# This script lives in `server/`, and your models package is `server/models/`,
# so the import is just `models` (not `app.models`).
from models import Base, Organization, Venue, Room, Request, RequestStatus

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]
engine = create_engine(DATABASE_URL, echo=False)

print(f"Connecting to {DATABASE_URL} ...")

with engine.begin() as conn:
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))

print("Dropping and recreating all tables (dev only)...")
Base.metadata.drop_all(engine)
Base.metadata.create_all(engine)
print("Tables created.\n")

with Session(engine) as session:
    org = Organization(name="Tech Club")
    venue = Venue(name="Student Activities Center")
    session.add_all([org, venue])
    session.flush()  # assigns their IDs without fully committing yet

    room = Room(venue_id=venue.id, name="Ballroom A", capacity=200)
    session.add(room)
    session.flush()

    start = datetime(2026, 10, 10, 18, 0, tzinfo=timezone.utc)
    end = datetime(2026, 10, 10, 21, 0, tzinfo=timezone.utc)

    # --- Test 1: a normal approved booking should just work ---
    r1 = Request(
        room_id=room.id,
        organization_id=org.id,
        event_name="Hackathon Kickoff",
        start_time=start,
        end_time=end,
        status=RequestStatus.APPROVED,
    )
    session.add(r1)
    session.commit()
    print("[PASS] First approved booking succeeded.")

    # --- Test 2: an OVERLAPPING approved booking should be REJECTED ---
    r2 = Request(
        room_id=room.id,
        organization_id=org.id,
        event_name="Overlapping Dance Showcase",
        start_time=start + timedelta(hours=1),
        end_time=end + timedelta(hours=1),
        status=RequestStatus.APPROVED,
    )
    session.add(r2)
    try:
        session.commit()
        print("[FAIL] Overlapping booking was allowed! The constraint isn't working.")
    except IntegrityError:
        session.rollback()
        print("[PASS] Overlapping booking was correctly rejected by Postgres.")

    # --- Test 3: denying a request with no reason should be REJECTED ---
    r3 = Request(
        room_id=room.id,
        organization_id=org.id,
        event_name="Denied With No Reason",
        start_time=start + timedelta(days=1),
        end_time=end + timedelta(days=1),
        status=RequestStatus.DENIED,  # no decision_reason set -- should fail
    )
    session.add(r3)
    try:
        session.commit()
        print("[FAIL] Denial with no reason was allowed!")
    except IntegrityError:
        session.rollback()
        print("[PASS] Denial with no reason was correctly rejected.")

print("\nDone. Check the [PASS]/[FAIL] lines above.")
