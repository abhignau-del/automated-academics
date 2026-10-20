import re

from automated_academics.models import (
    Batch, Calendar, Course, Faculty, Institution, LectureTime, Offering, Placement, Room, Slot, Timetable,
)
from automated_academics.validate import find_conflict_details, find_conflicts


def t(a, b):
    return LectureTime(start=a, end=b)


def world(**over):
    """Two classes (one split into lab groups), two teachers, two rooms, three lectures a day with times."""
    data = dict(
        name="W",
        calendar=Calendar(day_names=["Mon", "Tue"], lectures_per_day=3, break_after=[0],
                          times=[t("9:00", "9:50"), t("10:15", "11:05"), t("11:10", "12:00")]),
        rooms=[Room(id="R1", name="Room One", capacity=40), Room(id="R2", name="Room Two", capacity=40),
               Room(id="R3", name="Room Three", capacity=20)],
        faculty=[Faculty(id="F1", name="Prof. Rao", department="D", max_lectures_per_day=2),
                 Faculty(id="F2", name="Dr. Iyer", department="D")],
        batches=[Batch(id="B1", program="P", level="UG", semester=1, section="A", department="D", strength=30),
                 Batch(id="B1-A", program="P", level="UG", semester=1, section="A1", department="D", strength=15, group_of="B1"),
                 Batch(id="B2", program="P", level="UG", semester=1, section="B", department="D", strength=30)],
        courses=[Course(code="CAL", name="Calculus", department="D", credits=3),
                 Course(code="ALG", name="Algebra", department="D", credits=3)],
        offerings=[Offering(id="O1", course_code="CAL", faculty_id="F1", batch_ids=["B1"], sessions=[1, 1]),
                   Offering(id="O2", course_code="ALG", faculty_id="F1", batch_ids=["B2"], sessions=[1]),
                   Offering(id="O3", course_code="ALG", faculty_id="F2", batch_ids=["B1"], sessions=[1]),
                   Offering(id="O4", course_code="CAL", faculty_id="F2", batch_ids=["B1-A"], sessions=[1])],
    )
    data.update(over)
    return Institution(**data)


def pl(oid, idx, day, start, room="R1", length=1):
    return Placement(offering_id=oid, session_index=idx, day=day, start=start, length=length, room_id=room)


def full(*extra, skip=()):
    """A timetable that places every session cleanly, then the given placements replace those with the same key."""
    base = {("O1", 0): pl("O1", 0, 0, 0), ("O1", 1): pl("O1", 1, 1, 0), ("O2", 0): pl("O2", 0, 1, 1, "R2"),
            ("O3", 0): pl("O3", 0, 0, 2), ("O4", 0): pl("O4", 0, 1, 2, "R2")}
    for p in extra:
        base[(p.offering_id, p.session_index)] = p
    return Timetable(status="x", placements=[p for k, p in base.items() if k not in skip])


def msgs(tt, inst=None):
    return find_conflicts(inst or world(), tt)


def test_a_clean_timetable_has_no_messages():
    assert msgs(full()) == []


def test_a_teacher_in_two_places_is_named_with_the_day_lecture_and_time():
    out = msgs(full(pl("O2", 0, 0, 0, "R2")))  # Prof. Rao also teaches O1 on Monday lecture 1
    assert len(out) == 1
    m = out[0]
    assert "Prof. Rao is teaching two things at once on Mon, lecture 1 (9:00–9:50)" in m
    assert "Calculus for B1" in m and "Algebra for B2" in m


def test_a_class_with_two_lectures_at_once_names_the_class_and_its_groups_once():
    out = msgs(full(pl("O3", 0, 0, 0, "R2")))  # B1 has Calculus (O1) at Mon L1; O3 is also for B1, and O4 for B1-A is elsewhere
    assert len(out) == 1 and "two lectures at once on Mon, lecture 1 (9:00–9:50)" in out[0]  # reported once
    # a lab group's own lecture at the same time as its section's: only the group is double-booked
    group = [m for m in msgs(full(pl("O4", 0, 0, 0, "R2"))) if "lectures at once" in m]
    assert len(group) == 1 and group[0].startswith("B1-A has two lectures at once")
    # a clash on the section itself hits it and its groups: one message, naming them all
    section = [m for m in msgs(full(pl("O3", 0, 0, 0, "R2"))) if "lectures at once" in m]
    assert len(section) == 1 and "B1" in section[0] and "B1-A" in section[0] and " have two lectures at once" in section[0]


def test_a_room_booked_twice_is_named():
    out = msgs(full(pl("O2", 0, 0, 0, "R1")))
    assert any("Room One is booked twice on Mon, lecture 1" in m for m in out)


def test_lectures_count_from_one_and_ids_are_not_what_people_read():
    for m in msgs(full(pl("O2", 0, 0, 0, "R1"), pl("O3", 0, 1, 0))):
        assert not re.search(r"\bday \d\b", m) and "lecture 0" not in m
        assert "clash " not in m and "#" not in m


