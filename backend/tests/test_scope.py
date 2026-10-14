from automated_academics.models import Pin, Placement, Slot, Timetable
from automated_academics.scope import institution_violations, timetable_violations
from automated_academics.synthetic import sample_institution


def inst():
    return sample_institution()


def changed(new, old=None, depts=("CS",)):
    return institution_violations(old or inst(), new, set(depts))


# ---------- nothing changed ----------

def test_no_change_is_no_violation():
    assert changed(inst()) == []


# ---------- one's own department ----------

def test_editing_your_own_teachers_classes_courses_and_offerings_is_fine():
    new = inst()
    new.faculty = [f.model_copy(update={"name": "Renamed", "unavailable": [Slot(day=0, lecture=1)]}) if f.id == "CSF1" else f for f in new.faculty]
    new.batches = [b.model_copy(update={"strength": 99}) if b.id == "CS-UG1" else b for b in new.batches]
    new.courses = [c.model_copy(update={"credits": 5}) if c.code == "CS-UG1-T1" else c for c in new.courses]
    new.offerings = [o.model_copy(update={"sessions": [1, 1]}) if o.id == "O-CS-UG1-T1" else o for o in new.offerings]
    assert changed(new) == []


def test_adding_and_deleting_your_own_rows_is_fine():
    new = inst()
    new.faculty = [f for f in new.faculty if f.id != "CSF4"] + [new.faculty[0].model_copy(update={"id": "CSF9", "name": "New"})]
    assert changed(new) == []


# ---------- other departments ----------

def test_another_departments_rows_are_off_limits():
    new = inst()
    new.faculty = [f.model_copy(update={"name": "Hacked"}) if f.id == "MGF1" else f for f in new.faculty]
    v = changed(new)
    assert len(v) == 1 and "teacher 'MGF1' belongs to MG" in v[0]


def test_moving_a_row_into_or_out_of_your_department_is_a_change_in_both():
    new = inst()
    new.faculty = [f.model_copy(update={"department": "MG"}) if f.id == "CSF1" else f for f in new.faculty]  # giving yours away
    assert any("CSF1" in v and "MG" in v for v in changed(new))
    new = inst()
    new.faculty = [f.model_copy(update={"department": "CS"}) if f.id == "MGF1" else f for f in new.faculty]  # taking someone else's
    assert any("MGF1" in v for v in changed(new))


def test_a_joint_offering_needs_every_department_involved():
    old = inst()
    assert any(o.id == "O-MDC101" and set(o.batch_ids) == {"CS-UG1", "MG-UG1"} for o in old.offerings)
    new = inst()
    new.offerings = [o.model_copy(update={"sessions": [1, 1]}) if o.id == "O-MDC101" else o for o in new.offerings]
    v = changed(new)
    assert len(v) == 1 and "O-MDC101" in v[0] and "MG" in v[0]
    assert changed(new, depts=("CS", "MG")) == []  # someone scoped to both may


def test_you_cannot_attach_your_offering_to_another_departments_class():
    new = inst()
    new.offerings = [o.model_copy(update={"batch_ids": ["CS-UG1", "MG-UG1"]}) if o.id == "O-CS-UG1-T1" else o for o in new.offerings]
    assert any("O-CS-UG1-T1" in v and "MG" in v for v in changed(new))


# ---------- shared things ----------

def test_rooms_and_settings_are_for_the_whole_institution():
    new = inst()
    new.rooms = new.rooms[:-1]
    assert any("rooms" in v for v in changed(new))
    new = inst()
    new.calendar = new.calendar.model_copy(update={"lectures_per_day": 6})
    assert any("shared settings" in v for v in changed(new))
    new = inst()
    new.name = "Another name"
    assert any("shared settings" in v for v in changed(new))
    new = inst()
    new.weights = new.weights.model_copy(update={"batch_gaps": 0})
    assert any("shared settings" in v for v in changed(new))


# ---------- locking ----------

def test_locking_follows_the_offering():
    new = inst()
    new.pins = [Pin(offering_id="O-CS-UG1-T1", session_index=0, day=0, start=0, room_id=None)]
    assert changed(new) == []
    new.pins = [Pin(offering_id="O-MG-UG1-T1", session_index=0, day=0, start=0, room_id=None)]
    assert any("locked session" in v and "MG" in v for v in changed(new))


# ---------- the timetable ----------

def test_only_sessions_of_your_offerings_may_move():
    base = inst()
    old = Timetable(status="x", placements=[
        Placement(offering_id="O-CS-UG1-T1", session_index=0, day=0, start=0, length=1, room_id="C1"),
        Placement(offering_id="O-MG-UG1-T1", session_index=0, day=0, start=1, length=1, room_id="C2"),
    ])
    mine = Timetable(status="x", placements=[old.placements[0].model_copy(update={"day": 3}), old.placements[1]])
    assert timetable_violations(base, old, mine, {"CS"}) == []
    theirs = Timetable(status="x", placements=[old.placements[0], old.placements[1].model_copy(update={"day": 3})])
    v = timetable_violations(base, old, theirs, {"CS"})
    assert len(v) == 1 and "O-MG-UG1-T1" in v[0]
    removed = Timetable(status="x", placements=[old.placements[0]])
    assert timetable_violations(base, old, removed, {"CS"})  # dropping another department's session counts
    assert timetable_violations(base, None, old, {"CS", "MG"}) == []
