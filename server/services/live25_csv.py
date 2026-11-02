"""CSV export for 25Live's Data Import Tool.

While we only have read access to 25Live, an admin downloads the approved
bookings as a CSV and uploads it in 25Live (More menu -> Data Import Tool ->
Import Events CSV). The file follows CollegeNET's "CSV Event Import Formatting
Guidelines" (the events.csv service):

  * every column of the template, in the template's order, header row included
  * dates YYYY-MM-DD, times HH:MM:SS in 24-hour time, UTF-8 without a BOM
  * one row per event; a booking is a single occurrence, so it uses ADHOC_DATES
  * EVENT_ID becomes the 25Live "alien UID" with a "CSV-" prefix. We use the
    request's id, so uploading the same booking twice overwrites the 25Live
    event instead of creating a duplicate (unless someone edited it in 25Live)

Which bookings are already exported is tracked on the request itself
(Request.exported_at / export_batch_id), so each download only holds new ones.

Settings (environment variables), because these must match 25Live exactly:
    LIVE25_EVENT_TYPE            default "Meeting"
    LIVE25_PRIMARY_ORGANIZATION  default: the requesting club's name
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import os
import uuid
from dataclasses import dataclass, field

from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from models.room import Request, RequestStatus, SyncStatus
from services.room_merge import campus_now

# Template columns, in order. Don't rename or reorder: 25Live rejects the file.
HEADERS = [
    "EVENT_ID", "EVENT_NAME", "EVENT_TITLE", "DESCRIPTION", "INTERNAL_NOTES", "EVENT_TYPE",
    "PRIMARY_ORGANIZATION", "REG_HEADCOUNT", "EXP_HEADCOUNT", "CATEGORIES", "INSTRUCTOR",
    "SCHEDULER", "REQUESTOR", "PROFILE_ID", "PROFILE_NAME", "FIRST_OCCURRENCE_DATE",
    "PRE_EVENT_TIME", "START_TIME", "END_TIME", "POST_EVENT_TIME", "REPEATING_RULE",
    "ADHOC_DATES", "LOCATIONS", "BOUND_PROFILE_ID", "RELATED_EVENT_ID", "CUSTOM_ATTRIBUTES",
]

# 25Live says a single occurrence is an "ad hoc" date. If the first test upload
# complains about dates, set this to False to use FIRST_OCCURRENCE_DATE instead.
USE_ADHOC_DATE = True

EVENT_NAME_LIMIT = 40    # 25Live's limit for EVENT_NAME
EVENT_TITLE_LIMIT = 120  # ...and EVENT_TITLE
CANCEL_WINDOW_DAYS = 14  # how long a cancelled-after-export booking stays on the to-do list


def event_type() -> str:
    return os.getenv("LIVE25_EVENT_TYPE", "Meeting")


def primary_organization_override() -> str | None:
    return os.getenv("LIVE25_PRIMARY_ORGANIZATION") or None


def alien_uid(req: Request) -> str:
    """The id 25Live shows for an imported event."""
    return f"CSV-{req.id}"


@dataclass
class ExportRow:
    request: Request
    values: list[str]
    problems: list[str] = field(default_factory=list)  # can't be exported until fixed
    warnings: list[str] = field(default_factory=list)  # exported, but check these in 25Live


def _clean(text, limit: int | None = None) -> str:
    """One line, no '|' (25Live's separator for multiple values)."""
    out = " ".join(str(text or "").replace("|", "/").split())
    return out[:limit].rstrip() if limit else out


def _minutes(before: dt.datetime | None, after: dt.datetime) -> str:
    if before is None or before >= after:
        return ""
    return str(int((after - before).total_seconds() // 60))


def make_row(req: Request) -> ExportRow:
    problems: list[str] = []
    warnings: list[str] = []
    start, end = req.start_time, req.end_time

    if end - start >= dt.timedelta(hours=24):
        problems.append("Longer than 24 hours: 25Live's CSV import can't take it. "
                        "Enter this one in 25Live by hand.")

    room = req.room
    if room is not None and room.external_ref:
        location = str(room.external_ref)
    else:
        location = room.name if room is not None else ""
        if not location:
            problems.append("No room.")
        else:
            warnings.append(f"{location} has no 25Live ID, so its name is used: it must match "
                            "the 25Live location name exactly.")

    org_name = req.organization.name if req.organization is not None else ""
    attendance = str(req.expected_attendance) if (req.expected_attendance or 0) > 0 else ""

    who = ""
    if req.requester is not None:
        who = f" Requested by: {req.requester.name} <{req.requester.email}>."
    notes = _clean(f"CampusReserve request {req.id}. Organization: {org_name}.{who}")

    date = start.date().isoformat()
    values = {
        "EVENT_ID": str(req.id),
        "EVENT_NAME": _clean(req.event_name, EVENT_NAME_LIMIT),
        "EVENT_TITLE": _clean(req.event_name, EVENT_TITLE_LIMIT),
        "INTERNAL_NOTES": notes,
        "EVENT_TYPE": event_type(),
        "PRIMARY_ORGANIZATION": _clean(primary_organization_override() or org_name),
        "EXP_HEADCOUNT": attendance,
        "PROFILE_ID": f"P-{req.id}",
        "FIRST_OCCURRENCE_DATE": "" if USE_ADHOC_DATE else date,
        "PRE_EVENT_TIME": _minutes(req.setup_start, start),
        "START_TIME": start.strftime("%H:%M:%S"),
        # If this is earlier than START_TIME, 25Live reads it as the next day.
        "END_TIME": end.strftime("%H:%M:%S"),
        "POST_EVENT_TIME": _minutes(end, req.cleanup_end) if req.cleanup_end else "",
        "ADHOC_DATES": date if USE_ADHOC_DATE else "",
        "LOCATIONS": _clean(location),
    }
    return ExportRow(req, [values.get(h, "") for h in HEADERS], problems, warnings)


def build_csv(rows: list[ExportRow]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(HEADERS)
    for row in rows:
        writer.writerow(row.values)
    return out.getvalue()  # sent as plain UTF-8: no byte-order mark


# ------------------------------------------------------------------ database

def _base_query(db: Session):
    return db.query(Request).options(selectinload(Request.room),
                                     selectinload(Request.organization),
                                     selectinload(Request.requester))


def eligible(db: Session, *, include_past: bool = False, lock: bool = False) -> list[Request]:
    """Approved bookings not yet in a downloaded file and not already in 25Live.
    `lock` skips rows another admin is exporting right now (no double export)."""
    q = _base_query(db).filter(Request.status == RequestStatus.APPROVED,
                               Request.exported_at.is_(None),
                               Request.sync_status != SyncStatus.SYNCED)
    if not include_past:
        q = q.filter(Request.end_time >= campus_now())
    q = q.order_by(Request.start_time, Request.id)
    if lock:
        q = q.with_for_update(skip_locked=True, of=Request)
    return q.all()


def mark_exported(reqs: list[Request], now: dt.datetime) -> str:
    batch_id = str(uuid.uuid4())
    for req in reqs:
        req.exported_at = now
        req.export_batch_id = batch_id
    return batch_id


def batch_requests(db: Session, batch_id: str, *, lock: bool = False) -> list[Request]:
    q = _base_query(db).filter(Request.export_batch_id == batch_id).order_by(Request.start_time, Request.id)
    if lock:
        q = q.with_for_update(of=Request)
    return q.all()


def pending_cancellations(db: Session) -> list[Request]:
    """Bookings cancelled after they went into a downloaded file. 25Live would
    still hold the event if the file was uploaded, so an admin has to cancel it there."""
    cutoff = dt.datetime.utcnow() - dt.timedelta(days=CANCEL_WINDOW_DAYS)
    return (_base_query(db)
            .filter(Request.status == RequestStatus.CANCELLED,
                    Request.exported_at.isnot(None),
                    Request.cancel_handled_at.is_(None),
                    or_(Request.updated_at.is_(None), Request.updated_at >= cutoff))
            .order_by(Request.start_time, Request.id).all())
