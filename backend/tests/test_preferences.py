"""Room-stability and early/late-lecture goals: the measurement, the solver optimising them, and the workbook."""

import io

from automated_academics.excel_io import export_workbook, import_workbook
from automated_academics.models import Calendar, Course, Pin, RoomKind, Weights
from automated_academics.quality import METRICS, measure
from automated_academics.solver import solve
from automated_academics.synthetic import sample_institution
from test_quality import ZERO, place, tiny, tt


def with_course_kinds(inst, kind_of_c=RoomKind.CLASSROOM):
    return inst.model_copy(update={"courses": [Course(code="C", name="C", department="X", credits=1, room_kind=kind_of_c)]})


# ---------- the measurement ----------

def test_room_changes_counts_classrooms_beyond_the_first_per_class():
    inst = tiny([("O1", "F1", ["B"], [1, 1, 1])])
    m = measure(inst, tt(place("O1", 0, 1, room="R0"), place("O1", 0, 2, session=1, room="R1"),
                         place("O1", 1, 1, session=2, room="R2")))
    assert m["room_changes"] == 2  # three rooms = two beyond the first
    stay = measure(inst, tt(place("O1", 0, 1), place("O1", 0, 2, session=1), place("O1", 1, 1, session=2)))
    assert stay["room_changes"] == 0


def test_labs_and_halls_do_not_count_as_changing_room():
    inst = with_course_kinds(tiny([("O1", "F1", ["B"], [1, 1])]), RoomKind.LAB)
    assert measure(inst, tt(place("O1", 0, 1, room="R0"), place("O1", 0, 2, session=1, room="R1")))["room_changes"] == 0


def test_a_joint_class_counts_for_every_class_that_attends():
    inst = tiny([("J", "F1", ["B1", "B2"], [1]), ("A", "F2", ["B1"], [1]), ("C", "F3", ["B2"], [1])],
                batches=(("B1", None), ("B2", None)))
    m = measure(inst, tt(place("J", 0, 1, room="R0"), place("A", 0, 2, room="R1"), place("C", 0, 3, room="R0")))
    assert m["room_changes"] == 1  # B1 uses R0 and R1; B2 only R0


def test_odd_hours_counts_class_lectures_in_the_first_and_last_slots():
    inst = tiny([("O1", "F1", ["B"], [1, 2, 1])], lectures=6)  # the first and the last lecture of a day are odd by default
    m = measure(inst, tt(place("O1", 0, 0), place("O1", 0, 4, length=2, session=1, room="R1"), place("O1", 1, 2, session=2)))
    assert m["odd_hours"] == 2  # lecture 0, and lecture 5 (the second half of the block at 4-5)
    wide = inst.model_copy(update={"calendar": inst.calendar.model_copy(update={"early_count": 0, "late_count": 3})})
    assert measure(wide, tt(place("O1", 0, 0), place("O1", 0, 3, session=1, length=2, room="R1"), place("O1", 1, 2, session=2)))["odd_hours"] == 2


def test_which_lectures_are_early_and_late():
    assert Calendar(lectures_per_day=6).odd_lectures() == {0, 5}
    assert Calendar(lectures_per_day=6, early_count=2, late_count=1).odd_lectures() == {0, 1, 5}
    assert Calendar(lectures_per_day=6, early_count=0, late_count=0).odd_lectures() == set()
    assert Calendar(lectures_per_day=2, early_count=5, late_count=5).odd_lectures() == {0, 1}


def test_both_are_off_by_default_and_listed_as_metrics():
    w = Weights()
    assert w.room_changes == 0 and w.odd_hours == 0
    assert {"room_changes", "odd_hours"} <= set(METRICS)


# ---------- the solver ----------

