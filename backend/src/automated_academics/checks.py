"""Checking institution data as it is entered.

Two layers, both reporting problems *located* by table and row so a UI can highlight the cell:

  check_institution(data)   structure: required ids, duplicates, references between tables, field
                            types and ranges. Anything reported here means the data cannot be saved.
  diagnose(institution)     sense: data that is well formed but cannot possibly be scheduled (a
                            session with no room big enough, a teacher with more lectures than free
                            slots), plus warnings and notes. These do not block saving.

`level` is "error", "warning" or "info".
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any

from pydantic import ValidationError

from .models import Institution
from .solver import _room_ok

# table (as it appears in the JSON) -> the field that identifies a row
ID_FIELD = {"rooms": "id", "faculty": "id", "batches": "id", "courses": "code", "offerings": "id"}
# JSON key -> the section a UI shows it under
SECTIONS = {"name": "settings", "calendar": "settings", "weights": "settings", "pins": "pins", **{k: k for k in ID_FIELD}}


@dataclass(frozen=True)
class Issue:
    level: str
    section: str | None  # settings | rooms | faculty | batches | courses | offerings
    index: int | None  # zero-based row within the section
    field: str | None
    message: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _error(section, index, field, message) -> Issue:
    return Issue("error", section, index, field, message)


def _rows(data: dict, key: str) -> list:
    v = data.get(key)
    return v if isinstance(v, list) else []


def _get(row: Any, key: str) -> Any:
    return row.get(key) if isinstance(row, dict) else None


# ---------------------------------------------------------------- structure

def check_institution(data: Any) -> tuple[Institution | None, list[Issue]]:
    """Validate raw institution data. Returns (institution or None, problems)."""
    if not isinstance(data, dict):
        return None, [_error(None, None, None, "expected an object with the institution's data")]

    issues: list[Issue] = []
    ids: dict[str, dict[str, int]] = {}

    for section, field in ID_FIELD.items():
        seen: dict[str, int] = {}
        for i, row in enumerate(_rows(data, section)):
            value = _get(row, field)
            if not isinstance(value, str) or not value.strip():
                issues.append(_error(section, i, field, f"{field} is required"))
            elif value in seen:
                issues.append(_error(section, i, field, f"duplicate {field} {value!r} (also row {seen[value] + 1})"))
            else:
                seen[value] = i
        ids[section] = seen

    for i, o in enumerate(_rows(data, "offerings")):
        fid, code, batch_ids = _get(o, "faculty_id"), _get(o, "course_code"), _get(o, "batch_ids")
        if isinstance(fid, str) and fid and fid not in ids["faculty"]:
            issues.append(_error("offerings", i, "faculty_id", f"unknown faculty {fid!r}"))
        if isinstance(code, str) and code and code not in ids["courses"]:
            issues.append(_error("offerings", i, "course_code", f"unknown course {code!r}"))
        if isinstance(batch_ids, list):
            for b in batch_ids:
                if isinstance(b, str) and b not in ids["batches"]:
                    issues.append(_error("offerings", i, "batch_ids", f"unknown batch {b!r}"))

    batches = _rows(data, "batches")
    for i, b in enumerate(batches):
        parent, own = _get(b, "group_of"), _get(b, "id")
        if not isinstance(parent, str) or not parent:
            continue
        if parent == own:
            issues.append(_error("batches", i, "group_of", "a batch cannot be a sub-group of itself"))
        elif parent not in ids["batches"]:
            issues.append(_error("batches", i, "group_of", f"unknown parent batch {parent!r}"))
        elif _get(batches[ids["batches"][parent]], "group_of"):
            issues.append(_error("batches", i, "group_of",
                                 f"{parent!r} is itself a sub-group; sub-groups of sub-groups are not supported"))

    offering_sessions = {_get(o, "id"): len(_get(o, "sessions") or []) for o in _rows(data, "offerings")
                         if isinstance(_get(o, "id"), str)}
    for i, p in enumerate(_rows(data, "pins")):
        oid, idx, room = _get(p, "offering_id"), _get(p, "session_index"), _get(p, "room_id")
        if oid not in offering_sessions:
            issues.append(_error("pins", i, "offering_id", f"unknown offering {oid!r}"))
        elif isinstance(idx, int) and idx >= offering_sessions[oid]:
            issues.append(_error("pins", i, "session_index", f"{oid} has only {offering_sessions[oid]} session(s)"))
        if room not in (None, "") and room not in ids["rooms"]:
            issues.append(_error("pins", i, "room_id", f"unknown room {room!r}"))

    inst: Institution | None = None
    try:
        inst = Institution.model_validate(data)
    except ValidationError as e:
        for err in e.errors():
            loc = err["loc"]
            msg = err["msg"].removeprefix("Value error, ")
            if not loc:  # a cross-table rule; the located checks above already say which row
                if not issues:
                    issues.append(_error(None, None, None, msg))
                continue
            section = SECTIONS.get(str(loc[0]), None)
            index = loc[1] if len(loc) > 1 and isinstance(loc[1], int) else None
            rest = [str(x) for x in loc[(2 if index is not None else 1):]]
            field = ".".join(rest) or None
            if section == "settings" and field is None:
                field = str(loc[0])
            issues.append(_error(section, index, field, msg))

    return (None if issues else inst), issues


# ---------------------------------------------------------------- sense

def diagnose(inst: Institution) -> list[Issue]:
    """Problems and notes about well-formed data. Errors here mean no timetable can exist."""
    cal = inst.calendar
    days, lectures = cal.days, cal.lectures_per_day
    courses = {c.code: c for c in inst.courses}
    batches = {b.id: b for b in inst.batches}
    faculty_by_id = {f.id: f for f in inst.faculty}
    out: list[Issue] = []

    def err(section, index, field, msg):
        out.append(Issue("error", section, index, field, msg))

    def note(level, section, index, field, msg):
        out.append(Issue(level, section, index, field, msg))

    faculty_hours: dict[str, int] = defaultdict(int)
    batch_hours: dict[str, int] = defaultdict(int)
    leaves = set(inst.leaf_batches())
    used_courses: set[str] = set()

    for oi, o in enumerate(inst.offerings):
        course = courses[o.course_code]
        used_courses.add(o.course_code)
        strength = sum(batches[b].strength for b in o.batch_ids)
        eligible = [r for r in inst.rooms if _room_ok(r, course.room_kind, strength)]
        if not eligible:
            kind_ok = [r for r in inst.rooms if _room_ok(r, course.room_kind, 0)]
            biggest = max((r.capacity for r in kind_ok), default=None)
            have = f"the largest has {biggest} seats" if biggest else "there are none"
            err("offerings", oi, "course_code",
                f"{o.id}: needs a {course.room_kind.value} for {strength} students, but {have}")

        fac = faculty_by_id[o.faculty_id]
        blocked = {(s.day, s.lecture) for s in fac.unavailable}
        for length in set(o.sessions):
            if not any(cal.block_fits(p, length) and not any((d, t) in blocked for t in range(p, p + length))
                       for d in range(days) for p in range(lectures)):
                err("offerings", oi, "sessions",
                    f"{o.id}: no place for a {length}-lecture block (day length, breaks and "
                    f"{fac.name}'s unavailable slots leave none)")

        hours = sum(o.sessions)
        faculty_hours[o.faculty_id] += hours
        for b in inst.occupied_batches(o.batch_ids) & leaves:
            batch_hours[b] += hours

    for fi, f in enumerate(inst.faculty):
        for field in ("unavailable", "avoid"):
            outside = [s for s in getattr(f, field) if not (0 <= s.day < days and 0 <= s.lecture < lectures)]
            if outside:
                note("warning", "faculty", fi, field,
                     f"{f.name}: {len(outside)} {field} slot(s) are outside the week and are ignored")
        free = days * lectures - len({(s.day, s.lecture) for s in f.unavailable
                                      if 0 <= s.day < days and 0 <= s.lecture < lectures})
        taught = faculty_hours.get(f.id, 0)
        if taught > free:
            err("faculty", fi, None, f"{f.name} teaches {taught} lectures a week but only {free} slots are free")
        elif taught > days * f.max_lectures_per_day:
            err("faculty", fi, "max_lectures_per_day",
                f"{f.name} teaches {taught} lectures a week but the daily limit allows only "
                f"{days * f.max_lectures_per_day}")
        if taught == 0:
            note("info", "faculty", fi, None, f"{f.name} is not assigned to any offering")

    for bi, b in enumerate(inst.batches):
        if b.id in leaves:
            n = batch_hours.get(b.id, 0)
            if n > days * lectures:
                err("batches", bi, None, f"{b.id} has {n} lectures a week but the week has only {days * lectures} slots")
            elif n == 0:
                note("warning", "batches", bi, None, f"{b.id} has no lectures (no offering includes it)")

    for ci, c in enumerate(inst.courses):
        if c.code not in used_courses:
            note("info", "courses", ci, None, f"{c.code} is not used by any offering")

    for ri, r in enumerate(inst.rooms):
        if not any(_room_ok(r, courses[o.course_code].room_kind,
                            sum(batches[b].strength for b in o.batch_ids)) for o in inst.offerings):
            note("info", "rooms", ri, None, f"no session can use {r.name} (wrong kind or too small)")

    if any(b >= lectures - 1 for b in cal.break_after):
        note("warning", "settings", None, "calendar.break_after", "a break after the last lecture has no effect")

    out += _pin_problems(inst)
    out += _room_demand(inst)
    return out


# ---------------------------------------------------------------- pins and room demand

def _pin_problems(inst: Institution) -> list[Issue]:
    """Pins that cannot hold, alone or together."""
    cal = inst.calendar
    offerings = {o.id: o for o in inst.offerings}
    courses = {c.code: c for c in inst.courses}
    batches = {b.id: b for b in inst.batches}
    faculty = {f.id: f for f in inst.faculty}
    rooms = {r.id: r for r in inst.rooms}
    out: list[Issue] = []
    claims: dict[tuple, int] = {}  # (kind, id, day, lecture) -> the pin that holds it

    for pi, p in enumerate(inst.pins):
        o = offerings[p.offering_id]
        length = o.sessions[p.session_index]
        tag = f"{p.offering_id} session {p.session_index + 1}"
        day_name = cal.day_names[p.day] if p.day < cal.days else f"day {p.day + 1}"
        where = f"{day_name} lecture {p.start + 1}"
        if p.day >= cal.days or not cal.block_fits(p.start, length):
            out.append(Issue("error", "pins", pi, "start",
                             f"{tag} is pinned at {where}, but a {length}-lecture block can't go there (outside the week, or across a break)"))
            continue
        covered = [(p.day, t) for t in range(p.start, p.start + length)]
        fac = faculty[o.faculty_id]
        blocked = {(s.day, s.lecture) for s in fac.unavailable}
        if any(c in blocked for c in covered):
            out.append(Issue("error", "pins", pi, "start", f"{tag} is pinned at {where}, when {fac.name} is unavailable"))
        if p.room_id is not None:
            strength = sum(batches[b].strength for b in o.batch_ids)
            if not _room_ok(rooms[p.room_id], courses[o.course_code].room_kind, strength):
                out.append(Issue("error", "pins", pi, "room_id",
                                 f"{tag} is pinned to {rooms[p.room_id].name}, which is the wrong kind of room or too small"))
        keys = [("F", o.faculty_id)] + [("B", b) for b in inst.occupied_batches(o.batch_ids)]
        if p.room_id is not None:
            keys.append(("R", p.room_id))
        for kind, ident in keys:
            for day, t in covered:
                k = (kind, ident, day, t)
                if k in claims and claims[k] != pi:
                    other = inst.pins[claims[k]]
                    what = {"F": "the same teacher", "B": "the same class", "R": "the same room"}[kind]
                    out.append(Issue("error", "pins", pi, None,
                                     f"{tag} and {other.offering_id} session {other.session_index + 1} are pinned at the same "
                                     f"time ({where}) for {what}"))
                claims[k] = pi
    return out


def _room_demand(inst: Institution) -> list[Issue]:
    """More lecture-hours than the rooms that could hold them have slots for (a necessary condition).

    Sessions are grouped by which rooms could take them; a group needing more hours than its rooms
    have slots can never be placed, however cleverly.
    """
    batches = {b.id: b for b in inst.batches}
    courses = {c.code: c for c in inst.courses}
    cal = inst.calendar
    slots = cal.days * cal.lectures_per_day
    by_set: dict[frozenset[str], list[tuple[int, str]]] = defaultdict(list)
    for o in inst.offerings:
        strength = sum(batches[b].strength for b in o.batch_ids)
        eligible = frozenset(r.id for r in inst.rooms if _room_ok(r, courses[o.course_code].room_kind, strength))
        if eligible:  # sessions with no eligible room are already reported on their offering
            by_set[eligible].append((sum(o.sessions), o.id))
    out: list[Issue] = []
    names = {r.id: r.name for r in inst.rooms}
    for rooms, _ in list(by_set.items()):
        # sessions whose possible rooms are all inside this set can only use these rooms
        inside = [x for s, xs in by_set.items() if s <= rooms for x in xs]
        need, capacity = sum(h for h, _ in inside), len(rooms) * slots
        if need > capacity:
            label = ", ".join(sorted(names[r] for r in rooms)) if len(rooms) <= 3 else f"{len(rooms)} rooms"
            out.append(Issue("error", "rooms", None, None,
                             f"{need} lectures a week can only use {label}, which have {capacity} slots between them"))
    return out
