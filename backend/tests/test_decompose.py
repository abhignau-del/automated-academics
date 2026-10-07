import pytest

from automated_academics.decompose import partition, solve_auto, solve_decomposed
from automated_academics.models import Pin, Placement, Slot
from automated_academics.solver import Unsatisfiable, solve
from automated_academics.synthetic import sample_institution, university_institution
from automated_academics.validate import find_conflicts


def test_partition_puts_shared_classes_first_then_one_group_per_department():
    inst = university_institution(3, 4)
    groups = partition(inst)
    dept = {b.id: b.department for b in inst.batches}
    by_id = {o.id: o for o in inst.offerings}
    assert sorted(i for g in groups for i in g) == sorted(o.id for o in inst.offerings)  # nothing lost or doubled
    assert all(len({dept[b] for i in g for b in by_id[i].batch_ids}) > 1 for g in groups[:1])  # joint electives
    for g in groups[1:]:
        assert len({dept[b] for i in g for b in by_id[i].batch_ids}) == 1


def test_a_big_department_is_split_by_semester():
    inst = university_institution(1, 8)
    assert len(partition(inst, max_sessions=10_000)) == 1
    split = partition(inst, max_sessions=60)
    assert len(split) > 1
    sem = {b.id: b.semester for b in inst.batches}
    by_id = {o.id: o for o in inst.offerings}
    for g in split:
        assert len({sem[by_id[i].batch_ids[0]] for i in g}) == 1


def test_working_around_fixed_placements():
    inst = sample_institution()
    first = solve(inst, time_limit_s=4)
    keep = {o.id for o in inst.offerings[:10]}
    fixed = [p for p in first.placements if p.offering_id in keep]
    rest = solve(inst, time_limit_s=4, only={o.id for o in inst.offerings} - keep, fixed=fixed)
    assert {p.offering_id for p in rest.placements}.isdisjoint(keep)
    whole = type(first)(placements=[*fixed, *rest.placements], status="x")
    assert find_conflicts(inst, whole) == []  # the new part never clashes with what was fixed


def _crafted(teacher_block="CSF1", teacher_target="CSF1", same_class=False, target_sessions=(1, 1, 1), max_per_day=100):
    """Sample data plus a BLOCK offering (never scheduled here) and a TARGET offering (scheduled here).

    Goal weights are zero, as in the first stage of the department-by-department solve, so that only the hard
    rules decide where TARGET goes (with goals on, the goal model would also keep it off busy slots).
    """
    from automated_academics.models import Batch, Course, Offering
    from automated_academics.models import Weights
    inst = sample_institution()
    inst.weights = Weights(repeat_course_day=0, batch_gaps=0, faculty_gaps=0, peak_day_load=0, avoid_slot=0)
    inst.batches += [Batch(id="X1", program="X", level="UG", semester=1, department="CS", strength=10),
                     Batch(id="X2", program="X", level="UG", semester=1, department="CS", strength=10)]
    inst.courses.append(Course(code="CX", name="Crafted", department="CS", credits=1))
    inst.faculty = [f.model_copy(update={"max_lectures_per_day": max_per_day}) for f in inst.faculty]
    inst.offerings += [
        Offering(id="BLOCK", course_code="CX", faculty_id=teacher_block, batch_ids=["X1"], sessions=[1] * 45),
        Offering(id="TARGET", course_code="CX", faculty_id=teacher_target, batch_ids=["X1" if same_class else "X2"],
                 sessions=list(target_sessions)),
    ]
    return inst


def _block_all_but(free):
    """Fixed placements for BLOCK covering every slot of the 6 x 7 week except those in `free`."""
    cells = [(d, t) for d in range(6) for t in range(7) if (d, t) not in free]
    return [Placement(offering_id="BLOCK", session_index=k, day=d, start=t, length=1, room_id="C1")
            for k, (d, t) in enumerate(cells)]


def test_a_fixed_teacher_slot_is_really_busy():
    free = {(0, 2), (2, 4), (4, 1)}
    inst = _crafted()  # BLOCK and TARGET share a teacher but not a class
    rest = solve(inst, time_limit_s=4, only={"TARGET"}, fixed=_block_all_but(free))
    assert {(p.day, p.start) for p in rest.placements} == free


def test_a_fixed_class_slot_is_really_busy():
    free = {(1, 0), (3, 5), (5, 2)}
    inst = _crafted(teacher_block="CSF2", teacher_target="CSF3", same_class=True)  # same class, different teachers
    rest = solve(inst, time_limit_s=4, only={"TARGET"}, fixed=_block_all_but(free))
    assert {(p.day, p.start) for p in rest.placements} == free


def test_fixed_daily_load_counts_against_the_teachers_limit():
    inst = _crafted(target_sessions=(1,), max_per_day=1)
    fixed = [Placement(offering_id="BLOCK", session_index=k, day=d, start=0, length=1, room_id="C1")
             for k, d in enumerate([0, 1, 3, 4, 5])]  # the teacher's one lecture is used on every day but Wednesday
    rest = solve(inst, time_limit_s=4, only={"TARGET"}, fixed=fixed)
    assert [p.day for p in rest.placements] == [2]


def test_decomposed_solution_is_complete_and_clash_free():
    inst = university_institution(3, 4)
    tt = solve_decomposed(inst, time_limit_s=30)
    assert len(tt.placements) == sum(len(o.sessions) for o in inst.offerings)
    assert find_conflicts(inst, tt) == []
    assert tt.penalty == pytest.approx(tt.penalty) and set(tt.breakdown)  # measured over the whole timetable


def test_decomposed_respects_pins():
    inst = university_institution(2, 4)
    target = inst.offerings[5]
    inst.pins = [Pin(offering_id=target.id, session_index=0, day=3, start=2, room_id=None)]
    tt = solve_decomposed(inst, time_limit_s=30)
    p = next(p for p in tt.placements if p.offering_id == target.id and p.session_index == 0)
    assert (p.day, p.start) == (3, 2)


def test_an_unsatisfiable_group_is_reported_not_swallowed():
    inst = university_institution(2, 4)
    victim = inst.offerings[3]
    f = next(f for f in inst.faculty if f.id == victim.faculty_id)
    f.unavailable = [Slot(day=d, lecture=t) for d in range(6) for t in range(7)]  # never free
    with pytest.raises(Unsatisfiable):
        solve_decomposed(inst, time_limit_s=20)


def test_small_institutions_use_the_ordinary_solver(monkeypatch):
    called = []
    monkeypatch.setattr("automated_academics.decompose.solve_decomposed", lambda *a, **k: called.append(1))
    tt = solve_auto(sample_institution(), time_limit_s=4)
    assert not called and len(tt.placements) == 68


def test_large_institutions_are_split(monkeypatch):
    called = []
    sentinel = object()
    monkeypatch.setattr("automated_academics.decompose.solve_decomposed", lambda *a, **k: called.append(1) or sentinel)
    assert solve_auto(university_institution(4, 8), time_limit_s=4) is sentinel and called