def test_the_solver_keeps_a_class_in_few_rooms_when_asked():
    inst = tiny([("P0", "F1", ["B"], [1]), ("P1", "F2", ["B"], [1]), ("O1", "F3", ["B"], [1, 1, 1, 1])],
                days=3, weights=Weights(**{**ZERO.model_dump(), "room_changes": 5}))
    inst = inst.model_copy(update={"pins": [Pin(offering_id="P0", session_index=0, day=0, start=0, room_id="R0"),
                                            Pin(offering_id="P1", session_index=0, day=0, start=1, room_id="R1")]})
    # start from a layout that scatters the free sessions over R2 and R3; the goal must pull them back to R0/R1
    scattered = [place("P0", 0, 0, room="R0"), place("P1", 0, 1, room="R1"),
                 place("O1", 1, 0, session=0, room="R2"), place("O1", 1, 1, session=1, room="R3"),
                 place("O1", 2, 0, session=2, room="R2"), place("O1", 2, 1, session=3, room="R3")]
    assert measure(inst, tt(*scattered))["room_changes"] == 3
    out = solve(inst, time_limit_s=10, workers=1, hint=scattered, min_first_solution_s=5)
    assert out.breakdown["room_changes"] == 1  # the two pinned rooms force one change; nothing more
    assert {p.room_id for p in out.placements} == {"R0", "R1"}


def test_the_solver_avoids_early_and_late_lectures_when_asked():
    inst = tiny([("O1", "F1", ["B"], [1, 1, 1, 1])], days=2, lectures=6,
                weights=Weights(**{**ZERO.model_dump(), "odd_hours": 4}))
    odd_start = [place("O1", 0, 0, session=0), place("O1", 0, 5, session=1), place("O1", 1, 0, session=2), place("O1", 1, 5, session=3)]
    assert measure(inst, tt(*odd_start))["odd_hours"] == 4
    out = solve(inst, time_limit_s=10, workers=1, hint=odd_start, min_first_solution_s=5)
    assert out.breakdown["odd_hours"] == 0
    assert all(1 <= p.start <= 4 for p in out.placements)


def test_the_model_and_the_measurement_agree_on_the_sample():
    base = sample_institution()
    inst = base.model_copy(update={"weights": base.weights.model_copy(update={"room_changes": 3, "odd_hours": 2})})
    out = solve(inst, time_limit_s=20, workers=2)
    assert out.breakdown["room_changes"] == measure(inst, out)["room_changes"]
    plain = solve(base, time_limit_s=20, workers=2)
    assert out.breakdown["odd_hours"] <= plain.breakdown["odd_hours"] + 5  # asked for, so never much worse than not asked


# ---------- the workbook ----------

def test_weights_and_the_early_late_counts_survive_the_workbook():
    base = sample_institution()
    inst = base.model_copy(update={
        "weights": base.weights.model_copy(update={"room_changes": 7, "odd_hours": 9}),
        "calendar": base.calendar.model_copy(update={"early_count": 2, "late_count": 0}),
    })
    buf = io.BytesIO()
    export_workbook(inst, buf)
    buf.seek(0)
    back = import_workbook(buf)
    assert (back.weights.room_changes, back.weights.odd_hours) == (7, 9)
    assert (back.calendar.early_count, back.calendar.late_count) == (2, 0)


def test_the_solver_leaves_lab_sessions_alone():
    inst = with_course_kinds(tiny([("O1", "F1", ["B"], [1, 1, 1])], days=3, weights=Weights(**{**ZERO.model_dump(), "room_changes": 5})),
                             RoomKind.LAB)
    inst = inst.model_copy(update={"rooms": [r.model_copy(update={"kind": RoomKind.LAB}) for r in inst.rooms]})
    scattered = [place("O1", 0, 1, session=0, room="R0"), place("O1", 1, 1, session=1, room="R1"), place("O1", 2, 1, session=2, room="R2")]
    out = solve(inst, time_limit_s=10, workers=1, hint=scattered, min_first_solution_s=5)
    assert out.breakdown["room_changes"] == 0  # labs never count
    assert len({p.room_id for p in out.placements}) == 3  # and nothing pulled them into one room
