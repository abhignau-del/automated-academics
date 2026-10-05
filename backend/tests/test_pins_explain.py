import pytest
from pydantic import ValidationError

from automated_academics.checks import check_institution, diagnose
from automated_academics.explain import explain_infeasible, explain_message
from automated_academics.models import (
    Batch, Calendar, Course, Faculty, Institution, Offering, Pin, Room, RoomKind, Slot,
)
from automated_academics.solver import Unsatisfiable, solve
from automated_academics.synthetic import sample_institution
from automated_academics.validate import find_conflicts


def tiny(**over) -> Institution:
    """Two days of three lectures; two classes, three teachers, two rooms. Easy to solve, easy to break."""
    data = dict(
        name="Tiny",
        calendar=Calendar(day_names=["Mon", "Tue"], lectures_per_day=3, break_after=[]),
        rooms=[Room(id="R1", name="Room 1", capacity=40), Room(id="R2", name="Room 2", capacity=40)],
        faculty=[Faculty(id=f, name=f"Teacher {f}", department="D") for f in ("F1", "F2", "F3")],
        batches=[Batch(id=b, program="P", level="UG", semester=1, section=b, department="D", strength=30)
                 for b in ("B1", "B2")],
        courses=[Course(code=c, name=f"Course {c}", department="D", credits=3) for c in ("C1", "C2", "C3")],
        offerings=[
            Offering(id="O1", course_code="C1", faculty_id="F1", batch_ids=["B1"], sessions=[1, 1]),
            Offering(id="O2", course_code="C2", faculty_id="F2", batch_ids=["B1"], sessions=[1]),
            Offering(id="O3", course_code="C3", faculty_id="F3", batch_ids=["B2"], sessions=[1, 1]),
        ],
    )
    data.update(over)
    return Institution(**data)


def two_slot_day(**over) -> Institution:
    """B1 has one lecture each of F1 and F2 in a day of two lectures."""
    return tiny(
        calendar=Calendar(day_names=["Mon"], lectures_per_day=2, break_after=[]),
        offerings=[Offering(id="O1", course_code="C1", faculty_id="F1", batch_ids=["B1"], sessions=[1]),
                   Offering(id="O2", course_code="C2", faculty_id="F2", batch_ids=["B1"], sessions=[1])],
        **over,
    )


def placed(tt, oid, idx):
    return next(p for p in tt.placements if p.offering_id == oid and p.session_index == idx)


# ---------- solver honours pins ----------

def test_pinned_time_and_room_are_respected():
    inst = tiny(pins=[Pin(offering_id="O1", session_index=1, day=1, start=2, room_id="R2")])
    tt = solve(inst, time_limit_s=5)
    p = placed(tt, "O1", 1)
    assert (p.day, p.start, p.room_id) == (1, 2, "R2")
    assert find_conflicts(inst, tt) == []


def test_pin_without_room_leaves_room_to_solver_but_fixes_time():
    inst = tiny(pins=[Pin(offering_id="O2", session_index=0, day=0, start=1)])
    p = placed(solve(inst, time_limit_s=5), "O2", 0)
    assert (p.day, p.start) == (0, 1) and p.room_id in ("R1", "R2")


def test_pin_in_a_teachers_unavailable_slot_is_unsatisfiable():
    inst = tiny(pins=[Pin(offering_id="O1", session_index=0, day=0, start=0)])
    inst.faculty[0].unavailable = [Slot(day=0, lecture=0)]
    with pytest.raises(Unsatisfiable):
        solve(inst, time_limit_s=5)


# ---------- validation of pins ----------

def test_model_rejects_pins_pointing_at_nothing():
    for bad in (Pin(offering_id="GHOST", session_index=0, day=0, start=0),
                Pin(offering_id="O1", session_index=9, day=0, start=0),
                Pin(offering_id="O1", session_index=0, day=0, start=0, room_id="NOPE")):
        with pytest.raises(ValidationError):
            tiny(pins=[bad])


def test_check_institution_locates_pin_errors():
    data = tiny().model_dump(mode="json")
    data["pins"] = [{"offering_id": "GHOST", "session_index": 0, "day": 0, "start": 0, "room_id": None},
                    {"offering_id": "O1", "session_index": 7, "day": 0, "start": 0, "room_id": None},
                    {"offering_id": "O1", "session_index": 0, "day": 0, "start": 0, "room_id": "NOPE"}]
    inst, issues = check_institution(data)
    assert inst is None
    assert sorted(i.index for i in issues if i.section == "pins") == [0, 1, 2]


def messages(inst, level="error"):
    return [d.message for d in diagnose(inst) if d.level == level]


def test_diagnose_flags_pin_outside_block_unavailable_and_wrong_room():
    assert messages(tiny(pins=[Pin(offering_id="O1", session_index=0, day=0, start=0)])) == []
    off_week = tiny(pins=[Pin(offering_id="O1", session_index=0, day=5, start=0)])
    assert any("can't go there" in m for m in messages(off_week))
    busy = tiny(pins=[Pin(offering_id="O1", session_index=0, day=0, start=1)])
    busy.faculty[0].unavailable = [Slot(day=0, lecture=1)]
    assert any("Teacher F1 is unavailable" in m for m in messages(busy))
    small = tiny(rooms=[Room(id="R1", name="Room 1", capacity=40), Room(id="R2", name="Tiny room", capacity=10)],
                 pins=[Pin(offering_id="O1", session_index=0, day=0, start=0, room_id="R2")])
    assert any("wrong kind of room or too small" in m for m in messages(small))


