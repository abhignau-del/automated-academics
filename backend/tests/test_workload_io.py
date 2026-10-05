"""Importing a flat workload list. All data here is fictional."""

import io

import pytest
from openpyxl import Workbook

from automated_academics.checks import check_institution
from automated_academics.solver import solve
from automated_academics.validate import find_conflicts
from automated_academics.workload_io import WorkloadError, WorkloadOptions, import_workload

HEAD = ["No", "Subject", "Faculty Name", "Course", "Sem", "Division", "Credit", "Actual Load", "load Per week",
        "Total Students", "Delivery Mode", "Slots"]


def sheet(rows, head=HEAD, title_rows=0, name="Consolidated"):
    wb = Workbook()
    ws = wb.active
    ws.title = name
    for _ in range(title_rows):
        ws.append(["University timetable workload"])
    ws.append(head)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def row(n, subject, teacher, program, load, students=30, sem=1, div=1, actual=None, credit=2):
    return [n, subject, teacher, program, sem, div, credit, actual if actual is not None else load, load, students, "Offline", None]


BASIC = [
    row(1, "Applied Mathematics", "Dr Asha Rao", "BAA", 3, 32),
    row(2, "Applied Mathematics", "Dr Asha Rao", "BBB", 2, 11),
    row(3, "Digital Literacy", "Ravi Iyer", "BAA", 2, 32),
    row(4, "Financial Accounting", "Meera Shah", "BAA", 4, 32),
    row(5, "Financial Accounting", "Meera Shah", "BCC", 4, 64),
]


def test_reads_the_sheet_into_a_valid_institution():
    inst, rep = import_workload(sheet(BASIC))
    assert (rep.rows_read, rep.rows_used) == (5, 5)
    assert [b.id for b in inst.batches] == ["BAA-S1", "BBB-S1", "BCC-S1"]
    assert {b.id: b.strength for b in inst.batches} == {"BAA-S1": 32, "BBB-S1": 11, "BCC-S1": 64}
    assert len(inst.offerings) == 5
    am = next(o for o in inst.offerings if o.batch_ids == ["BAA-S1"] and inst.courses[0].code == o.course_code)
    assert am.sessions == [1, 1, 1]  # three one-lecture sessions
    inst2, issues = check_institution(inst.model_dump(mode="json"))
    assert issues == [] and inst2 is not None


def test_ids_are_unique_and_readable():
    inst, _ = import_workload(sheet(BASIC))
    assert len({c.code for c in inst.courses}) == len(inst.courses) == 3
    assert {f.id for f in inst.faculty} == {"AR", "RI", "MS"}  # initials, titles ignored
    assert len({o.id for o in inst.offerings}) == len(inst.offerings)


def test_two_teachers_with_the_same_initials_get_different_ids():
    rows = [row(1, "Maths", "Asha Rao", "BAA", 2), row(2, "Physics", "Anil Rathi", "BAA", 2)]
    inst, _ = import_workload(sheet(rows))
    assert sorted(f.id for f in inst.faculty) == ["AR", "AR2"]


def test_finds_the_heading_row_below_title_rows_and_in_any_column_order():
    reordered = ["Faculty", "Programme", "Subject", "Hours per week", "Students"]
    rows = [["Dr Asha Rao", "BAA", "Maths", 3, 40]]
    inst, rep = import_workload(sheet(rows, head=reordered, title_rows=3))
    assert inst.offerings[0].sessions == [1, 1, 1] and inst.batches[0].strength == 40
    assert "heading on row 4" in rep.assumptions[0]


def test_prefers_load_per_week_over_actual_load():
    inst, _ = import_workload(sheet([row(1, "Maths", "Asha Rao", "BAA", load=4, actual=2)]))
    assert inst.offerings[0].sessions == [1, 1, 1, 1]


