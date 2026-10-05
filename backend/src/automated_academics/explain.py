"""Say *why* no timetable exists.

When the solver proves the constraints can't all hold, "failed" is no use to the person who has to
fix it. This finds a small set of the user's own constraints that cannot coexist (for example "Dr X's
unavailable slots, together with this pinned session") by relaxing them one at a time and asking the
solver whether a timetable becomes possible.

Only constraints the user chose are candidates (pins, teachers' unavailable slots, teachers' daily
limits). The basic rules (no double-booking, rooms fit) are never relaxed. If the problem is
impossible even with all candidates relaxed, it is structural, and the message says so.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .checks import diagnose
from .models import Institution, Weights
from .solver import InfeasibleError, TimedOut, Unsatisfiable, solve

log = logging.getLogger("automated_academics")

_ZERO = Weights(repeat_course_day=0, batch_gaps=0, faculty_gaps=0, peak_day_load=0, avoid_slot=0)


@dataclass(frozen=True)
class Item:
    kind: str  # pin | unavailable | limit
    key: str
    label: str


@dataclass
class Explanation:
    items: list[Item]  # constraints that cannot all hold together (empty if the problem is structural)
    structural: bool  # impossible even with every optional constraint removed
    complete: bool  # False if the time budget ran out before the set was narrowed down fully


def candidate_items(inst: Institution) -> list[Item]:
    days = inst.calendar.day_names
    offerings = {o.id: o for o in inst.offerings}
    courses = {c.code: c.name for c in inst.courses}
    items: list[Item] = []
    for p in inst.pins:
        o = offerings[p.offering_id]
        day = days[p.day] if p.day < len(days) else f"day {p.day + 1}"
        items.append(Item("pin", f"{p.offering_id}#{p.session_index}",
                          f"{courses[o.course_code]} ({o.id}) session {p.session_index + 1} is pinned at {day} lecture {p.start + 1}"))
    for f in inst.faculty:
        if f.unavailable:
            items.append(Item("unavailable", f.id, f"{f.name} is unavailable for {len(f.unavailable)} slot(s)"))
    for f in inst.faculty:
        if f.max_lectures_per_day < inst.calendar.lectures_per_day:
            items.append(Item("limit", f.id, f"{f.name} can teach at most {f.max_lectures_per_day} lecture(s) a day"))
    return items


def _without(inst: Institution, removed: set[Item]) -> Institution:
    pins_off = {i.key for i in removed if i.kind == "pin"}
    unavail_off = {i.key for i in removed if i.kind == "unavailable"}
    limit_off = {i.key for i in removed if i.kind == "limit"}
    faculty = [
        f.model_copy(update={
            "unavailable": [] if f.id in unavail_off else f.unavailable,
            "max_lectures_per_day": inst.calendar.lectures_per_day if f.id in limit_off else f.max_lectures_per_day,
        }) for f in inst.faculty
    ]
    pins = [p for p in inst.pins if f"{p.offering_id}#{p.session_index}" not in pins_off]
    return inst.model_copy(update={"faculty": faculty, "pins": pins, "weights": _ZERO})


def _feasible(inst: Institution, limit_s: float) -> bool | None:
    """True if a timetable exists, False if provably none does, None if undecided in the time given."""
    try:
        solve(inst, time_limit_s=limit_s, min_first_solution_s=limit_s, workers=8)
        return True
    except Unsatisfiable:
        return False
    except TimedOut:
        return None
    except InfeasibleError:
        return None


def explain_infeasible(inst: Institution, budget_s: float = 30.0, trial_s: float = 5.0) -> Explanation:
    """Narrow the user's constraints down to a set that can't all hold. Never raises."""
    started = time.monotonic()
    out_of_time = lambda: time.monotonic() - started > budget_s  # noqa: E731
    items = candidate_items(inst)

    if _feasible(_without(inst, set(items)), trial_s) is False:
        return Explanation([], structural=True, complete=True)

    enabled = set(items)
    complete = True
    # first drop whole groups that make no difference, then narrow within what is left
    for kind in ("pin", "unavailable", "limit"):
        group = {i for i in enabled if i.kind == kind}
        if not group:
            continue
        if out_of_time():
            complete = False
            break
        if _feasible(_without(inst, set(items) - (enabled - group)), trial_s) is False:
            enabled -= group  # still impossible without this whole group, so none of it is needed

    for item in sorted(enabled, key=lambda i: (i.kind, i.key)):
        if out_of_time():
            complete = False
            break
        trial = enabled - {item}
        if _feasible(_without(inst, set(items) - trial), trial_s) is False:
            enabled = trial  # impossible without it too, so it is not part of the clash
    return Explanation(sorted(enabled, key=lambda i: (i.kind, i.key)), structural=False, complete=complete)


def explain_message(inst: Institution, solver_message: str, budget_s: float = 30.0) -> str:
    """A plain-language account of why generating failed, ready to show to the user."""
    try:
        e = explain_infeasible(inst, budget_s)
    except Exception:  # noqa: BLE001 - an explanation must never hide the original failure
        log.exception("could not explain an infeasible timetable")
        return solver_message
    if e.structural:
        hints = [d.message for d in diagnose(inst) if d.level == "error"][:4]
        lines = ["No timetable is possible, even with every pin, availability and daily limit removed."]
        if hints:
            lines.append("The data has these problems:")
            lines += [f"  - {h}" for h in hints]
        else:
            lines.append("There are more lectures than the rooms, teachers or class slots can hold. Compare how many "
                         "lectures each class, teacher and kind of room has per week with the slots in the week.")
        return "\n".join(lines)
    if not e.items:
        return solver_message
    head = "No timetable can satisfy all of these at once:" if e.complete else \
        "These constraints are among those that can't all be satisfied (the search stopped early, so there may be fewer):"
    return "\n".join([head, *[f"  - {i.label}" for i in e.items], "Remove or change one of them and try again."])
