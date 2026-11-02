"""Tests for the 25Live CSV export (services/live25_csv.py and /admin/sync/export...)."""

from __future__ import annotations

import csv
import datetime as dt
import io

import pytest

from models.room import RequestStatus, SyncStatus
from services import live25_csv
from services.room_merge import campus_now


def parse(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


@pytest.fixture()
def approved(make_request):
    """An approved booking; each call needs a different `days` (no double booking)."""
    def _make(days, **kw):
        return make_request(days=days, status=RequestStatus.APPROVED, **kw)
    return _make


# --------------------------------------------------------------------------- the file itself

def test_row_follows_the_25live_template(make_room, make_request, db):
    room = make_room(name="Room 236", external_ref="48213")
    start = dt.datetime(2030, 3, 4, 18, 0)
    req = make_request(days=7, status=RequestStatus.APPROVED, in_room=room,
                       event_name="Robotics | Demo Night")
    req.expected_attendance = 35
    req.start_time, req.end_time = start, start + dt.timedelta(hours=2)
    req.setup_start, req.cleanup_end = start - dt.timedelta(minutes=30), start + dt.timedelta(hours=2, minutes=15)
    db.flush()

    row = parse(live25_csv.build_csv([live25_csv.make_row(req)]))[0]

    assert list(row) == live25_csv.HEADERS
    assert row["EVENT_ID"] == str(req.id)
    assert row["EVENT_NAME"] == "Robotics / Demo Night"      # '|' would split a multi-value column
    assert row["EVENT_TYPE"] == "Meeting"
    assert row["PRIMARY_ORGANIZATION"] == req.organization.name
    assert row["ADHOC_DATES"] == "2030-03-04"
    assert row["START_TIME"] == "18:00:00" and row["END_TIME"] == "20:00:00"
    assert row["PRE_EVENT_TIME"] == "30" and row["POST_EVENT_TIME"] == "15"
    assert row["LOCATIONS"] == "48213"
    assert row["EXP_HEADCOUNT"] == "35"
    assert str(req.id) in row["INTERNAL_NOTES"]


def test_long_names_are_cut_to_25lives_limits(make_request):
    req = make_request(days=7, status=RequestStatus.APPROVED, event_name="x" * 200)
    row = live25_csv.make_row(req)
    assert len(row.values[live25_csv.HEADERS.index("EVENT_NAME")]) == 40
    assert len(row.values[live25_csv.HEADERS.index("EVENT_TITLE")]) == 120


def test_file_has_no_byte_order_mark_and_is_utf8(make_request):
    text = live25_csv.build_csv([live25_csv.make_row(make_request(days=7, status=RequestStatus.APPROVED,
                                                                event_name="Caf\u00e9 night"))])
    assert not text.startswith("\ufeff")
    assert "Caf\u00e9 night".encode("utf-8") in text.encode("utf-8")


def test_event_settings_come_from_the_environment(make_request, monkeypatch):
    monkeypatch.setenv("LIVE25_EVENT_TYPE", "Student Event")
    monkeypatch.setenv("LIVE25_PRIMARY_ORGANIZATION", "Student Activities")
    row = parse(live25_csv.build_csv([live25_csv.make_row(make_request(days=7, status=RequestStatus.APPROVED))]))[0]
    assert row["EVENT_TYPE"] == "Student Event"
    assert row["PRIMARY_ORGANIZATION"] == "Student Activities"


def test_room_without_a_25live_id_uses_its_name_with_a_warning(make_request):
    row = live25_csv.make_row(make_request(days=7, status=RequestStatus.APPROVED))
    assert row.values[live25_csv.HEADERS.index("LOCATIONS")] == "Room 101"
    assert row.warnings and not row.problems


def test_events_of_24_hours_or_more_cannot_be_imported(make_request, db):
    req = make_request(days=7, status=RequestStatus.APPROVED)
    req.end_time = req.start_time + dt.timedelta(hours=30)
    req.cleanup_end = req.end_time
    assert live25_csv.make_row(req).problems


def test_event_past_midnight_gets_an_earlier_end_time(make_request, db):
    req = make_request(days=7, status=RequestStatus.APPROVED, start_hour=22, hours=3)
    row = live25_csv.make_row(req)
    assert row.values[live25_csv.HEADERS.index("START_TIME")] == "22:00:00"
    assert row.values[live25_csv.HEADERS.index("END_TIME")] == "01:00:00"   # 25Live reads this as next day
    assert not row.problems


# --------------------------------------------------------------------------- access

def test_export_is_admin_only(client, auth, leader, host):
    assert client.get("/admin/sync/export").status_code == 401
    for user in (leader, host):
        assert client.get("/admin/sync/export", headers=auth(user)).status_code == 403
        assert client.post("/admin/sync/export", headers=auth(user)).status_code == 403


# --------------------------------------------------------------------------- queue and dedupe

def test_preview_lists_only_new_approved_bookings(client, auth, admin, approved, make_request, db):
    mine = approved(10)
    approved(11, sync=SyncStatus.SYNCED, external_booking_ref="123")        # already in 25Live
    approved(12, exported_at=dt.datetime.utcnow(), export_batch_id="b1")    # already downloaded
    make_request(days=13)                                                    # still pending
    approved(-3)                                                             # in the past

    data = client.get("/admin/sync/export", headers=auth(admin)).json()
    assert [i["id"] for i in data["ready"]] == [str(mine.id)]
    assert data["ready"][0]["event_name"] == "Test Event"
    assert data["skipped"] == []

    with_past = client.get("/admin/sync/export", params={"include_past": True}, headers=auth(admin)).json()
    assert len(with_past["ready"]) == 2


def test_downloading_marks_bookings_so_they_are_not_exported_twice(client, auth, admin, approved, db):
    first = approved(10)
    second = approved(11)

    r = client.post("/admin/sync/export", headers=auth(admin))
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2 and body["filename"].startswith("25live-import-")
    assert {row["EVENT_ID"] for row in parse(body["csv"])} == {str(first.id), str(second.id)}
    db.refresh(first)
    assert first.exported_at is not None and first.export_batch_id == body["batch_id"]
    assert first.sync_status == SyncStatus.NOT_SYNCED   # downloaded is not the same as in 25Live

    # Nothing new, so a second download is refused instead of repeating the same bookings.
    again = client.post("/admin/sync/export", headers=auth(admin))
    assert again.status_code == 409

    # A booking approved later goes into the next file, alone.
    third = approved(12)
    nxt = client.post("/admin/sync/export", headers=auth(admin)).json()
    assert [row["EVENT_ID"] for row in parse(nxt["csv"])] == [str(third.id)]
    assert nxt["batch_id"] != body["batch_id"]


def test_bookings_that_cannot_be_imported_stay_in_the_queue(client, auth, admin, approved, db):
    ok = approved(10)
    too_long = approved(12)
    too_long.end_time = too_long.start_time + dt.timedelta(hours=30)
    too_long.cleanup_end = too_long.end_time
    db.flush()

    body = client.post("/admin/sync/export", headers=auth(admin)).json()
    assert body["count"] == 1 and [s["id"] for s in body["skipped"]] == [str(too_long.id)]
    db.refresh(too_long)
    assert too_long.exported_at is None
    assert ok.id != too_long.id


# --------------------------------------------------------------------------- batches

def test_redownload_gives_the_same_bookings_and_drops_cancelled_ones(client, auth, admin, approved, db):
    keep, cancelled = approved(10), approved(11)
    batch = client.post("/admin/sync/export", headers=auth(admin)).json()["batch_id"]

    same = client.get(f"/admin/sync/batches/{batch}/csv", headers=auth(admin)).json()
    assert same["count"] == 2

    cancelled.status = RequestStatus.CANCELLED
    db.flush()
    after = client.get(f"/admin/sync/batches/{batch}/csv", headers=auth(admin)).json()
    assert [r["EVENT_ID"] for r in parse(after["csv"])] == [str(keep.id)]

    assert client.get("/admin/sync/batches/nope/csv", headers=auth(admin)).status_code == 404


def test_undo_puts_a_batch_back_in_the_queue(client, auth, admin, approved, db):
    req = approved(10)
    batch = client.post("/admin/sync/export", headers=auth(admin)).json()["batch_id"]
    assert client.get("/admin/sync/export", headers=auth(admin)).json()["ready"] == []

    r = client.post(f"/admin/sync/batches/{batch}/undo", headers=auth(admin))
    assert r.json() == {"released": 1}
    db.refresh(req)
    assert req.exported_at is None and req.export_batch_id is None
    ready = client.get("/admin/sync/export", headers=auth(admin)).json()["ready"]
    assert [i["id"] for i in ready] == [str(req.id)]


def test_confirm_marks_bookings_as_in_25live(client, auth, admin, approved, db):
    req = approved(10)
    batch = client.post("/admin/sync/export", headers=auth(admin)).json()["batch_id"]

    assert client.post(f"/admin/sync/batches/{batch}/confirm", headers=auth(admin)).json() == {"confirmed": 1}
    db.refresh(req)
    assert req.sync_status == SyncStatus.SYNCED
    assert req.external_booking_ref == f"CSV-{req.id}"

    assert client.post(f"/admin/sync/batches/{batch}/confirm", headers=auth(admin)).json() == {"confirmed": 0}
    assert client.post(f"/admin/sync/batches/{batch}/undo", headers=auth(admin)).status_code == 409
    batches = client.get("/admin/sync/export", headers=auth(admin)).json()["batches"]
    assert batches[0]["count"] == 1 and batches[0]["confirmed"] == 1


# --------------------------------------------------------------------------- cancellations

def test_cancelled_after_download_shows_up_until_handled(client, auth, admin, approved, db):
    req = approved(10)
    client.post("/admin/sync/export", headers=auth(admin))
    req.status = RequestStatus.CANCELLED
    db.flush()

    todo = client.get("/admin/sync/export", headers=auth(admin)).json()["cancellations"]
    assert [c["id"] for c in todo] == [str(req.id)]
    assert todo[0]["event_id"] == f"CSV-{req.id}"

    assert client.post(f"/admin/sync/cancellations/{req.id}/done", headers=auth(admin)).status_code == 200
    assert client.get("/admin/sync/export", headers=auth(admin)).json()["cancellations"] == []


def test_cancelled_before_download_never_needs_cancelling_in_25live(client, auth, admin, approved, db):
    req = approved(10)
    req.status = RequestStatus.CANCELLED
    db.flush()
    assert client.get("/admin/sync/export", headers=auth(admin)).json()["cancellations"] == []
    assert client.post(f"/admin/sync/cancellations/{req.id}/done", headers=auth(admin)).status_code == 409
