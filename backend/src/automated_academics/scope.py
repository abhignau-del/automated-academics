"""What a department-scoped editor may change.

A coordinator of one department should be able to maintain that department's classes, teachers, courses and
offerings without being able to disturb anyone else's. This works out, from the saved institution and the one
being saved, every change made, and reports the ones outside the person's departments.

Belonging:
  * a teacher, a class or a course belongs to the department written on it;
  * an offering belongs to the departments of the classes it is taught to (a joint class between departments
    needs all of them, so one department can't change what another's students attend);
  * a pin and a timetable session belong to their offering;
  * rooms and the institution's settings (days, lecture times, goal weights, name) are shared by everyone,
    so a scoped editor can't change them.
"""

from __future__ import annotations

from typing import Any

from .models import Institution, Timetable

SHARED = "the institution's shared settings"


def _by_key(rows: list[Any], key: str) -> dict[str, Any]:
    return {getattr(r, key): r for r in rows}


def _offering_departments(inst: Institution, offering_id: str) -> set[str] | None:
    """The departments of the classes an offering is taught to; None if the offering isn't in `inst`."""
    o = next((x for x in inst.offerings if x.id == offering_id), None)
    if o is None:
        return None
    dept = {b.id: b.department for b in inst.batches}
    return {dept[b] for b in o.batch_ids if b in dept}


def institution_violations(old: Institution, new: Institution, departments: set[str]) -> list[str]:
    """Plain-language list of the changes from `old` to `new` that fall outside `departments`."""
    out: list[str] = []

    def outside(depts: set[str]) -> set[str]:
        return {d for d in depts if d not in departments}

    if (old.name != new.name or old.calendar != new.calendar or old.weights != new.weights):
        out.append(f"{SHARED} (name, days, lecture times, goal weights)")
    if old.rooms != new.rooms:
        out.append("rooms, which are shared by every department")

    for label, rows_old, rows_new, key in (
        ("teacher", old.faculty, new.faculty, "id"),
        ("class", old.batches, new.batches, "id"),
        ("course", old.courses, new.courses, "code"),
    ):
        a, b = _by_key(rows_old, key), _by_key(rows_new, key)
        for k in sorted(set(a) | set(b)):
            if a.get(k) == b.get(k):
                continue
            depts = {x.department for x in (a.get(k), b.get(k)) if x is not None}  # where it was, and where it is going
            bad = outside(depts)
            if bad:
                out.append(f"{label} {k!r} belongs to {', '.join(sorted(bad))}")

    oa, ob = _by_key(old.offerings, "id"), _by_key(new.offerings, "id")
    for k in sorted(set(oa) | set(ob)):
        if oa.get(k) == ob.get(k):
            continue
        depts: set[str] = set()
        if k in oa:
            depts |= _offering_departments(old, k) or set()
        if k in ob:
            depts |= _offering_departments(new, k) or set()
        bad = outside(depts)
        if bad:
            out.append(f"offering {k!r} involves classes of {', '.join(sorted(bad))}")

    pa = {(p.offering_id, p.session_index): p for p in old.pins}
    pb = {(p.offering_id, p.session_index): p for p in new.pins}
    for k in sorted(set(pa) | set(pb)):
        if pa.get(k) == pb.get(k):
            continue
        depts = (_offering_departments(new, k[0]) or _offering_departments(old, k[0]) or set())
        bad = outside(depts)
        if bad:
            out.append(f"a locked session of offering {k[0]!r} belongs to {', '.join(sorted(bad))}")
    return out


def timetable_violations(inst: Institution, old: Timetable | None, new: Timetable, departments: set[str]) -> list[str]:
    """Sessions moved, added or removed that belong to an offering outside `departments`."""
    key = lambda p: (p.offering_id, p.session_index)  # noqa: E731
    a = {key(p): p for p in (old.placements if old else [])}
    b = {key(p): p for p in new.placements}
    out: list[str] = []
    seen: set[str] = set()
    for k in sorted(set(a) | set(b)):
        if a.get(k) == b.get(k):
            continue
        depts = _offering_departments(inst, k[0]) or set()
        bad = {d for d in depts if d not in departments}
        if bad and k[0] not in seen:
            seen.add(k[0])
            out.append(f"a session of offering {k[0]!r} belongs to {', '.join(sorted(bad))}")
    return out
