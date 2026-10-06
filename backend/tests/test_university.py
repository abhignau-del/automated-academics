from automated_academics.checks import diagnose
from automated_academics.solver import solve
from automated_academics.synthetic import university_institution
from automated_academics.validate import find_conflicts


def test_university_is_deterministic_and_consistent():
    a, b = university_institution(4, 8, seed=3), university_institution(4, 8, seed=3)
    assert a.model_dump() == b.model_dump()
    assert university_institution(4, 8, seed=4).model_dump() != a.model_dump()
    assert [d for d in diagnose(a) if d.level == "error"] == []
    assert sum(len(o.sessions) for o in a.offerings) > 600


def test_departments_are_coupled():
    inst = university_institution(6, 8)
    dept = {b.id: b.department for b in inst.batches}
    fac = {f.id: f.department for f in inst.faculty}
    # joint electives bring classes of several departments into one offering
    assert any(len({dept[x] for x in o.batch_ids}) > 1 for o in inst.offerings)
    # some teachers teach for another department
    assert any(fac[o.faculty_id] != dept[o.batch_ids[0]] for o in inst.offerings if len(o.batch_ids) == 1)
    # and there is one pool of rooms, not one per department
    assert len(inst.rooms) < len(inst.batches)


def test_a_small_university_solves_cleanly():
    inst = university_institution(1, 4)
    tt = solve(inst, time_limit_s=20)
    assert len(tt.placements) == sum(len(o.sessions) for o in inst.offerings)
    assert find_conflicts(inst, tt) == []
