"""Soft goals: the independent measurement, and the solver actually optimising them."""

import pytest

from automated_academics.models import (
    Batch, Calendar, Course, Faculty, Institution, Level, Offering, Placement, Room, Slot,
    Timetable, Weights,
)
from automated_academics.quality import METRICS, measure, score
from automated_academics.solver import solve
from automated_academics.synthetic import sample_institution
from automated_academics.validate import find_conflicts

ZERO = Weights(repeat_course_day=0, batch_gaps=0, faculty_gaps=0, peak_day_load=0, avoid_slot=0)


def tiny(offerings, *, days=2, lectures=6, weights=ZERO, faculty=("F1", "F2", "F3", "F4"),
         batches=(("B", None),), avoid=None, free=None):
    """A small institution: plenty of rooms, so only batch/faculty/slot choices matter.

    `free` maps a faculty id to the lectures (on every day) they can teach; others are unavailable.
    Restricting this makes the gap-free layout a non-obvious one, so a goal-blind solver would not
    land on it by accident.
    """
    avoid, free = avoid or {}, free or {}
    return Institution(
        name="t",
        calendar=Calendar(day_names=[f"D{i}" for i in range(days)], lectures_per_day=lectures, break_after=[]),
        rooms=[Room(id=f"R{i}", name=f"R{i}", capacity=100) for i in range(4)],
        faculty=[Faculty(id=f, name=f, department="X", avoid=avoid.get(f, []),
                         unavailable=[Slot(day=d, lecture=t) for d in range(days) for t in range(lectures)
                                      if f in free and t not in free[f]])
                 for f in faculty],
        batches=[Batch(id=b, program="P", level=Level.UG, semester=1, department="X", strength=20,
                       group_of=parent) for b, parent in batches],
        courses=[Course(code="C", name="C", department="X", credits=1)],
        offerings=[Offering(id=i, course_code="C", faculty_id=f, batch_ids=bs, sessions=s)
                   for i, f, bs, s in offerings],
        weights=weights,
    )


def place(oid, day, start, length=1, session=0, room="R0"):
    return Placement(offering_id=oid, session_index=session, day=day, start=start, length=length, room_id=room)


def tt(*placements):
    return Timetable(placements=list(placements), status="MANUAL")


# ---------- the independent measurement ----------

def test_measure_counts_gaps_peak_and_repeats_by_hand():
    inst = tiny([("O1", "F1", ["B"], [1, 1]), ("O2", "F2", ["B"], [1]), ("O3", "F1", ["B"], [1])])
    m = measure(inst, tt(place("O1", 0, 0), place("O1", 0, 3, session=1), place("O2", 0, 1, room="R1"),
                         place("O3", 1, 5, room="R2")))
    # class day 0 has lectures 0,1,3 -> one idle lecture (2); day 1 has lecture 5 alone
    assert m["batch_gaps"] == 1
    # F1 teaches lectures 0 and 3 on day 0 -> two idle lectures; day 1 only one lecture
    assert m["faculty_gaps"] == 2
    assert m["peak_day_load"] == 3  # busiest class day has 3 lectures
    assert m["repeat_course_day"] == 1  # O1's two sessions share day 0
    assert m["avoid_slot"] == 0


def test_measure_avoid_and_weights():
    inst = tiny([("O1", "F1", ["B"], [2])], avoid={"F1": [Slot(day=0, lecture=1), Slot(day=1, lecture=0)]})
    m = measure(inst, tt(place("O1", 0, 0, length=2)))  # covers lectures 0 and 1 of day 0
    assert m["avoid_slot"] == 1
    assert score(Weights(avoid_slot=6, repeat_course_day=0, batch_gaps=0, faculty_gaps=0, peak_day_load=0), m) == 6


