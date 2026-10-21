"""A class's, teacher's or room's weekly timetable as an iCalendar (.ics) file, for a phone or computer calendar.

Each session becomes one event that repeats every week from the first matching day of the term until its last day.
Times are "floating" (no time zone), so a calendar shows them as the same clock time wherever the phone is, which is what a
college timetable means. The unique id of an event depends only on the session, so when a calendar re-reads the address after
the timetable changed, it updates the events instead of duplicating them. Holidays are not known to the app: a calendar shows
every week of the term.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta

from .models import Institution, Timetable
from .views import Kind, entities, session_views

_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MAX_TERM_DAYS = 400


def weekday_of(day_names: list[str], day: int) -> int:
    """0 = Monday. A day called "Mon", "Monday", "mon." counts as Monday; any other name falls back to its position."""
    name = day_names[day].strip().lower()[:3]
    return _WEEKDAYS.index(name) if name in _WEEKDAYS else day % 7


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")


def _fold(line: str) -> list[str]:
    """Lines longer than 75 bytes continue on the next line after a space; never splitting a character."""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > (75 if not out else 74):
            out.append(cur)
            cur, size = "", 0
        cur += ch
        size += n
    out.append(cur)
    return [out[0], *(" " + x for x in out[1:])]


def has_times(inst: Institution) -> bool:
    cal = inst.calendar
    return bool(cal.times or cal.clocks or cal.day_times)


def build_ics(inst: Institution, tt: Timetable, kind: Kind, ident: str, term_start: date, term_end: date,
              stamp: datetime) -> str:
    """Raises LookupError for an unknown entity and ValueError if the lecture times are not set."""
    cal = inst.calendar
    sessions = session_views(inst, tt, kind, ident)
    label = dict(entities(inst, kind))[ident]
    offerings = {o.id: o for o in inst.offerings}
    events = []
    for s in sessions:
        rows = cal.times_on(s["day"], inst.clock_of_offering(offerings[s["offering_id"]]))
        if not rows or s["start"] + s["length"] > len(rows):
            raise ValueError("the lecture times are not set, so there is nothing to put in a calendar")
        hh, mm = rows[s["start"]].start.split(":")
        eh, em = rows[s["start"] + s["length"] - 1].end.split(":")
        first = term_start + timedelta(days=(weekday_of(cal.day_names, s["day"]) - term_start.weekday()) % 7)
        if first > term_end:
            continue
        who = ", ".join(s["batch_ids"])
        title = {"batch": f"{s['course_name']} ({s['faculty_name']})", "faculty": f"{s['course_name']} \u2013 {who}",
                 "room": f"{s['course_name']} \u2013 {who} ({s['faculty_name']})"}[kind]
        uid = hashlib.sha256(f"{kind}|{ident}|{s['offering_id']}|{s['session_index']}".encode()).hexdigest()[:32]
        events.append([
            "BEGIN:VEVENT", f"UID:{uid}@automated-academics", f"DTSTAMP:{stamp:%Y%m%dT%H%M%SZ}",
            f"DTSTART:{first:%Y%m%d}T{hh}{mm}00", f"DTEND:{first:%Y%m%d}T{eh}{em}00",
            f"RRULE:FREQ=WEEKLY;UNTIL={term_end:%Y%m%d}T235959",
            f"SUMMARY:{_escape(title)}", f"LOCATION:{_escape(s['room_name'])}",
            "DESCRIPTION:" + _escape(f"{s['course_code']} {s['course_name']}\nTeacher: {s['faculty_name']}\nClass: {who}\nRoom: {s['room_name']}"),
            "END:VEVENT",
        ])
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Automated Academics//Timetable//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:{_escape(inst.name + ' \u2013 ' + label)}",
             "X-PUBLISHED-TTL:PT12H", "REFRESH-INTERVAL;VALUE=DURATION:PT12H"]
    for e in events:
        lines += e
    lines.append("END:VCALENDAR")
    return "\r\n".join(part for line in lines for part in _fold(line)) + "\r\n"