def test_a_double_lecture_names_both_lectures():
    inst = world(calendar=Calendar(day_names=["Mon", "Tue"], lectures_per_day=3, break_after=[],
                                   times=[t("9:00", "9:50"), t("10:15", "11:05"), t("11:10", "12:00")]),
                 offerings=[Offering(id="O1", course_code="CAL", faculty_id="F1", batch_ids=["B1"], sessions=[2])])
    tt = Timetable(status="x", placements=[pl("O1", 0, 0, 0, length=2)])
    assert msgs(tt, inst) == []
    bad = Timetable(status="x", placements=[pl("O1", 0, 0, 2, length=2)])
    m = msgs(bad, inst)
    assert any("a 2-lecture block can't run across a break or past the end of the day" in x and "Mon, lectures 3-4" in x for x in m)


def test_a_break_is_explained_in_plain_words():
    m = msgs(full(pl("O1", 0, 0, 0, length=2)))  # the calendar has a break after lecture 1
    assert any("run across a break" in x for x in m)


def test_unavailable_overloaded_too_small_and_unplaced_read_naturally():
    inst = world(faculty=[Faculty(id="F1", name="Prof. Rao", department="D", max_lectures_per_day=2, unavailable=[Slot(day=1, lecture=0)]),
                          Faculty(id="F2", name="Dr. Iyer", department="D")])
    m = msgs(full(), inst)
    assert any("Prof. Rao is unavailable on Tue, lecture 1 (9:00–9:50), but teaches Calculus for B1 (session 2 of 2) then." in x for x in m)
    small = msgs(full(pl("O1", 0, 0, 0, "R3")))  # Room Three seats 20 but B1 has 30
    assert any("Room Three seats 20 but 30 students attend" in x for x in small)
    missing = msgs(full(skip=[("O2", 0)]))
    assert any("Algebra for B2 has not been placed." in x for x in missing)
    heavy = msgs(full(pl("O2", 0, 1, 1, "R2"), pl("O1", 1, 1, 0), pl("O1", 0, 1, 2)))  # Prof. Rao: three lectures on Tue
    assert any("Prof. Rao has 3 lectures on Tue; the most they can teach in a day is 2." in x for x in heavy)


def test_malformed_edits_are_described_not_crashed():
    bad = Timetable(status="x", placements=[pl("GHOST", 0, 0, 0), pl("O1", 0, 99, 0), pl("O1", 0, 0, 0, "ZZ")])
    out = msgs(bad)
    assert any("an offering that doesn't exist ('GHOST')" in m for m in out)
    assert any("outside the week" in m and "Calculus for B1" in m for m in out)
    assert any("a room that doesn't exist ('ZZ')" in m for m in out)


def test_times_are_left_out_when_the_institution_has_none():
    inst = world(calendar=Calendar(day_names=["Mon", "Tue"], lectures_per_day=3, break_after=[0]))
    m = msgs(full(pl("O2", 0, 0, 0, "R2")), inst)
    assert any("on Mon, lecture 1:" in x and "(" not in x.split(":")[0] for x in m)


def test_each_conflict_says_what_kind_it_is_and_which_offerings():
    details = find_conflict_details(world(), full(pl("O2", 0, 0, 0, "R1")))
    kinds = {d.kind for d in details}
    assert {"teacher", "room"} <= kinds
    teacher = next(d for d in details if d.kind == "teacher")
    assert set(teacher.offering_ids) == {"O1", "O2"}


def test_the_time_shown_is_the_one_the_offerings_class_keeps():
    cal = Calendar(day_names=["Mon", "Tue"], lectures_per_day=3, break_after=[0],
                   times=[t("9:00", "9:50"), t("10:15", "11:05"), t("11:10", "12:00")],
                   clocks={"short": [t("9:00", "9:40"), t("10:00", "10:40"), t("10:45", "11:25")]})
    inst = world(calendar=cal)
    inst.batches = [b.model_copy(update={"clock": "short"}) if b.id == "B2" else b for b in inst.batches]
    # O2 (class B2, on the short clock) is placed first, so the clash is quoted in its times; O1 is on the default clock
    first_short = Timetable(status="x", placements=[pl("O2", 0, 0, 0, "R2"), pl("O1", 0, 0, 0, "R1")])
    clash = [m for m in msgs(first_short, inst) if "teaching two things at once" in m]
    assert len(clash) == 1 and "on Mon, lecture 1 (9:00–9:40)" in clash[0]
    first_default = Timetable(status="x", placements=[pl("O1", 0, 0, 0, "R1"), pl("O2", 0, 0, 0, "R2")])
    clash = [m for m in msgs(first_default, inst) if "teaching two things at once" in m]
    assert len(clash) == 1 and "on Mon, lecture 1 (9:00–9:50)" in clash[0]
