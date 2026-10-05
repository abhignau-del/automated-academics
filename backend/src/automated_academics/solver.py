"""CP-SAT timetable solver.

Formulation
  Every session gets one 0/1 variable per possible start slot (exactly one is chosen) and, separately,
  one 0/1 variable per eligible room (exactly one is chosen). Double-booking is ruled out with
  interval / no-overlap constraints, which CP-SAT handles far better than a variable per
  (slot, room) combination: the model grows with sessions x slots + sessions x rooms,
  not sessions x slots x rooms.

Hard constraints
  * every session is placed exactly once
  * a block never spans a break and never leaves the day
  * no faculty, batch or room is double-booked
  * faculty unavailability and max lectures per day are respected
  * room kind matches the course and capacity covers all attending batches

Soft goals (minimised as a weighted sum; weights live on `Institution.weights`)
  * repeat_course_day  more than one session of the same offering on one day
  * batch_gaps         idle lectures inside a class's day
  * faculty_gaps       idle lectures inside a faculty member's day
  * peak_day_load      a class's busiest day (spreads the week out)
  * avoid_slot         lectures in slots a faculty member asked to avoid

`quality.measure` computes the same quantities independently from a finished
timetable; tests check the two agree.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from .models import Institution, Placement, Room, RoomKind, Timetable
from .quality import measure, score

log = logging.getLogger("automated_academics")

# Least time stage 1 (find any valid timetable) may use, whatever the requested limit.
MIN_FIRST_SOLUTION_S = 20.0


class InfeasibleError(RuntimeError):
    """Raised when no timetable was produced."""


class Unsatisfiable(InfeasibleError):
    """Proven impossible: the constraints cannot all hold, however much time is spent."""


class TimedOut(InfeasibleError):
    """No timetable was found in the time given; one may exist."""


def _room_ok(room: Room, needed: RoomKind, strength: int) -> bool:
    if room.capacity < strength:
        return False
    if needed is RoomKind.LAB:
        return room.kind is RoomKind.LAB
    if needed is RoomKind.HALL:
        return room.kind is RoomKind.HALL
    return room.kind in (RoomKind.CLASSROOM, RoomKind.HALL)


def _new_solver(time_limit_s: float, workers: int, log_search: bool) -> cp_model.CpSolver:
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_workers = workers
    solver.parameters.log_search_progress = log_search
    # On timetabling models CP-SAT's presolve "probing" and symmetry detection dominate the run
    # time: on a 272-session instance presolve took 55 s of a 61 s solve (the search took 5 s), and
    # switching these two off cut the total to about 4 s. They are preprocessing aids, so turning
    # them off does not change which timetables are valid.
    solver.parameters.cp_model_probing_level = 0
    solver.parameters.symmetry_level = 0
    return solver


@dataclass
class _Session:
    offering_id: str
    index: int
    length: int
    starts: dict[int, cp_model.IntVar]  # linear slot (day * lectures_per_day + lecture) -> chosen?
    rooms: list[tuple[Room, cp_model.IntVar]] = field(default_factory=list)


def solve(inst: Institution, time_limit_s: float = 30.0, workers: int = 8,
          log_search: bool = False, min_first_solution_s: float | None = None) -> Timetable:
    cal = inst.calendar
    n_lectures, n_days = cal.lectures_per_day, cal.days
    courses = {c.code: c for c in inst.courses}
    batches = {b.id: b for b in inst.batches}
    faculty = {f.id: f for f in inst.faculty}
    w = inst.weights

    pins = {(p.offering_id, p.session_index): p for p in inst.pins}
    model = cp_model.CpModel()
    sessions: list[_Session] = []
    fac_intervals: dict[str, list] = defaultdict(list)
    batch_intervals: dict[str, list] = defaultdict(list)
    room_intervals: dict[str, list] = defaultdict(list)
    fac_day_load: dict[tuple[str, int], list[tuple[int, cp_model.IntVar]]] = defaultdict(list)
    per_day: dict[tuple[str, int], list[cp_model.IntVar]] = defaultdict(list)
    avoid_terms: list[tuple[int, cp_model.IntVar]] = []  # (avoided lectures covered, start var)
    # who is busy when, as lists of start variables; only needed for the gap and spread goals
    need_occupancy = bool(w.batch_gaps or w.faculty_gaps or w.peak_day_load)
    occupancy: dict[tuple[str, str, int, int], list[cp_model.IntVar]] = defaultdict(list)

    for off in inst.offerings:
        course = courses[off.course_code]
        strength = sum(batches[b].strength for b in off.batch_ids)
        rooms = [r for r in inst.rooms if _room_ok(r, course.room_kind, strength)]
        fac = faculty[off.faculty_id]
        blocked = {(s.day, s.lecture) for s in fac.unavailable}
        avoided = {(s.day, s.lecture) for s in fac.avoid}
        attending = inst.occupied_batches(off.batch_ids)

        for i, length in enumerate(off.sessions):
            if not rooms:
                raise Unsatisfiable(
                    f"{off.id} session {i}: no {course.room_kind.value} with capacity >= {strength}")
            slots = [d * n_lectures + p
                     for d in range(n_days) for p in range(n_lectures)
                     if cal.block_fits(p, length)
                     and not any((d, t) in blocked for t in range(p, p + length))]
            if not slots:
                raise Unsatisfiable(f"{off.id} session {i}: no admissible slot")

            pin = pins.get((off.id, i))
            usable = rooms
            if pin is not None:  # a pinned session may only go where it is pinned
                at = pin.day * n_lectures + pin.start
                if pin.day >= n_days or at not in slots:
                    raise Unsatisfiable(f"{off.id} session {i + 1} is pinned where it cannot go")
                slots = [at]
                if pin.room_id is not None:
                    usable = [r for r in rooms if r.id == pin.room_id]
                    if not usable:
                        raise Unsatisfiable(f"{off.id} session {i + 1} is pinned to a room it cannot use")

            tag = f"{off.id}_{i}"
            starts = {t: model.NewBoolVar(f"s_{tag}_{t}") for t in slots}
            model.AddExactlyOne(starts.values())
            start = model.NewIntVarFromDomain(cp_model.Domain.FromValues(slots), f"start_{tag}")
            model.Add(start == sum(t * v for t, v in starts.items()))
            interval = model.NewFixedSizeIntervalVar(start, length, f"iv_{tag}")

            fac_intervals[off.faculty_id].append(interval)
            for b in attending:
                batch_intervals[b].append(interval)

            sess = _Session(off.id, i, length, starts)
            for r in usable:
                y = model.NewBoolVar(f"r_{tag}_{r.id}")
                sess.rooms.append((r, y))
                room_intervals[r.id].append(
                    model.NewOptionalFixedSizeIntervalVar(start, length, y, f"riv_{tag}_{r.id}"))
            model.AddExactlyOne(y for _, y in sess.rooms)
            sessions.append(sess)

            for t, v in starts.items():
                d, p = divmod(t, n_lectures)
                fac_day_load[(off.faculty_id, d)].append((length, v))
                per_day[(off.id, d)].append(v)
                n_avoided = sum(1 for c in range(p, p + length) if (d, c) in avoided)
                if n_avoided:
                    avoid_terms.append((n_avoided, v))
                if need_occupancy:
                    for c in range(p, p + length):
                        occupancy[("F", off.faculty_id, d, c)].append(v)
                        for b in attending:
                            occupancy[("B", b, d, c)].append(v)

    for intervals in (*fac_intervals.values(), *batch_intervals.values(), *room_intervals.values()):
        if len(intervals) > 1:
            model.AddNoOverlap(intervals)

    for (fid, _d), items in fac_day_load.items():
        cap = faculty[fid].max_lectures_per_day
        if sum(length for length, _ in items) > cap:  # skip constraints that can never bind
            model.Add(sum(length * v for length, v in items) <= cap)

    # ---- stage 1: any valid timetable (hard rules only) ----
    # With the quality goals in the model, finding a first solution can take far longer than finding
    # one without them (at 8x the sample size it never arrived in 60 s, while the hard rules alone
    # solve in 16 s). So find a valid timetable first and use it as the starting point: a short time
    # limit then still returns a result, and a longer one improves on it.
    started = time.monotonic()
    # A constant objective makes CP-SAT use its optimisation search strategies, which find a first
    # solution far faster than its pure-feasibility ones on these models (measured: 272 sessions
    # 2 s against 12-17 s, and 544 sessions 16 s against never within 100 s). The solve still ends
    # at the first solution, since a constant objective cannot be improved.
    model.Minimize(0)
    # Stage 1 stops at its first solution, so a generous ceiling costs nothing when it is easy and
    # prevents "no result" when it is not. The time limit therefore mainly governs the improvement
    # stage: finding any valid timetable can take longer than a very short limit.
    floor = MIN_FIRST_SOLUTION_S if min_first_solution_s is None else min_first_solution_s
    first = _new_solver(max(time_limit_s * 0.85, floor), workers, log_search)
    first_status = first.Solve(model)
    if first_status == cp_model.INFEASIBLE:
        raise Unsatisfiable("no timetable can satisfy all the hard constraints "
                            "(too many sessions for the available faculty, rooms or slots?)")
    if first_status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise TimedOut("no timetable found within the time limit; try a longer one")

    def extract(solution) -> list[Placement]:
        out = []
        for s in sessions:
            t = next(t for t, v in s.starts.items() if solution.Value(v))
            room = next(r for r, y in s.rooms if solution.Value(y))
            d, p = divmod(t, n_lectures)
            out.append(Placement(offering_id=s.offering_id, session_index=s.index, day=d,
                                 start=p, length=s.length, room_id=room.id))
        return out

    start_from = extract(first)
    for s in sessions:  # the goal variables are all determined by these, so the hint is complete
        for v in s.starts.values():
            model.AddHint(v, first.Value(v))
        for _, y in s.rooms:
            model.AddHint(y, first.Value(y))

    # ---- stage 2: improve it against the soft goals ----
    occ_cache: dict[tuple[str, str, int, int], cp_model.IntVar | None] = {}

    def occ(kind: str, ident: str, d: int, t: int):
        """0/1 variable: is this entity busy in this lecture? None when it never can be."""
        key = (kind, ident, d, t)
        if key not in occ_cache:
            lits = occupancy.get(key)
            if not lits:
                occ_cache[key] = None
            elif len(lits) == 1:
                occ_cache[key] = lits[0]
            else:
                v = model.NewBoolVar(f"occ_{kind}_{ident}_{d}_{t}")
                model.Add(sum(lits) == v)  # the no-overlap rules above make this sum 0 or 1
                occ_cache[key] = v
        return occ_cache[key]

    def gap_vars(kind: str, ident: str) -> list[cp_model.IntVar]:
        """One variable per idle lecture that sits between two busy lectures of the same day."""
        gaps = []
        for d in range(n_days):
            cells = [occ(kind, ident, d, t) for t in range(n_lectures)]
            if sum(c is not None for c in cells) < 2:
                continue
            # before[t] / after[t]: true if anything is busy earlier / later that day
            # Each helper is defined exactly (an OR of its inputs), so the objective is exact for
            # every solution found, not only the proven optimum.
            before: list = [None] * n_lectures
            cur = None
            for t in range(1, n_lectures):
                prev = cells[t - 1]
                if prev is not None:
                    nxt = model.NewBoolVar(f"bef_{kind}_{ident}_{d}_{t}")
                    inputs = [prev] if cur is None else [prev, cur]
                    model.AddBoolOr(inputs).OnlyEnforceIf(nxt)
                    for x in inputs:
                        model.AddImplication(x, nxt)
                    cur = nxt
                before[t] = cur
            after: list = [None] * n_lectures
            cur = None
            for t in range(n_lectures - 2, -1, -1):
                nxt_cell = cells[t + 1]
                if nxt_cell is not None:
                    nv = model.NewBoolVar(f"aft_{kind}_{ident}_{d}_{t}")
                    inputs = [nxt_cell] if cur is None else [nxt_cell, cur]
                    model.AddBoolOr(inputs).OnlyEnforceIf(nv)
                    for x in inputs:
                        model.AddImplication(x, nv)
                    cur = nv
                after[t] = cur
            for t in range(n_lectures):
                if before[t] is None or after[t] is None:
                    continue
                g = model.NewBoolVar(f"gap_{kind}_{ident}_{d}_{t}")
                # g is true exactly when something is busy before and after, but not now
                model.AddImplication(g, before[t])
                model.AddImplication(g, after[t])
                if cells[t] is not None:
                    model.AddImplication(g, cells[t].Not())
                    model.AddBoolOr([g, before[t].Not(), after[t].Not(), cells[t]])
                else:
                    model.AddBoolOr([g, before[t].Not(), after[t].Not()])
                gaps.append(g)
        return gaps

    # terms[goal] holds the unweighted quantity per entity; the objective is weight x their sum
    terms: dict[str, list] = defaultdict(list)

    if w.repeat_course_day:
        for (oid, d), lits in per_day.items():
            if len(lits) > 1:
                ex = model.NewIntVar(0, len(lits) - 1, f"ex_{oid}_{d}")
                model.AddMaxEquality(ex, [sum(lits) - 1, 0])  # exactly max(0, sessions that day - 1)
                terms["repeat_course_day"].append(ex)

    leaves = inst.leaf_batches()
    if w.batch_gaps:
        for b in leaves:
            terms["batch_gaps"].extend(gap_vars("B", b))
    if w.faculty_gaps:
        for f in inst.faculty:
            terms["faculty_gaps"].extend(gap_vars("F", f.id))
    if w.peak_day_load:
        for b in leaves:
            loads = []
            for d in range(n_days):
                day_cells = [c for t in range(n_lectures) if (c := occ("B", b, d, t)) is not None]
                if day_cells:
                    loads.append(sum(day_cells))
            if loads:
                peak = model.NewIntVar(0, n_lectures, f"peak_{b}")
                model.AddMaxEquality(peak, loads)  # exactly the busiest day's lecture count
                terms["peak_day_load"].append(peak)
    if w.avoid_slot:
        terms["avoid_slot"].extend(n * v for n, v in avoid_terms)

    def finish(placements: list[Placement], status: str) -> Timetable:
        # Quality comes from the independent measurement of what is actually returned, so it is
        # right even for goals weighted 0. (CP-SAT's own objective figure can differ slightly from
        # the timetable it hands back when it is stopped by the time limit, so it is not used.)
        tt = Timetable(placements=placements, status=status)
        tt.breakdown = measure(inst, tt)
        tt.penalty = score(w, tt.breakdown)
        return tt

    if not terms:  # every goal is switched off: the stage 1 timetable is as good as any
        return finish(start_from, "OPTIMAL")

    model.Minimize(sum(getattr(w, goal) * sum(exprs) for goal, exprs in terms.items()))
    solver = _new_solver(max(time_limit_s - (time.monotonic() - started), 2.0), workers, log_search)
    status = solver.Solve(model)

    baseline = finish(start_from, "FEASIBLE")
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        log.warning("improvement stage found nothing (%s); returning the stage 1 timetable",
                    solver.StatusName(status))
        return baseline
    improved = finish(extract(solver), solver.StatusName(status))
    for goal, exprs in terms.items():  # self-check: every goal's model value equals its measurement
        in_model = sum(solver.Value(e) for e in exprs)
        if in_model != improved.breakdown[goal]:
            log.warning("%s: model says %d, measurement says %d", goal, in_model, improved.breakdown[goal])
    return improved if improved.penalty <= baseline.penalty else baseline
