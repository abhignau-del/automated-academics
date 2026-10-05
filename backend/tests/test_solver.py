import pytest

from automated_academics.models import Calendar, Offering, Placement, Timetable
from automated_academics.solver import InfeasibleError, solve
from automated_academics.synthetic import sample_institution
from automated_academics.validate import find_conflicts


def test_calendar_blocks_respect_break():
    cal = Calendar(lectures_per_day=7, break_after=[3])
    assert cal.block_fits(2, 2)
    assert not cal.block_fits(3, 2)  # would span lunch
    assert cal.block_fits(4, 3)
    assert not cal.block_fits(6, 2)  # leaves the day


def test_sample_institution_solves_without_conflicts():
    inst = sample_institution()
    tt = solve(inst, time_limit_s=4)
    assert tt.status in ("OPTIMAL", "FEASIBLE")
    expected = sum(len(o.sessions) for o in inst.offerings)
    assert len(tt.placements) == expected
    assert find_conflicts(inst, tt) == []


def test_shared_elective_blocks_both_batches():
    inst = sample_institution()
    tt = solve(inst, time_limit_s=4)
    mdc = {p.offering_id: p for p in tt.placements}
    slots = [(p.day, p.start) for p in tt.placements if p.offering_id == "O-MDC101"]
    assert len(slots) == len(set(slots)) == 3
    # no other offering of either attending batch may use those slots
    for p in tt.placements:
        if p.offering_id == "O-MDC101":
            continue
        off = next(o for o in inst.offerings if o.id == p.offering_id)
        if {"CS-UG1", "MG-UG1"} & set(off.batch_ids):
            for d, s in slots:
                assert not (p.day == d and p.start <= s < p.start + p.length)
    assert mdc  # sanity


def test_lab_subgroup_never_overlaps_parent_lectures():
    inst = sample_institution()
    tt = solve(inst, time_limit_s=4)
    offs = {o.id: o for o in inst.offerings}

    def cells(p):
        return {(p.day, t) for t in range(p.start, p.start + p.length)}

    lectures = [p for p in tt.placements if offs[p.offering_id].batch_ids == ["CS-UG1"]]
    labs = [p for p in tt.placements if offs[p.offering_id].batch_ids[0].startswith("CS-UG1-")]
    assert lectures and len(labs) == 2
    for lec in lectures:
        for lab in labs:
            assert not cells(lec) & cells(lab)


def test_one_double_booking_is_reported_once_not_per_class_group():
    # CS-UG1 is split into lab groups A and B. Putting two of the section's lectures in the same
    # slot clashes the section and both groups, but it is one mistake and should read as one.
    inst = sample_institution()
    lectures = [o for o in inst.offerings if o.batch_ids == ["CS-UG1"]][:2]
    tt = Timetable(status="MANUAL", placements=[
        Placement(offering_id=lectures[0].id, session_index=0, day=0, start=0, length=1, room_id="C1"),
        Placement(offering_id=lectures[1].id, session_index=0, day=0, start=0, length=1, room_id="C2"),
    ])
    clashes = [c for c in find_conflicts(inst, tt) if c.startswith("clash B:")]
    assert len(clashes) == 1
    assert all(b in clashes[0] for b in ("CS-UG1", "CS-UG1-A", "CS-UG1-B"))


def test_validator_detects_a_clash():
    inst = sample_institution()
    tt = solve(inst, time_limit_s=4)
    broken = Timetable(placements=list(tt.placements), status="MANUAL")
    first = broken.placements[0]
    victim = next(p for p in broken.placements[1:] if p.offering_id != first.offering_id)
    broken.placements[broken.placements.index(victim)] = victim.model_copy(
        update={"day": first.day, "start": first.start, "room_id": first.room_id}
    )
    assert find_conflicts(inst, broken)


def test_oversubscribed_faculty_is_infeasible():
    inst = sample_institution()
    # 50 one-lecture sessions for one faculty member exceed 42 weekly slots
    inst.offerings.append(
        Offering(id="O-X", course_code="CS-UG1-T1", faculty_id="CSF1",
                 batch_ids=["CS-UG1"], sessions=[1] * 50)
    )
    with pytest.raises(InfeasibleError):
        solve(inst, time_limit_s=10)