def test_diagnose_flags_colliding_pins():
    same_class = tiny(pins=[Pin(offering_id="O1", session_index=0, day=0, start=0),
                            Pin(offering_id="O2", session_index=0, day=0, start=0)])
    assert any("same class" in m for m in messages(same_class))
    same_teacher = tiny(pins=[Pin(offering_id="O1", session_index=0, day=0, start=0),
                              Pin(offering_id="O1", session_index=1, day=0, start=0)])
    assert any("same teacher" in m for m in messages(same_teacher))
    same_room = tiny(pins=[Pin(offering_id="O1", session_index=0, day=0, start=0, room_id="R1"),
                           Pin(offering_id="O3", session_index=0, day=0, start=0, room_id="R1")])
    assert any("same room" in m for m in messages(same_room))
    different = tiny(pins=[Pin(offering_id="O1", session_index=0, day=0, start=0, room_id="R1"),
                           Pin(offering_id="O3", session_index=0, day=0, start=0, room_id="R2")])
    assert messages(different) == []


def test_room_demand_check_counts_lab_lectures_against_lab_slots():
    inst = tiny(
        courses=[Course(code="C1", name="Lab", department="D", credits=1, room_kind=RoomKind.LAB),
                 Course(code="C2", name="Two", department="D", credits=1),
                 Course(code="C3", name="Three", department="D", credits=1)],
        rooms=[Room(id="L1", name="Lab 1", capacity=40, kind=RoomKind.LAB),
               Room(id="R1", name="Room 1", capacity=40)],
        offerings=[Offering(id="O1", course_code="C1", faculty_id="F1", batch_ids=["B1"], sessions=[1] * 4),
                   Offering(id="O2", course_code="C1", faculty_id="F2", batch_ids=["B2"], sessions=[1] * 3)],
    )  # 7 lab lectures, but one lab has only 2 days x 3 lectures = 6 slots
    assert any("can only use Lab 1" in m for m in messages(inst))
    assert not any("can only use" in m for m in messages(tiny()))


def test_sample_data_has_no_errors():
    assert messages(sample_institution()) == []


# ---------- explaining a failure ----------

def test_explanation_names_the_two_availabilities_that_collide():
    inst = two_slot_day()
    inst.faculty[0].unavailable = [Slot(day=0, lecture=1)]  # F1 can only do lecture 1
    inst.faculty[1].unavailable = [Slot(day=0, lecture=1)]  # F2 too, so both want the same slot of B1
    inst.faculty[2].unavailable = [Slot(day=0, lecture=0)]  # bystander
    inst.faculty[2].max_lectures_per_day = 1               # another bystander
    e = explain_infeasible(inst, budget_s=60, trial_s=5)
    assert not e.structural and e.complete
    assert {(i.kind, i.key) for i in e.items} == {("unavailable", "F1"), ("unavailable", "F2")}


def test_explanation_finds_a_pin_together_with_an_availability():
    inst = two_slot_day(pins=[Pin(offering_id="O1", session_index=0, day=0, start=0)])
    inst.faculty[1].unavailable = [Slot(day=0, lecture=1)]  # F2 must be at 0, which the pin has taken
    e = explain_infeasible(inst, budget_s=60, trial_s=5)
    assert {(i.kind, i.key) for i in e.items} == {("pin", "O1#0"), ("unavailable", "F2")}
    msg = explain_message(inst, "solver said no")
    assert "pinned at Mon lecture 1" in msg and "Teacher F2 is unavailable" in msg


def test_explanation_says_structural_when_nothing_user_chosen_is_to_blame():
    inst = tiny(calendar=Calendar(day_names=["Mon"], lectures_per_day=1, break_after=[]),
                offerings=[Offering(id="O1", course_code="C1", faculty_id="F1", batch_ids=["B1"], sessions=[1]),
                           Offering(id="O2", course_code="C2", faculty_id="F2", batch_ids=["B1"], sessions=[1])])
    e = explain_infeasible(inst, budget_s=30, trial_s=5)
    assert e.structural and e.items == []
    assert "even with every pin" in explain_message(inst, "solver said no")


def test_feasible_data_is_not_blamed_on_anything():
    assert explain_message(tiny(), "solver said no") == "solver said no"


def test_pins_survive_the_excel_round_trip(tmp_path):
    from automated_academics.excel_io import export_workbook, import_workbook
    inst = tiny(pins=[Pin(offering_id="O1", session_index=1, day=1, start=2, room_id="R2"),
                      Pin(offering_id="O2", session_index=0, day=0, start=1)])
    path = tmp_path / "p.xlsx"
    export_workbook(inst, path)
    back = import_workbook(path)
    back = back[0] if isinstance(back, tuple) else back
    assert back.pins == inst.pins
