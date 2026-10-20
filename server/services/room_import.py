"""Create Room and Venue rows from 25Live's room list.

25Live is the system of record for which rooms exist. Our database needs a
Room row (and its Venue) before anyone can file a request for it, so:
  - POST /admin/rooms/import creates them in bulk, and
  - submitting a request for a room we haven't imported yet imports just
    that room on the spot (ensure_room).

Importing never overwrites what admins have set (tags, club_bookable, name
edits, etc.); it only fills in rooms and venues that don't exist yet.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from models.organization_and_venue import Venue
from models.room import Room

NO_BUILDING_REF = "none"


def _venue_for(db: Session, space: dict, venues: dict[str, Venue], names: set[str]) -> Venue:
    ref = space.get("building_id") or NO_BUILDING_REF
    venue = venues.get(ref)
    if venue is None:
        name = (space.get("building") or "Other locations")[:240]
        if name in names:  # Venue.name is unique; keep same-named buildings apart
            name = f"{name} ({ref})"
        venue = Venue(id=uuid.uuid4(), name=name, external_ref=ref)
        db.add(venue)
        venues[ref] = venue
        names.add(name)
    return venue


def import_rooms(db: Session, spaces: list[dict], info: dict[str, dict], *,
                 only_ids: set[str] | None = None, building: str | None = None) -> dict:
    """Create missing rooms/venues. Doesn't commit; the caller does.

    spaces:   live25.list_spaces()          info: live25.list_location_info()
    only_ids: import just these space_ids   building: only rooms in matching buildings
    """
    venues = {v.external_ref: v for v in db.query(Venue).filter(Venue.external_ref.isnot(None))}
    venue_names = {n for (n,) in db.query(Venue.name)}
    existing = {ref for (ref,) in
                db.query(Room.external_ref).filter(Room.external_ref.isnot(None))}
    # Room names are unique per venue.
    taken = {(vid, n) for vid, n in db.query(Room.venue_id, Room.name)}

    venues_before = len(venues)
    created_rooms = 0
    skipped = 0
    for space in spaces:
        sid = space["id"]
        if only_ids is not None and sid not in only_ids:
            continue
        if building and building.lower() not in (space.get("building") or "").lower():
            continue
        if sid in existing:
            skipped += 1
            continue
        venue = _venue_for(db, space, venues, venue_names)
        extra = info.get(sid) or {}
        name = (space.get("name") or space.get("short_name") or f"Room {sid}")[:240]
        if (venue.id, name) in taken:
            name = f"{name} ({sid})"
        taken.add((venue.id, name))
        db.add(Room(
            id=uuid.uuid4(),
            venue_id=venue.id,
            name=name,
            capacity=space.get("capacity") or extra.get("max_capacity"),
            room_type=extra.get("room_type") or space.get("room_type"),
            features=[],  # our curated tags; 25Live's own features are read live
            external_ref=sid,
        ))
        existing.add(sid)
        created_rooms += 1

    return {"rooms_created": created_rooms,
            "venues_created": len(venues) - venues_before,
            "rooms_already_imported": skipped}


def find_room(db: Session, public_id: str) -> Room | None:
    """A room by its public id: a 25Live space_id or our UUID."""
    room = db.query(Room).filter(Room.external_ref == str(public_id)).first()
    if room is None:
        try:
            room = db.get(Room, uuid.UUID(str(public_id)))
        except ValueError:
            room = None
    return room


def ensure_room(db: Session, public_id: str, spaces: list[dict], info: dict[str, dict]) -> Room | None:
    """Our Room for a public id, importing it from 25Live if needed (flushes)."""
    room = find_room(db, public_id)
    if room is None and str(public_id).isdigit():
        import_rooms(db, spaces, info, only_ids={str(public_id)})
        db.flush()
        room = find_room(db, public_id)
    return room
