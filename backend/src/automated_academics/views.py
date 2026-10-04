"""Which sessions belong in a class's, faculty member's or room's week.

Shared by the JSON API and the PDF/Excel exports so they always agree.
"""

from __future__ import annotations

from typing import Any, Literal

from .models import Institution, Timetable

Kind = Literal["batch", "faculty", "room"]
KINDS: tuple[Kind, ...] = ("batch", "faculty", "room")


def entities(inst: Institution, kind: Kind) -> list[tuple[str, str]]:
    """(id, human label) for every batch / faculty member / room."""
    if kind == "batch":
        return [(b.id, f"{b.id} · {b.program} Sem {b.semester}" + (" (group)" if b.group_of else ""))
                for b in inst.batches]
    if kind == "faculty":
        return [(f.id, f"{f.name} ({f.department})") for f in inst.faculty]
    return [(r.id, f"{r.name} ({r.capacity} seats)") for r in inst.rooms]


def session_views(inst: Institution, tt: Timetable, kind: Kind, ident: str) -> list[dict[str, Any]]:
    """Sessions of one entity, sorted by day and start, with names resolved.

    Raises LookupError if the entity does not exist. Lectures are zero-based.
    """
    offerings = {o.id: o for o in inst.offerings}
    courses = {c.code: c for c in inst.courses}
    faculty = {f.id: f for f in inst.faculty}
    rooms = {r.id: r for r in inst.rooms}
    batches = {b.id: b for b in inst.batches}
    known = {"batch": batches, "faculty": faculty, "room": rooms}[kind]
    if ident not in known:
        raise LookupError(f"{kind} {ident!r} not found")

    def relevant(p) -> bool:
        o = offerings[p.offering_id]
        if kind == "faculty":
            return o.faculty_id == ident
        if kind == "room":
            return p.room_id == ident
        # a section also shows its sub-groups' labs; a sub-group also shows its parent's sessions
        return ident in inst.occupied_batches(o.batch_ids) or any(
            batches[b].group_of == ident for b in o.batch_ids)

    cal = inst.calendar
    out = []
    for p in sorted(tt.placements, key=lambda p: (p.day, p.start)):
        # saved edits are not guaranteed valid; skip rows that cannot be displayed
        if p.offering_id not in offerings or p.room_id not in rooms or not 0 <= p.day < cal.days:
            continue
        if not relevant(p):
            continue
        o = offerings[p.offering_id]
        out.append({
            "offering_id": o.id, "session_index": p.session_index,
            "course_code": o.course_code, "course_name": courses[o.course_code].name,
            "faculty_id": o.faculty_id, "faculty_name": faculty[o.faculty_id].name,
            "batch_ids": o.batch_ids, "room_id": p.room_id, "room_name": rooms[p.room_id].name,
            "day": p.day, "day_name": cal.day_names[p.day],
            "start": p.start, "length": p.length,
        })
    return out
