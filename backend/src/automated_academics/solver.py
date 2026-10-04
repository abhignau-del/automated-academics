"""CP-SAT timetable solver.

Hard constraints
  * every session is placed exactly once
  * a block never spans a break and never leaves the day
  * no faculty, batch or room is double-booked
  * faculty unavailability and max periods per day are respected
  * room kind matches the course and capacity covers all attending batches

Soft constraints (minimised)
  * more than one session of the same offering on one day
"""

from __future__ import annotations

from collections import defaultdict

from ortools.sat.python import cp_model

from .models import Institution, Placement, Room, RoomKind, Timetable


class InfeasibleError(RuntimeError):
    """Raised when a session has no possible room or the model has no solution."""


def _room_ok(room: Room, needed: RoomKind, strength: int) -> bool:
    if room.capacity < strength:
        return False
    if needed is RoomKind.LAB:
        return room.kind is RoomKind.LAB
    if needed is RoomKind.HALL:
        return room.kind is RoomKind.HALL
    return room.kind in (RoomKind.CLASSROOM, RoomKind.HALL)


def solve(inst: Institution, time_limit_s: float = 30.0, workers: int = 8) -> Timetable:
    cal = inst.calendar
    courses = {c.code: c for c in inst.courses}
    batches = {b.id: b for b in inst.batches}
    faculty = {f.id: f for f in inst.faculty}

    model = cp_model.CpModel()
    # (offering, session) -> list of (var, day, start, length, room_id)
    options: dict[tuple[str, int], list[tuple[cp_model.IntVar, int, int, int, str]]] = {}
    occupancy: dict[tuple[str, str, int, int], list[cp_model.IntVar]] = defaultdict(list)
    fac_day_load: dict[tuple[str, int], list[tuple[cp_model.IntVar, int]]] = defaultdict(list)
    per_day: dict[tuple[str, int], list[cp_model.IntVar]] = defaultdict(list)

    for off in inst.offerings:
        course = courses[off.course_code]
        strength = sum(batches[b].strength for b in off.batch_ids)
        rooms = [r for r in inst.rooms if _room_ok(r, course.room_kind, strength)]
        fac = faculty[off.faculty_id]
        blocked = {(s.day, s.period) for s in fac.unavailable}
        attending = inst.occupied_batches(off.batch_ids)

        for i, length in enumerate(off.sessions):
            if not rooms:
                raise InfeasibleError(
                    f"{off.id} session {i}: no {course.room_kind.value} with capacity >= {strength}"
                )
            opts = []
            for d in range(cal.days):
                for p in range(cal.periods_per_day):
                    if not cal.block_fits(p, length):
                        continue
                    covered = range(p, p + length)
                    if any((d, t) in blocked for t in covered):
                        continue
                    for r in rooms:
                        v = model.NewBoolVar(f"x_{off.id}_{i}_{d}_{p}_{r.id}")
                        opts.append((v, d, p, length, r.id))
                        for t in covered:
                            occupancy[("F", off.faculty_id, d, t)].append(v)
                            occupancy[("R", r.id, d, t)].append(v)
                            for b in attending:
                                occupancy[("B", b, d, t)].append(v)
                        fac_day_load[(off.faculty_id, d)].append((v, length))
                        per_day[(off.id, d)].append(v)
            if not opts:
                raise InfeasibleError(f"{off.id} session {i}: no admissible slot")
            model.AddExactlyOne(v for v, *_ in opts)
            options[(off.id, i)] = opts

    for lits in occupancy.values():
        if len(lits) > 1:
            model.AddAtMostOne(lits)

    for (fid, _d), items in fac_day_load.items():
        model.Add(sum(v * ln for v, ln in items) <= faculty[fid].max_periods_per_day)

    penalties = []
    for (oid, d), lits in per_day.items():
        if len(lits) > 1:
            ex = model.NewIntVar(0, len(lits), f"ex_{oid}_{d}")
            model.Add(ex >= sum(lits) - 1)
            penalties.append(ex)
    model.Minimize(sum(penalties))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_workers = workers
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise InfeasibleError(f"no timetable found ({solver.StatusName(status)})")

    placements = []
    for (oid, i), opts in options.items():
        for v, d, p, ln, rid in opts:
            if solver.Value(v):
                placements.append(
                    Placement(offering_id=oid, session_index=i, day=d, start=p, length=ln, room_id=rid)
                )
                break
    return Timetable(
        placements=placements,
        status=solver.StatusName(status),
        penalty=int(solver.ObjectiveValue()),
    )
