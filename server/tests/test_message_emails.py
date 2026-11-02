"""Emails for direct messages (routers/messages.py send_message).

The first message in a quiet conversation emails the other participants; more
messages within 30 minutes only create the in-app notification.
Nothing is really sent: services.notify.send_email is replaced with a list.
"""

from __future__ import annotations

import datetime as dt

import pytest

import services.notify as notify_module
from models.communication import Message, NotificationType


@pytest.fixture()
def emails(monkeypatch):
    sent = []
    monkeypatch.setattr(notify_module, "send_email", lambda to, subject, body: sent.append((to, subject, body)))
    return sent


def start_chat(client, auth, me, *others):
    r = client.post("/conversations", json={"participant_ids": [str(o.id) for o in others]}, headers=auth(me))
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def send(client, auth, me, conv_id, text):
    r = client.post(f"/conversations/{conv_id}/messages", json={"body": text}, headers=auth(me))
    assert r.status_code == 201, r.text


def test_first_message_emails_the_other_person(client, auth, leader, admin, emails):
    conv = start_chat(client, auth, leader, admin)
    send(client, auth, leader, conv, "Is the ballroom free Friday?")

    assert [to for to, _, _ in emails] == [admin.email]
    to, subject, body = emails[0]
    assert leader.name in subject
    assert "Is the ballroom free Friday?" in body


def test_sender_is_not_emailed(client, auth, leader, admin, emails):
    conv = start_chat(client, auth, leader, admin)
    send(client, auth, leader, conv, "hi")
    assert leader.email not in [to for to, _, _ in emails]


def test_quick_follow_ups_are_not_emailed(client, auth, leader, admin, emails):
    conv = start_chat(client, auth, leader, admin)
    send(client, auth, leader, conv, "one")
    send(client, auth, leader, conv, "two")
    send(client, auth, admin, conv, "reply")       # an active chat: nobody needs an email
    assert len(emails) == 1


def test_follow_ups_still_notify_in_the_app(client, auth, leader, admin, emails):
    conv = start_chat(client, auth, leader, admin)
    send(client, auth, leader, conv, "one")
    send(client, auth, leader, conv, "two")
    items = client.get("/notifications", headers=auth(admin)).json()
    assert len([n for n in items if n["type"] == NotificationType.NEW_MESSAGE.value]) == 2


def test_message_after_a_quiet_half_hour_is_emailed(client, auth, leader, admin, emails, db):
    conv = start_chat(client, auth, leader, admin)
    send(client, auth, leader, conv, "first")
    # pretend that message was sent an hour ago
    db.query(Message).filter(Message.conversation_id == conv).update(
        {Message.created_at: dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)})
    db.flush()
    db.expire_all()

    send(client, auth, admin, conv, "sorry, just saw this")

    assert [to for to, _, _ in emails] == [admin.email, leader.email]


def test_group_chat_emails_everyone_else(client, auth, leader, admin, make_user, emails):
    third = make_user()
    conv = start_chat(client, auth, leader, admin, third)
    send(client, auth, leader, conv, "group hello")
    assert sorted(to for to, _, _ in emails) == sorted([admin.email, third.email])
