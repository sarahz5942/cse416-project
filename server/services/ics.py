"""Building iCalendar (.ics) feeds, so bookings show up in Google/Apple calendars."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

CAMPUS_TZ = ZoneInfo("America/New_York")


def _escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _utc(t: dt.datetime) -> str:
    """Naive campus time -> 20261009T220000Z"""
    if t.tzinfo is None:
        t = t.replace(tzinfo=CAMPUS_TZ)
    return t.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _fold(line: str) -> str:
    """iCalendar lines are limited to 75 octets; continue with a leading space."""
    out, raw = [], line.encode()
    while len(raw) > 75:
        cut = 75
        while (raw[cut] & 0xC0) == 0x80:  # don't split a UTF-8 character
            cut -= 1
        out.append(raw[:cut].decode())
        raw = b" " + raw[cut:]
    out.append(raw.decode())
    return "\r\n".join(out)


def build_calendar(name: str, events: list[dict], now: dt.datetime | None = None) -> str:
    """events: {"uid", "title", "start", "end", "location", "description"}"""
    stamp = _utc(now or dt.datetime.now(dt.timezone.utc))
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//CampusReserve//Bookings//EN",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", f"X-WR-CALNAME:{_escape(name)}"]
    for e in events:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{e['uid']}@campusreserve",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{_utc(e['start'])}",
            f"DTEND:{_utc(e['end'])}",
            f"SUMMARY:{_escape(e['title'])}",
        ]
        if e.get("location"):
            lines.append(f"LOCATION:{_escape(e['location'])}")
        if e.get("description"):
            lines.append(f"DESCRIPTION:{_escape(e['description'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
