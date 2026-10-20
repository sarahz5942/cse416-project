"""Dev-only: set up a local database for trying the API. Not for production.

Creates the tables in DATABASE_URL and adds test accounts and an
organization, so every endpoint can be tried from /docs right away.

Run from the server/ folder (safe to run again; it skips what exists):
    python dev_setup.py                  # tables + test accounts
    python dev_setup.py --import-rooms   # also import 25Live rooms (all of SBU)
    python dev_setup.py --import-rooms --building "Student Activities"

Test accounts (password for all: devpass123):
    admin@dev.local    admin       (approve/deny, sync, rooms, users)
    leader@dev.local   club_leader (verified member of "Dev Club": can book)
    pending@dev.local  club_leader (asked to join "Dev Club", not verified yet)
    host@dev.local     venue_host
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import sys
from pathlib import Path


def _load_dotenv() -> None:
    """Read KEY=value lines from server/.env into the environment (variables
    already set in the terminal win). Must run before importing
    authentication, which needs JWT_SECRET_KEY at import time."""
    env = Path(__file__).resolve().parent / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv()
if not os.environ.get("JWT_SECRET_KEY"):
    sys.exit("JWT_SECRET_KEY isn't set. Put it in server/.env or export it, "
             "using the same value the server uses.")

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import models  # noqa: F401,E402  (registers every table with Base.metadata)
from authentication import hash_password  # noqa: E402
from models.base import Base  # noqa: E402
from models.organization_and_venue import Organization, OrganizationMember  # noqa: E402
from models.user import User, UserRole  # noqa: E402

PASSWORD = "devpass123"
ACCOUNTS = [
    ("admin@dev.local", "Dev Admin", UserRole.ADMIN),
    ("leader@dev.local", "Dev Leader", UserRole.CLUB_LEADER),
    ("pending@dev.local", "Dev Pending", UserRole.CLUB_LEADER),
    ("host@dev.local", "Dev Host", UserRole.VENUE_HOST),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--import-rooms", action="store_true",
                        help="import rooms from 25Live into the database")
    parser.add_argument("--building", help="with --import-rooms: only this building")
    args = parser.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL first, e.g. "
                 "postgresql+psycopg://localhost:5432/campusreserve")
    engine = create_engine(url)

    with engine.begin() as conn:
        # Needed by Request's ck_no_double_booking exclusion constraint.
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
    Base.metadata.create_all(engine)
    print("Tables ready.")

    with Session(engine) as db:
        users = {}
        for email, name, role in ACCOUNTS:
            user = db.query(User).filter(User.email == email).first()
            if user is None:
                user = User(email=email, name=name, role=role,
                            password_hash=hash_password(PASSWORD))
                db.add(user)
                print(f"  created {email} ({role.value})")
            users[email] = user
        db.flush()

        org = db.query(Organization).filter(Organization.name == "Dev Club").first()
        if org is None:
            org = Organization(name="Dev Club", description="Test organization")
            db.add(org)
            db.flush()
            print("  created organization Dev Club")

        admin = users["admin@dev.local"]
        for email, verified in (("leader@dev.local", True), ("pending@dev.local", False)):
            user = users[email]
            if db.get(OrganizationMember, (user.id, org.id)) is None:
                db.add(OrganizationMember(
                    user_id=user.id, organization_id=org.id, term_start=dt.date.today(),
                    position_title="President" if verified else "Treasurer",
                    verified_at=dt.datetime.utcnow() if verified else None,
                    verified_by_id=admin.id if verified else None))
        db.commit()

        if args.import_rooms:
            from services.request_flow import load_25live_rooms
            from services.room_import import import_rooms
            spaces, info = asyncio.run(load_25live_rooms())
            counts = import_rooms(db, spaces, info, building=args.building)
            db.commit()
            print(f"  25Live import: {counts}")

        print(f"\nDev Club id: {org.id}  (use it as organization_id when booking)")
    print(f"Log in on /docs (Authorize) with any account above, password {PASSWORD}.")


if __name__ == "__main__":
    main()
