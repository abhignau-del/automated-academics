"""Independent clash checker.

Deliberately does not reuse solver internals so it can verify solver output and,
later, check manual edits made in the UI.
"""

from __future__ import annotations

from collections import defaultdict

from .models import Institution, Timetable


def find_conflicts(inst: Institution, tt: Timetable) -> list[str]:
    cal = inst.calendar
    offerings = {o.id: o for o in inst.offerings}
    rooms = {r.id: r for r in inst.rooms}
    faculty = {f.id: f for f in inst.faculty}
    problems: list[str] = []
    seen: dict[tuple[str, str, int, int], str] = {}
    fac_load: dict[tuple[str, int], int] = defaultdict(int)

    expected = {(o.id, i): ln for o in inst.offerings for i, ln in enumerate(o.sessions)}
    got = {(p.offering_id, p.session_index): p for p in tt.placements}
    for key in expected.keys() - got.keys():
        problems.append(f"unplaced session {key}")

    for p in tt.placements:
        off = offerings[p.offering_id]
        if not cal.block_fits(p.start, p.length):
            problems.append(f"{p.offering_id}#{p.session_index}: block crosses break or day end")
        if expected.get((p.offering_id, p.session_index)) != p.length:
            problems.append(f"{p.offering_id}#{p.session_index}: wrong session length")
        total = sum(b.strength for b in inst.batches if b.id in off.batch_ids)
        if rooms[p.room_id].capacity < total:
            problems.append(f"{p.offering_id}#{p.session_index}: room {p.room_id} too small")
        fac_load[(off.faculty_id, p.day)] += p.length
        blocked = {(s.day, s.period) for s in faculty[off.faculty_id].unavailable}
        for t in range(p.start, p.start + p.length):
            if (p.day, t) in blocked:
                problems.append(f"{p.offering_id}: faculty unavailable day {p.day} period {t}")
            keys = [("F", off.faculty_id), ("R", p.room_id)] + [
                ("B", b) for b in inst.occupied_batches(off.batch_ids)
            ]
            for kind, ident in keys:
                k = (kind, ident, p.day, t)
                if k in seen:
                    problems.append(
                        f"clash {kind}:{ident} day {p.day} period {t}: {seen[k]} vs {p.offering_id}"
                    )
                seen[k] = p.offering_id

    for (fid, d), load in fac_load.items():
        if load > faculty[fid].max_periods_per_day:
            problems.append(f"{fid} overloaded on day {d}: {load} periods")
    return problems
