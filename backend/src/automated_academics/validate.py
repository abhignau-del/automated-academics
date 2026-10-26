"""Independent clash checker.

Deliberately does not reuse solver internals so it can verify solver output and
check manual edits made in the UI. It tolerates malformed input (unknown ids,
days out of range, duplicate placements) and reports it as conflicts instead
of raising.

Messages are written for the person reading the timetable: names rather than ids, "Mon, lecture 1
(9:00-9:50)" rather than "day 0 lecture 0".
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .models import Institution, RoomKind, Timetable


@dataclass(frozen=True)
class Conflict:
    message: str
    offering_ids: tuple[str, ...]
    kind: str = "other"  # teacher | class | room | unavailable | overload | room-size | home-room | block | missing | invalid


_KIND = {"F": "teacher", "B": "class", "R": "room"}


def find_conflict_details(inst: Institution, tt: Timetable) -> list[Conflict]:
    cal = inst.calendar
    offerings = {o.id: o for o in inst.offerings}
    rooms = {r.id: r for r in inst.rooms}
    faculty = {f.id: f for f in inst.faculty}
    courses = {c.code: c for c in inst.courses}
    out: list[Conflict] = []
    seen: dict[tuple[str, str, int, int], str] = {}
    clashes: dict[tuple, list[str]] = defaultdict(list)
    fac_load: dict[tuple[str, int], int] = defaultdict(int)
    fac_offerings: dict[tuple[str, int], set[str]] = defaultdict(set)

    expected = {(o.id, i): ln for o in inst.offerings for i, ln in enumerate(o.sessions)}
    placed: set[tuple[str, int]] = set()

    def day_name(d: int) -> str:
        return cal.day_names[d] if 0 <= d < cal.days else f"day {d + 1}"

    def when(day: int, lecture: int, length: int = 1, offering_id: str | None = None) -> str:
        """"Mon, lecture 1 (9:00-9:50)" (lectures are counted from 1; the time only if the institution has times)."""
        span = f"lectures {lecture + 1}-{lecture + length}" if length > 1 else f"lecture {lecture + 1}"
        clock = inst.clock_of_offering(offerings[offering_id]) if offering_id in offerings else None
        t = cal.time_label(day, lecture, length, clock) if 0 <= day < cal.days else None
        return f"{day_name(day)}, {span}" + (f" ({t})" if t else "")

    def what(offering_id: str, session_index: int | None = None) -> str:
        """"Calculus for CS-UG1", plus which session when it has several."""
        o = offerings.get(offering_id)
        if o is None:
            return f"offering {offering_id!r}"
        c = courses.get(o.course_code)
        text = f"{c.name if c else o.course_code} for {', '.join(o.batch_ids)}"
        if session_index is not None and len(o.sessions) > 1:
            text += f" (session {session_index + 1} of {len(o.sessions)})"
        return text

    def person(fid: str) -> str:
        f = faculty.get(fid)
        return f.name if f else fid

    for p in tt.placements:
        if p.offering_id not in offerings:
            out.append(Conflict(f"A session refers to an offering that doesn't exist ({p.offering_id!r}).",
                                (p.offering_id,), "invalid"))
            continue
        if p.room_id not in rooms:
            out.append(Conflict(f"{what(p.offering_id, p.session_index)} is in a room that doesn't exist ({p.room_id!r}).",
                                (p.offering_id,), "invalid"))
            continue
        if not 0 <= p.day < cal.days:
            out.append(Conflict(f"{what(p.offering_id, p.session_index)} is on day {p.day + 1}, which is outside the week.",
                                (p.offering_id,), "invalid"))
            continue
        off = offerings[p.offering_id]
        label = what(p.offering_id, p.session_index)

        key = (p.offering_id, p.session_index)
        if key in placed:
            out.append(Conflict(f"{label} is placed twice.", (p.offering_id,), "invalid"))
        placed.add(key)

        if not cal.block_fits(p.start, p.length):
            out.append(Conflict(f"{label} on {when(p.day, p.start, p.length, p.offering_id)}: a {p.length}-lecture block can't "
                                "run across a break or past the end of the day.", (p.offering_id,), "block"))
        if expected.get(key) != p.length:
            out.append(Conflict(f"{label} has the wrong length or isn't a session of this offering.", (p.offering_id,), "invalid"))
        total = sum(b.strength for b in inst.batches if b.id in off.batch_ids)
        if rooms[p.room_id].capacity < total:
            out.append(Conflict(f"{label} on {when(p.day, p.start, p.length, p.offering_id)}: {rooms[p.room_id].name} seats "
                                f"{rooms[p.room_id].capacity} but {total} students attend.", (p.offering_id,), "room-size"))
        course = courses.get(off.course_code)
        home = inst.home_room_of(off.batch_ids) if course is not None and course.room_kind is RoomKind.CLASSROOM else None
        if home is not None and p.room_id != home and home in rooms:
            out.append(Conflict(f"{label} on {when(p.day, p.start, p.length, p.offering_id)}: its class lives in {rooms[home].name}, "
                                f"but this is in {rooms[p.room_id].name}.", (p.offering_id,), "home-room"))
        fac_load[(off.faculty_id, p.day)] += p.length
        fac_offerings[(off.faculty_id, p.day)].add(p.offering_id)

        blocked = {(s.day, s.lecture) for s in faculty[off.faculty_id].unavailable}
        for t in range(p.start, p.start + p.length):
            if (p.day, t) in blocked:
                out.append(Conflict(f"{person(off.faculty_id)} is unavailable on {when(p.day, t, 1, p.offering_id)}, "
                                    f"but teaches {label} then.", (p.offering_id,), "unavailable"))
            keys = [("F", off.faculty_id), ("R", p.room_id)] + [
                ("B", b) for b in inst.occupied_batches(off.batch_ids)
            ]
            for kind, ident in keys:
                k = (kind, ident, p.day, t)
                if k in seen and seen[k] != p.offering_id:
                    clashes[(kind, seen[k], p.offering_id, p.day, t)].append(ident)
                elif k in seen:
                    clashes[(kind, p.offering_id, None, p.day, t)].append(ident)
                seen[k] = p.offering_id

    # One real double-booking can hit several entities at once (a section and each of its lab
    # groups, say); report it once, naming all of them.
    def names(kind: str, idents: list[str]) -> str:
        if kind == "F":
            return " and ".join(person(i) for i in idents)
        if kind == "R":
            return " and ".join(rooms[i].name if i in rooms else i for i in idents)
        return " and ".join(idents)

    for (kind, a, b, day, t), idents in clashes.items():
        who = names(kind, idents)
        at = when(day, t, 1, a)
        if b is None:
            out.append(Conflict(f"{what(a)} is placed on top of itself on {at}.", (a,), "invalid"))
            continue
        sentence = {
            "F": f"{who} is teaching two things at once on {at}",
            "B": f"{who} {'have' if len(idents) > 1 else 'has'} two lectures at once on {at}",
            "R": f"{who} is booked twice on {at}",
        }[kind]
        out.append(Conflict(f"{sentence}: {what(a)} and {what(b)}.", (a, b), _KIND[kind]))

    for key in sorted(expected.keys() - placed):
        out.append(Conflict(f"{what(key[0], key[1])} has not been placed.", (key[0],), "missing"))

    for (fid, d), load in fac_load.items():
        if load > faculty[fid].max_lectures_per_day:
            out.append(Conflict(f"{person(fid)} has {load} lectures on {day_name(d)}; the most they can teach in a day is "
                                f"{faculty[fid].max_lectures_per_day}.", tuple(sorted(fac_offerings[(fid, d)])), "overload"))
    return out


def find_conflicts(inst: Institution, tt: Timetable) -> list[str]:
    return [c.message for c in find_conflict_details(inst, tt)]
