"""Independent measurement of timetable quality (the soft goals).

Like validate.py, this deliberately shares no code with the solver, so it can both
verify what the solver optimised and score hand-edited timetables in the UI.
Hard rules (clashes etc.) are validate.py's job; this only measures how *good* a
valid timetable is. Placements that cannot be interpreted are skipped.

Metrics (lower is better, all in whole lectures or sessions)
  repeat_course_day  extra sessions of the same course on one day (2 sessions = 1)
  batch_gaps         idle lectures between a class's first and last lecture of a day
  faculty_gaps       the same, for each faculty member
  peak_day_load      sum over classes of their busiest day's lecture count
  avoid_slot         lectures placed in slots a faculty member asked to avoid
"""

from __future__ import annotations

from collections import defaultdict

from .models import Institution, Timetable, Weights

METRICS = ("repeat_course_day", "batch_gaps", "faculty_gaps", "peak_day_load", "avoid_slot")


def _gaps(cells: set[tuple[int, int]]) -> int:
    """Idle lectures between first and last occupied lecture, summed over days."""
    by_day: dict[int, list[int]] = defaultdict(list)
    for day, lecture in cells:
        by_day[day].append(lecture)
    return sum(max(ls) - min(ls) + 1 - len(ls) for ls in by_day.values())


def measure(inst: Institution, tt: Timetable) -> dict[str, int]:
    cal = inst.calendar
    offerings = {o.id: o for o in inst.offerings}
    faculty = {f.id: f for f in inst.faculty}
    leaves = inst.leaf_batches()

    batch_cells: dict[str, set[tuple[int, int]]] = {b: set() for b in leaves}
    fac_cells: dict[str, set[tuple[int, int]]] = defaultdict(set)
    sessions_per_day: dict[tuple[str, int], int] = defaultdict(int)
    avoid_hits = 0

    for p in tt.placements:
        off = offerings.get(p.offering_id)
        if off is None or not 0 <= p.day < cal.days:
            continue
        covered = [(p.day, t) for t in range(p.start, p.start + p.length)]
        sessions_per_day[(off.id, p.day)] += 1
        fac_cells[off.faculty_id].update(covered)
        attending = inst.occupied_batches(off.batch_ids)
        for b in leaves:
            if b in attending:
                batch_cells[b].update(covered)
        avoid = {(s.day, s.lecture) for s in faculty[off.faculty_id].avoid}
        avoid_hits += sum(1 for c in covered if c in avoid)

    peak = 0
    for cells in batch_cells.values():
        per_day: dict[int, int] = defaultdict(int)
        for day, _ in cells:
            per_day[day] += 1
        peak += max(per_day.values(), default=0)

    return {
        "repeat_course_day": sum(max(0, n - 1) for n in sessions_per_day.values()),
        "batch_gaps": sum(_gaps(c) for c in batch_cells.values()),
        "faculty_gaps": sum(_gaps(c) for c in fac_cells.values()),
        "peak_day_load": peak,
        "avoid_slot": avoid_hits,
    }


def score(weights: Weights, metrics: dict[str, int]) -> int:
    """Weighted penalty: what the solver minimises. Lower is better."""
    return sum(getattr(weights, m) * metrics.get(m, 0) for m in METRICS)
