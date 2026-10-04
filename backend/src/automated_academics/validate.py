"""Independent clash checker.

Deliberately does not reuse solver internals so it can verify solver output and
check manual edits made in the UI. It tolerates malformed input (unknown ids,
days out of range, duplicate placements) and reports it as conflicts instead
of raising.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .models import Institution, Timetable


@dataclass(frozen=True)
class Conflict:
    message: str
    offering_ids: tuple[str, ...]


def find_conflict_details(inst: Institution, tt: Timetable) -> list[Conflict]:
    cal = inst.calendar
    offerings = {o.id: o for o in inst.offerings}
    rooms = {r.id: r for r in inst.rooms}
    faculty = {f.id: f for f in inst.faculty}
    out: list[Conflict] = []
    seen: dict[tuple[str, str, int, int], str] = {}
    fac_load: dict[tuple[str, int], int] = defaultdict(int)
    fac_offerings: dict[tuple[str, int], set[str]] = defaultdict(set)

    expected = {(o.id, i): ln for o in inst.offerings for i, ln in enumerate(o.sessions)}
    placed: set[tuple[str, int]] = set()

    for p in tt.placements:
        tag = f"{p.offering_id}#{p.session_index}"
        if p.offering_id not in offerings:
            out.append(Conflict(f"unknown offering {p.offering_id}", (p.offering_id,)))
            continue
        if p.room_id not in rooms:
            out.append(Conflict(f"{tag}: unknown room {p.room_id}", (p.offering_id,)))
            continue
        if not 0 <= p.day < cal.days:
            out.append(Conflict(f"{tag}: day {p.day} outside the week", (p.offering_id,)))
            continue
        off = offerings[p.offering_id]

        key = (p.offering_id, p.session_index)
        if key in placed:
            out.append(Conflict(f"{tag}: session placed twice", (p.offering_id,)))
        placed.add(key)

        if not cal.block_fits(p.start, p.length):
            out.append(Conflict(f"{tag}: block crosses a break or the end of the day", (p.offering_id,)))
        if expected.get(key) != p.length:
            out.append(Conflict(f"{tag}: unknown session or wrong length", (p.offering_id,)))
        total = sum(b.strength for b in inst.batches if b.id in off.batch_ids)
        if rooms[p.room_id].capacity < total:
            out.append(Conflict(f"{tag}: room {p.room_id} too small ({total} students)", (p.offering_id,)))
        fac_load[(off.faculty_id, p.day)] += p.length
        fac_offerings[(off.faculty_id, p.day)].add(p.offering_id)

        blocked = {(s.day, s.period) for s in faculty[off.faculty_id].unavailable}
        for t in range(p.start, p.start + p.length):
            if (p.day, t) in blocked:
                out.append(Conflict(
                    f"{tag}: faculty {off.faculty_id} unavailable on day {p.day} period {t}",
                    (p.offering_id,)))
            keys = [("F", off.faculty_id), ("R", p.room_id)] + [
                ("B", b) for b in inst.occupied_batches(off.batch_ids)
            ]
            for kind, ident in keys:
                k = (kind, ident, p.day, t)
                if k in seen and seen[k] != p.offering_id:
                    out.append(Conflict(
                        f"clash {kind}:{ident} day {p.day} period {t}: {seen[k]} vs {p.offering_id}",
                        (seen[k], p.offering_id)))
                elif k in seen:
                    out.append(Conflict(
                        f"clash {kind}:{ident} day {p.day} period {t}: {p.offering_id} overlaps itself",
                        (p.offering_id,)))
                seen[k] = p.offering_id

    for key in expected.keys() - placed:
        out.append(Conflict(f"unplaced session {key[0]}#{key[1]}", (key[0],)))

    for (fid, d), load in fac_load.items():
        if load > faculty[fid].max_periods_per_day:
            out.append(Conflict(f"{fid} overloaded on day {d}: {load} periods",
                                tuple(sorted(fac_offerings[(fid, d)]))))
    return out


def find_conflicts(inst: Institution, tt: Timetable) -> list[str]:
    return [c.message for c in find_conflict_details(inst, tt)]
