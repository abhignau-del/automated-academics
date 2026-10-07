"""Solve a large institution one department at a time.

Solving everything at once stops working somewhere past about a thousand sessions that share rooms,
teachers and joint classes (measured: 806 sessions take about 6 minutes, 1,615 find nothing in 10).
Instead:

1. Split the offerings into groups: first the joint classes that bring several departments together,
   then one group per department (a very large department is split by semester).
2. Solve the groups one after another. Each group works around everything already placed: the teachers,
   classes and rooms those sessions use are busy. If a group cannot be fitted, it is merged with the
   group placed just before it and the two are solved together, and so on, so the worst case is the
   ordinary all-at-once solve and an answer is never lost to the splitting.
3. Spend the remaining time improving the soft goals one group at a time, with the rest held still.
   An improvement is kept only if the whole timetable's penalty gets better.

The result is valid but usually a little less polished than a full solve would be if it could finish.
"""

from __future__ import annotations

import logging
import math
import time
from collections import Counter, defaultdict, deque
from collections.abc import Sequence

from .models import Institution, Placement, Timetable, Weights
from .quality import measure, score
from .solver import InfeasibleError, TimedOut, solve

log = logging.getLogger("automated_academics")

DECOMPOSE_ABOVE = 400  # sessions; smaller institutions solve fine all at once (and a bit better)
MAX_GROUP_SESSIONS = 350  # a department bigger than this is split by semester

_ZERO = Weights(repeat_course_day=0, batch_gaps=0, faculty_gaps=0, peak_day_load=0, avoid_slot=0)


def _sessions(inst: Institution) -> int:
    return sum(len(o.sessions) for o in inst.offerings)


def partition(inst: Institution, max_sessions: int = MAX_GROUP_SESSIONS) -> list[list[str]]:
    """Groups of offering ids, in the order to solve them."""
    dept = {b.id: b.department for b in inst.batches}
    sem = {b.id: b.semester for b in inst.batches}
    shared: list[str] = []
    by_dept: dict[str, list] = defaultdict(list)
    for o in inst.offerings:
        depts = {dept[b] for b in o.batch_ids}
        if len(depts) > 1:
            shared.append(o.id)
        else:
            by_dept[next(iter(depts))].append(o)

    groups: list[list[str]] = []
    if shared:
        groups.append(shared)
    for offs in sorted(by_dept.values(), key=lambda g: -sum(len(o.sessions) for o in g)):
        if sum(len(o.sessions) for o in offs) <= max_sessions:
            groups.append([o.id for o in offs])
            continue
        by_sem: dict[int, list[str]] = defaultdict(list)
        for o in offs:
            by_sem[sem[o.batch_ids[0]]].append(o.id)
        groups.extend(by_sem[k] for k in sorted(by_sem))
    return groups


def room_pool(inst: Institution, ids: Sequence[str], busy: Counter[str], keep: Sequence[str] = ()) -> set[str]:
    """A small set of rooms for one group: the least-used rooms of each kind that are big enough.

    Offering every room to every session makes the model enormous (a university has hundreds), and almost all
    of that choice is pointless since rooms of a kind are interchangeable. Departments have home rooms
    in practice too. Sized for about half-full rooms plus a margin; `keep` are rooms that must stay available.
    """
    by_id = {o.id: o for o in inst.offerings}
    courses = {c.code: c for c in inst.courses}
    strength = {b.id: b.strength for b in inst.batches}
    cal = inst.calendar
    slots = cal.days * cal.lectures_per_day
    pool = set(keep)
    for kind in {courses[by_id[i].course_code].room_kind for i in ids}:
        offs = [by_id[i] for i in ids if courses[by_id[i].course_code].room_kind is kind]
        hours = sum(sum(o.sessions) for o in offs)
        need = max(sum(strength[b] for b in o.batch_ids) for o in offs)
        fits = sorted((r for r in inst.rooms if r.kind is kind and r.capacity >= need), key=lambda r: (busy[r.id], r.id))
        pool |= {r.id for r in fits[: math.ceil(hours / (slots * 0.5)) + 2]}
    return pool


def solve_decomposed(inst: Institution, time_limit_s: float = 60.0, workers: int = 8,
                     max_sessions: int = MAX_GROUP_SESSIONS) -> Timetable:
    started = time.monotonic()
    groups = partition(inst, max_sessions)
    hard_only = inst.model_copy(update={"weights": _ZERO})
    ceiling = min(max(20.0, time_limit_s * 0.5), 180.0)  # for one group, hard rules only
    give_up = started + max(600.0, 4 * time_limit_s)  # merging groups can snowball; never run unboundedly

    # ---- step 2: place the groups in turn, merging with the previous one when stuck ----
    solved: list[tuple[list[str], list[Placement]]] = []
    queue = deque(groups)
    while queue:
        ids = queue.popleft()
        while True:
            fixed = [p for _, ps in solved for p in ps]
            busy = Counter(p.room_id for p in fixed)
            try:
                try:  # first with a small room pool (fast), then, if that is too tight, with every room
                    tt = solve(hard_only, time_limit_s=ceiling, min_first_solution_s=ceiling, workers=workers,
                               only=set(ids), fixed=fixed, room_ids=room_pool(inst, ids, busy))
                except InfeasibleError:
                    tt = solve(hard_only, time_limit_s=ceiling, min_first_solution_s=ceiling, workers=workers,
                               only=set(ids), fixed=fixed)
                solved.append((ids, tt.placements))
                log.info("placed %d offerings (%d groups done)", len(ids), len(solved))
                break
            except InfeasibleError:
                if not solved:
                    raise  # impossible even with nothing else placed
                if time.monotonic() > give_up:
                    raise TimedOut("could not fit the departments together within the time allowed; "
                                   "try a longer time limit") from None
                prev, _ = solved.pop()
                log.info("a group did not fit around the others; solving it together with the previous one")
                ids = prev + ids

    placements = [p for _, ps in solved for p in ps]
    current = measure(inst, Timetable(placements=placements, status="FEASIBLE"))
    best = score(inst.weights, current)

    # ---- step 3: improve one group at a time against the soft goals ----
    if any(getattr(inst.weights, k) for k in _ZERO.model_fields) and len(solved) > 1:
        left = time_limit_s - (time.monotonic() - started)
        per = left / len(solved)
        for n, (ids, mine) in enumerate(solved):
            if per < 3.0:
                break
            others = [p for m, (_, ps) in enumerate(solved) if m != n for p in ps]
            pool = room_pool(inst, ids, Counter(p.room_id for p in others), keep=[p.room_id for p in mine])
            try:
                tt = solve(inst, time_limit_s=per, workers=workers, only=set(ids), fixed=others, hint=mine, room_ids=pool)
            except InfeasibleError:
                continue
            if tt.penalty < best:
                solved[n] = (ids, tt.placements)
                best = tt.penalty
        placements = [p for _, ps in solved for p in ps]

    out = Timetable(placements=placements, status="FEASIBLE")
    out.breakdown = measure(inst, out)
    out.penalty = score(inst.weights, out.breakdown)
    return out


def solve_auto(inst: Institution, time_limit_s: float = 30.0, workers: int = 8, **kw) -> Timetable:
    """The all-at-once solve for ordinary sizes, the department-by-department one for large institutions."""
    if _sessions(inst) > DECOMPOSE_ABOVE and len(partition(inst)) > 1:
        return solve_decomposed(inst, time_limit_s, workers)
    return solve(inst, time_limit_s=time_limit_s, workers=workers, **kw)
