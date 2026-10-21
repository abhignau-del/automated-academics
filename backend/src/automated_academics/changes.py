"""A plain-language summary of what changed between two versions of an institution, for the audit log.

Only ids and counts go in: never the contents of a row beyond its id, and never anything secret.
"""

from __future__ import annotations

from typing import Any

from .models import Institution, Timetable

_NOUN = {"rooms": ("room", "rooms"), "faculty": ("teacher", "teachers"), "batches": ("class", "classes"),
         "courses": ("course", "courses"), "offerings": ("offering", "offerings"), "pins": ("locked session", "locked sessions")}
_KEY = {"rooms": "id", "faculty": "id", "batches": "id", "courses": "code", "offerings": "id"}
_CAP = 50  # ids kept per list in the detail


def _ids(rows: list[Any], key: str) -> dict[str, Any]:
    return {getattr(r, key): r for r in rows}


def describe_changes(old: Institution, new: Institution) -> tuple[str, dict[str, Any]]:
    """("changed 2 teachers (CSF1, CSF2) and the calendar", {...detail}); an empty summary means nothing changed."""
    detail: dict[str, Any] = {}
    parts: list[str] = []

    settings = [name for name, a, b in (("name", old.name, new.name), ("calendar", old.calendar, new.calendar),
                                        ("goal weights", old.weights, new.weights)) if a != b]
    if settings:
        detail["settings"] = settings

    def phrase(verb: str, table: str, ids: list[str]) -> str:
        one, many = _NOUN[table]
        shown = ", ".join(ids[:4]) + (f", +{len(ids) - 4} more" if len(ids) > 4 else "")
        return f"{verb} {len(ids)} {one if len(ids) == 1 else many} ({shown})"

    for table, key in _KEY.items():
        a, b = _ids(getattr(old, table), key), _ids(getattr(new, table), key)
        added = sorted(set(b) - set(a))
        removed = sorted(set(a) - set(b))
        changed = sorted(k for k in set(a) & set(b) if a[k] != b[k])
        if added or removed or changed:
            detail[table] = {"added": added[:_CAP], "removed": removed[:_CAP], "changed": changed[:_CAP]}
        for verb, ids in (("added", added), ("removed", removed), ("changed", changed)):
            if ids:
                parts.append(phrase(verb, table, ids))

    pa = {(p.offering_id, p.session_index) for p in old.pins}
    pb = {(p.offering_id, p.session_index) for p in new.pins}
    if old.pins != new.pins:
        detail["pins"] = {"locked": len(pb - pa), "unlocked": len(pa - pb), "moved": len(pa & pb)}
        if pb - pa:
            parts.append(f"locked {len(pb - pa)} session{'s' if len(pb - pa) != 1 else ''}")
        if pa - pb:
            parts.append(f"unlocked {len(pa - pb)} session{'s' if len(pa - pb) != 1 else ''}")
        if not (pb - pa or pa - pb):
            parts.append("moved locked sessions")

    for s in settings:
        parts.append("renamed the institution" if s == "name" else f"changed the {s}")
    return ("; ".join(parts), detail) if parts else ("", {})


def describe_timetable_edit(old: Timetable | None, new: Timetable) -> tuple[str, dict[str, Any]]:
    """How many sessions a hand edit moved, added or removed."""
    key = lambda p: (p.offering_id, p.session_index)  # noqa: E731
    a = {key(p): p for p in (old.placements if old else [])}
    b = {key(p): p for p in new.placements}
    moved = sorted(k[0] for k in set(a) & set(b) if a[k] != b[k])
    added = sorted(k[0] for k in set(b) - set(a))
    removed = sorted(k[0] for k in set(a) - set(b))
    if not (moved or added or removed):
        return "", {}
    bits = []
    if moved:
        bits.append(f"moved {len(moved)} session{'s' if len(moved) != 1 else ''}")
    if added:
        bits.append(f"added {len(added)}")
    if removed:
        bits.append(f"removed {len(removed)}")
    return ", ".join(bits), {"moved": len(moved), "added": len(added), "removed": len(removed),
                             "offerings": sorted(set(moved + added + removed))[:_CAP]}


def timetable_delta(old: Timetable, new: Timetable) -> dict[str, set[tuple[str, int]]]:
    """Which sessions were moved (different day, time or room), newly added, or taken away between two timetables."""
    key = lambda p: (p.offering_id, p.session_index)  # noqa: E731
    a = {key(p): p for p in old.placements}
    b = {key(p): p for p in new.placements}
    return {"moved": {k for k in set(a) & set(b) if a[k] != b[k]}, "added": set(b) - set(a), "removed": set(a) - set(b)}