def test_empty_numbered_rows_are_ignored_and_bad_rows_are_reported():
    rows = BASIC + [
        [6, None, None, None, None, None, None, None, None, None, None, None],  # numbered but empty
        row(7, "Chemistry", "", "BAA", 2),  # no teacher
        row(8, "Biology", "Ravi Iyer", "BAA", 0),  # no hours
        row(9, "", "Ravi Iyer", "BAA", 2),  # no subject
    ]
    inst, rep = import_workload(sheet(rows))
    assert rep.rows_used == 5 and len(inst.offerings) == 5
    text = " | ".join(rep.problems)
    assert "row 8" in text and "no faculty" in text
    assert "row 9" in text and "no hours per week" in text
    assert "row 10" in text and "no subject" in text
    assert "row 7" not in text  # the empty numbered row says nothing


def test_missing_student_counts_are_assumed_and_reported():
    rows = [["Maths", "Asha Rao", "BAA", 3]]
    inst, rep = import_workload(sheet(rows, head=["Subject", "Faculty", "Programme", "Load"]), WorkloadOptions(default_students=45))
    assert inst.batches[0].strength == 45
    assert any("assumed 45" in p for p in rep.problems)


def test_divisions_appear_in_the_class_id_only_when_a_programme_has_several():
    rows = [row(1, "Maths", "Asha Rao", "BAA", 2, div="A"), row(2, "Maths", "Asha Rao", "BAA", 2, div="B"),
            row(3, "Maths", "Asha Rao", "BBB", 2, div="A")]
    inst, _ = import_workload(sheet(rows))
    assert sorted(b.id for b in inst.batches) == ["BAA-S1-A", "BAA-S1-B", "BBB-S1"]


def test_pg_programmes_are_recognised():
    inst, _ = import_workload(sheet([row(1, "Finance", "Asha Rao", "MBA", 3), row(2, "Maths", "Asha Rao", "BBA", 3)]))
    levels = {b.program: b.level.value for b in inst.batches}
    assert levels == {"MBA": "PG", "BBA": "UG"}


# ---------- joint teaching

def joint(rows, limit, **kw):
    return import_workload(sheet(rows), WorkloadOptions(joint_max_students=limit, **kw))


def test_joint_teaching_is_off_by_default():
    inst, rep = import_workload(sheet(BASIC))
    assert rep.joint == [] and all(len(o.batch_ids) == 1 for o in inst.offerings)


def test_same_teacher_and_subject_are_combined_up_to_the_size_limit():
    inst, rep = joint(BASIC, 70)
    am = next(o for o in inst.offerings if len(o.batch_ids) > 1 and "BAA-S1" in o.batch_ids)
    assert am.batch_ids == ["BAA-S1", "BBB-S1"]  # 32 + 11 = 43
    fa = [o for o in inst.offerings if o.course_code == next(c.code for c in inst.courses if c.name == "Financial Accounting")]
    assert [o.batch_ids for o in fa] == [["BAA-S1"], ["BCC-S1"]]  # 32 + 64 = 96 > 70, so kept apart
    assert len(rep.joint) == 1 and "43 students" in rep.joint[0]


def test_combining_fills_groups_in_order_and_starts_a_new_one_when_full():
    rows = [row(i, "Maths", "Asha Rao", p, 2, students=s) for i, (p, s) in enumerate([("P1", 30), ("P2", 30), ("P3", 30), ("P4", 30)], 1)]
    inst, _ = joint(rows, 70)
    assert [o.batch_ids for o in inst.offerings] == [["P1-S1", "P2-S1"], ["P3-S1", "P4-S1"]]


def test_combined_classes_with_different_hours_use_the_larger_and_say_so():
    inst, rep = joint(BASIC, 70)
    am = next(o for o in inst.offerings if len(o.batch_ids) > 1)
    assert am.sessions == [1, 1, 1]  # 3 hours for BAA, 2 for BBB: the larger wins
    assert any("hours differ" in p and "BAA-S1 3" in p and "BBB-S1 2" in p for p in rep.problems)


def test_different_teachers_or_semesters_are_never_combined():
    rows = [row(1, "Maths", "Asha Rao", "P1", 2), row(2, "Maths", "Ravi Iyer", "P2", 2),
            row(3, "Maths", "Asha Rao", "P3", 2, sem=3)]
    inst, rep = joint(rows, 500)
    assert all(len(o.batch_ids) == 1 for o in inst.offerings) and rep.joint == []


