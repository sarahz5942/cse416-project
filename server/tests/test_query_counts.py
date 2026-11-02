"""Request lists must not run an extra query per booking.

The admin and leader lists show each request's room, club and requester. Without
eager loading each of those is a separate database query per row, and every query
is a network round trip to the hosted database: 40 bookings meant 40+ extra trips.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event

from models.room import RequestStatus

LISTS = [
    ("/admin/requests?status=approved&limit=200", "admin"),
    ("/admin/sync", "admin"),
    ("/admin/sync/export", "admin"),
    ("/requests/mine", "leader"),
]


@pytest.fixture()
def queries(engine):
    box = {"n": 0}

    def count(conn, cursor, statement, params, context, executemany):
        box["n"] += 1

    event.listen(engine, "before_cursor_execute", count)
    yield box
    event.remove(engine, "before_cursor_execute", count)


def test_lists_use_the_same_number_of_queries_for_3_or_25_bookings(
        client, auth, admin, leader, make_room, make_request, queries, db):
    users = {"admin": admin, "leader": leader}
    seen = {}
    made = 0
    for size in (3, 25):
        while made < size:
            made += 1
            make_request(days=7 + made, status=RequestStatus.APPROVED, in_room=make_room())
        db.flush()
        for url, who in LISTS:
            queries["n"] = 0
            r = client.get(url, headers=auth(users[who]))
            assert r.status_code == 200, url
            seen[(url, size)] = queries["n"]

    for url, _ in LISTS:
        assert seen[(url, 25)] <= seen[(url, 3)] + 1, (
            f"{url}: {seen[(url, 3)]} queries for 3 bookings but {seen[(url, 25)]} for 25")