def test_measure_uses_leaf_batches_so_a_group_sees_its_parents_lectures():
    inst = tiny([("LEC", "F1", ["S"], [1]), ("LAB", "F2", ["S-A"], [1])],
                batches=(("S", None), ("S-A", "S"), ("S-B", "S")))
    # lecture at 0 for the whole section, group A's lab at 3: group A idles 1,2; group B only has lecture
    m = measure(inst, tt(place("LEC", 0, 0), place("LAB", 0, 3, room="R1")))
    assert m["batch_gaps"] == 2
    assert m["peak_day_load"] == 2 + 1  # A attends 2 lectures that day, B attends 1, parent not counted


def test_measure_skips_uninterpretable_placements():
    inst = tiny([("O1", "F1", ["B"], [1])])
    m = measure(inst, tt(place("GHOST", 0, 0), place("O1", 99, 0), place("O1", 0, 2)))
    assert m["peak_day_load"] == 1 and m["batch_gaps"] == 0


# ---------- the solver optimises each goal ----------

def solved(inst, limit=10):
    result = solve(inst, time_limit_s=limit)
    assert find_conflicts(inst, result) == []
    return result


# one day of 8 lectures in the three tests below
ONE_DAY = dict(days=1, lectures=8)


def class_gap_case(weights):
    # Only lectures 5-7 chain together (F1 at 5, F2 at 6, F3 at 7). Every other choice, such as
    # F1 at lecture 0, leaves the class a long idle stretch.
    offs = [("O1", "F1", ["B"], [1]), ("O2", "F2", ["B"], [1]), ("O3", "F3", ["B"], [1])]
    return tiny(offs, **ONE_DAY, weights=weights,
                free={"F1": {0, 5}, "F2": {5, 6}, "F3": {6, 7}})


def faculty_gap_case(weights):
    # F1 teaches two classes and is only free at 0, 1 and 6: the only gap-free pair is (0, 1).
    offs = [("O1", "F1", ["B"], [1]), ("O2", "F1", ["C"], [1])]
    return tiny(offs, **ONE_DAY, weights=weights, batches=(("B", None), ("C", None)),
                free={"F1": {0, 1, 6}})


def lab_block_case(weights):
    # A 2-lecture lab (free at 0-1 or 6-7) and a single lecture (free at 2 or 4).
    # Only lab 0-1 + lecture 2 is contiguous; the other combinations leave idle lectures.
    offs = [("LAB", "F1", ["B"], [2]), ("O2", "F2", ["B"], [1])]
    return tiny(offs, **ONE_DAY, weights=weights, free={"F1": {0, 1, 6, 7}, "F2": {2, 4}})


def test_solver_removes_class_gaps():
    result = solved(class_gap_case(ZERO.model_copy(update={"batch_gaps": 10})))
    assert result.breakdown["batch_gaps"] == 0
    assert sorted(p.start for p in result.placements) == [5, 6, 7]


def test_solver_removes_faculty_gaps():
    result = solved(faculty_gap_case(ZERO.model_copy(update={"faculty_gaps": 3})))
    assert result.breakdown["faculty_gaps"] == 0
    assert sorted(p.start for p in result.placements) == [0, 1]


def test_solver_spreads_load_across_days():
    offs = [(f"O{i}", f"F{i}", ["B"], [1]) for i in range(1, 5)]
    result = solved(tiny(offs, days=4, weights=ZERO.model_copy(update={"peak_day_load": 4})))
    assert result.breakdown["peak_day_load"] == 1  # one lecture a day


def test_solver_avoids_discouraged_slots_when_it_can():
    keep = (1, 3)
    avoid = [Slot(day=d, lecture=t) for d in range(2) for t in range(6) if (d, t) != keep]
    inst = tiny([("O1", "F1", ["B"], [1])], avoid={"F1": avoid},
                weights=ZERO.model_copy(update={"avoid_slot": 6}))
    result = solved(inst)
    p = result.placements[0]
    assert (p.day, p.start) == keep and result.breakdown["avoid_slot"] == 0


def test_avoid_is_soft_not_hard():
    everything = [Slot(day=d, lecture=t) for d in range(2) for t in range(6)]
    inst = tiny([("O1", "F1", ["B"], [1])], avoid={"F1": everything},
                weights=ZERO.model_copy(update={"avoid_slot": 6}))
    result = solved(inst)  # must still be placed, just not happily
    assert len(result.placements) == 1 and result.breakdown["avoid_slot"] == 1 and result.penalty == 6