def test_the_same_programme_twice_is_not_a_joint_class():
    rows = [row(1, "Maths", "Asha Rao", "P1", 2, div="A"), row(2, "Maths", "Asha Rao", "P1", 2, div="B")]
    inst, _ = joint(rows, 500)  # two divisions of one programme: not "several programmes"
    assert [len(o.batch_ids) for o in inst.offerings] == [1, 1]


# ---------- rooms and calendar assumptions

def test_rooms_are_sized_for_the_largest_class_that_sits_together():
    inst, rep = joint(BASIC, 70)
    assert {r.capacity for r in inst.rooms} == {70}  # BCC has 64 students: rounded up to 70
    assert len(inst.rooms) == 3  # one per class by default
    assert any("3 classroom(s) of 70 seats" in a for a in rep.assumptions)


def test_options_set_the_rooms_and_week():
    inst, _ = import_workload(sheet(BASIC), WorkloadOptions(classrooms=2, seats=80, labs=1, lab_seats=40,
                                                            days=["Mon", "Tue"], lectures_per_day=5, break_after=[3]))
    assert [(r.id, r.capacity, r.kind.value) for r in inst.rooms] == [("R1", 80, "classroom"), ("R2", 80, "classroom"), ("L1", 40, "lab")]
    assert inst.calendar.day_names == ["Mon", "Tue"] and inst.calendar.lectures_per_day == 5 and inst.calendar.break_after == [2]


def test_every_assumption_is_stated():
    _, rep = import_workload(sheet(BASIC))
    text = " ".join(rep.assumptions)
    for needle in ("available at all times", "no rooms", "one lecture long", "PG", "days of"):
        assert needle in text, needle


# ---------- it all works end to end

def test_the_result_can_be_scheduled_without_clashes():
    inst, _ = joint(BASIC, 70, classrooms=3)
    tt = solve(inst, time_limit_s=10)
    assert find_conflicts(inst, tt) == []
    assert len(tt.placements) == sum(len(o.sessions) for o in inst.offerings)


# ---------- the endpoint

def test_endpoint_returns_a_draft_and_a_report_without_saving_anything(tmp_path):
    import json

    from fastapi.testclient import TestClient

    from automated_academics.api import create_app

    with TestClient(create_app(str(tmp_path / "w.db"))) as c:
        files = {"file": ("w.xlsx", sheet(BASIC).getvalue())}
        r = c.post("/institutions/import-workload", files=files,
                   data={"options": json.dumps({"joint_max_students": 70, "classrooms": 3})})
        assert r.status_code == 200
        body = r.json()
        assert body["report"]["rows_used"] == 5 and len(body["report"]["joint"]) == 1
        assert len(body["institution"]["offerings"]) == 4  # the two Applied Mathematics rows became one joint offering
        assert not any(i["level"] == "error" for i in body["issues"])  # the draft needs no repair before it can be used
        assert c.get("/institutions").json() == []  # a preview: nothing was saved

        created = c.post("/institutions", json=body["institution"])  # the draft is accepted as it is
        assert created.status_code == 201

        # a bad file, a bad sheet name and bad options say so clearly
        assert c.post("/institutions/import-workload", files={"file": ("x.xlsx", b"not a workbook")}).status_code == 400
        junk = c.post("/institutions/import-workload", files={"file": ("w.xlsx", sheet([[1, 2, 3]], head=["a", "b", "c"]).getvalue())})
        assert junk.status_code == 422 and "heading row" in junk.json()["detail"]
        bad = c.post("/institutions/import-workload", files=files, data={"options": json.dumps({"lectures_per_day": 99})})
        assert bad.status_code == 422


def test_unreadable_sheets_say_why():
    with pytest.raises(WorkloadError, match="heading row"):
        import_workload(sheet([[1, 2, 3]], head=["a", "b", "c"]))
    with pytest.raises(WorkloadError, match="no usable rows"):
        import_workload(sheet([row(1, "", "", "", 0)]))
    with pytest.raises(WorkloadError, match="not found"):
        import_workload(sheet(BASIC), sheet="Nope")
