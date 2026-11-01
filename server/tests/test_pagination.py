"""?limit= / ?offset= pagination and the X-Total-Count header (services/pagination.py)."""

from __future__ import annotations

import datetime as dt

import pytest

from models.communication import Notification, NotificationType
from models.organization_and_venue import Organization


@pytest.fixture()
def notifications(db, leader):
    """23 notifications for the leader, n0 newest ... n22 oldest."""
    now = dt.datetime.now(dt.timezone.utc)
    for i in range(23):
        db.add(Notification(user_id=leader.id, type=NotificationType.MEMBERSHIP_VERIFIED,
                            message=f"n{i}", is_read=False, created_at=now - dt.timedelta(minutes=i)))
    db.flush()


def messages(r):
    return [n["message"] for n in r.json()]


def test_default_is_unchanged(client, auth, leader, notifications):
    r = client.get("/notifications", headers=auth(leader))
    assert r.status_code == 200
    assert len(r.json()) == 23                     # default limit is still 50
    assert r.headers["X-Total-Count"] == "23"


def test_limit_and_offset(client, auth, leader, notifications):
    r = client.get("/notifications", params={"limit": 10, "offset": 10}, headers=auth(leader))
    assert messages(r) == [f"n{i}" for i in range(10, 20)]
    assert r.headers["X-Total-Count"] == "23"


def test_last_page_is_short(client, auth, leader, notifications):
    r = client.get("/notifications", params={"limit": 10, "offset": 20}, headers=auth(leader))
    assert messages(r) == ["n20", "n21", "n22"]


def test_offset_past_the_end_is_empty(client, auth, leader, notifications):
    r = client.get("/notifications", params={"limit": 10, "offset": 100}, headers=auth(leader))
    assert r.status_code == 200 and r.json() == []
    assert r.headers["X-Total-Count"] == "23"


def test_pages_dont_overlap_or_skip(client, auth, leader, notifications):
    seen = []
    for offset in range(0, 23, 5):
        seen += messages(client.get("/notifications", params={"limit": 5, "offset": offset},
                                    headers=auth(leader)))
    assert seen == [f"n{i}" for i in range(23)]


def test_total_counts_only_matching_rows(client, auth, leader, notifications, db):
    db.query(Notification).filter(Notification.message.in_(["n0", "n1"])).update({"is_read": True})
    db.flush()
    r = client.get("/notifications", params={"unread_only": True, "limit": 5}, headers=auth(leader))
    assert r.headers["X-Total-Count"] == "21" and len(r.json()) == 5


@pytest.mark.parametrize("params", [{"offset": -1}, {"limit": 0}, {"limit": 201}])
def test_bad_values_are_rejected(client, auth, leader, params):
    assert client.get("/notifications", params=params, headers=auth(leader)).status_code == 422


def test_endpoint_without_a_default_limit_returns_everything(client, auth, leader, db):
    for name in ["Zeta Club", "Alpha Club", "Mu Club"]:
        db.add(Organization(name=name))
    db.flush()
    everything = client.get("/organizations", headers=auth(leader))
    total = int(everything.headers["X-Total-Count"])
    assert len(everything.json()) == total >= 3

    page = client.get("/organizations", params={"limit": 2, "offset": 1}, headers=auth(leader))
    assert [o["name"] for o in page.json()] == [o["name"] for o in everything.json()][1:3]


def test_search_and_pagination_together(client, auth, leader, db):
    for i in range(5):
        db.add(Organization(name=f"Paginate Test Club {i}"))
    db.flush()
    r = client.get("/organizations", params={"search": "paginate test", "limit": 2, "offset": 4},
                   headers=auth(leader))
    assert r.headers["X-Total-Count"] == "5"
    assert [o["name"] for o in r.json()] == ["Paginate Test Club 4"]


def test_requests_mine_paginates(client, auth, leader):
    r = client.get("/requests/mine", params={"limit": 5}, headers=auth(leader))
    assert r.status_code == 200 and "X-Total-Count" in r.headers