def test_zero_weights_switch_goals_off_but_metrics_are_still_reported():
    offs = [(f"O{i}", f"F{i}", ["B"], [1]) for i in range(1, 4)]
    result = solved(tiny(offs))
    assert result.penalty == 0
    assert set(result.breakdown) == set(METRICS)


def test_multi_lecture_blocks_count_every_lecture_for_gaps():
    result = solved(lab_block_case(ZERO.model_copy(update={"batch_gaps": 10})))
    assert result.breakdown["batch_gaps"] == 0
    lab = next(p for p in result.placements if p.offering_id == "LAB")
    single = next(p for p in result.placements if p.offering_id == "O2")
    assert (lab.start, single.start) == (0, 2)


# ---------- on the realistic sample ----------

def test_solver_objective_matches_independent_measurement_and_beats_the_baseline():
    inst = sample_institution()
    baseline = solve(inst.model_copy(update={"weights": ZERO}), time_limit_s=5)
    better = solve(inst, time_limit_s=20)

    assert find_conflicts(inst, better) == []
    # the solver's own objective equals the independent weighted measurement
    assert better.penalty == score(inst.weights, better.breakdown) == score(inst.weights, measure(inst, better))
    # a goal-blind solve (what v0.1.0 effectively did) is far worse on the same yardstick
    base_score = score(inst.weights, measure(inst, baseline))
    assert better.penalty < base_score / 2, (better.penalty, base_score)
    assert better.breakdown["batch_gaps"] < measure(inst, baseline)["batch_gaps"]
    # the sample's "no Saturday afternoon" wish is honoured at least as well as by a goal-blind solve
    # (the strict version of this check is test_solver_avoids_discouraged_slots_when_it_can)
    assert better.breakdown["avoid_slot"] <= measure(inst, baseline)["avoid_slot"]


# ---------- robustness and scale (regressions found while building this) ----------

@pytest.mark.parametrize("limit", [1, 2])
def test_even_a_very_short_time_limit_returns_a_valid_timetable(limit):
    # The first version of the improvement stage found *no* timetable under about 5 s, because
    # the heavier model needed longer to find a first solution. Stage 1 now guarantees one.
    inst = sample_institution()
    result = solve(inst, time_limit_s=limit)
    assert find_conflicts(inst, result) == []
    assert len(result.placements) == sum(len(o.sessions) for o in inst.offerings)


def test_results_are_far_better_than_a_goal_blind_timetable_at_any_time_limit():
    # A goal-blind timetable scores about 900 on the sample. The parallel search is not
    # deterministic, so these are generous absolute bounds rather than a comparison between runs.
    inst = sample_institution()
    assert solve(inst, time_limit_s=2).penalty < 600
    assert solve(inst, time_limit_s=10).penalty < 400


def test_larger_institution_solves_and_is_valid():
    # 3 copies = about 200 sessions. Before the solver rewrite and the presolve settings, even
    # 4 copies took 45 s with every goal switched off and with the goals on found nothing in a minute.
    from automated_academics.synthetic import scaled_institution

    # (goals off, so this checks the hard rules at scale and stays quick; the goals are covered
    # on the small instance)
    inst = scaled_institution(3).model_copy(update={"weights": ZERO})
    result = solve(inst, time_limit_s=60)
    assert find_conflicts(inst, result) == []
    assert len(result.placements) == sum(len(o.sessions) for o in inst.offerings)


def test_scaled_institution_is_consistent():
    from automated_academics.synthetic import scaled_institution

    one, three = sample_institution(), scaled_institution(3)
    assert len(three.offerings) == 3 * len(one.offerings) and len(three.batches) == 3 * len(one.batches)
    assert len({o.id for o in three.offerings}) == len(three.offerings)  # unique ids
    assert scaled_institution(1).model_dump() == one.model_dump()


def test_negative_weights_are_rejected():
    with pytest.raises(ValueError):
        Weights(batch_gaps=-1)
