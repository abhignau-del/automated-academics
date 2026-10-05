"""Data entry: structural checks, diagnostics, and the endpoints that use them."""

import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from automated_academics.api import create_app
from automated_academics.checks import check_institution, diagnose
from automated_academics.models import Slot
from automated_academics.store import Store
from automated_academics.synthetic import sample_institution


def sample_data():
    return sample_institution().model_dump(mode="json")


def located(issues, section, field=None):
    return [i for i in issues if i.section == section and (field is None or i.field == field)]


# ---------- structure ----------

def test_valid_data_passes_and_returns_the_institution():
    inst, issues = check_institution(sample_data())
    assert issues == [] and inst is not None and inst.name.startswith("Sample")


def test_blank_institution_is_valid():
    blank = dict(name="X", rooms=[], faculty=[], batches=[], courses=[], offerings=[])
    inst, issues = check_institution(blank)
    assert inst is not None and issues == []


def test_not_an_object_is_one_clear_problem():
    inst, issues = check_institution(["nope"])
    assert inst is None and len(issues) == 1 and issues[0].section is None


def test_field_errors_are_located_by_section_row_and_field():
    d = sample_data()
    d["rooms"][2]["capacity"] = 0
    d["batches"][4]["level"] = "Diploma"
    d["calendar"]["lectures_per_day"] = 0
    inst, issues = check_institution(d)
    assert inst is None
    assert [(i.section, i.index, i.field) for i in located(issues, "rooms")] == [("rooms", 2, "capacity")]
    assert [(i.section, i.index, i.field) for i in located(issues, "batches")] == [("batches", 4, "level")]
    cal = located(issues, "settings")
    assert cal and cal[0].field == "lectures_per_day"


def test_missing_and_duplicate_ids_are_reported_on_the_offending_row():
    d = sample_data()
    d["rooms"][1]["id"] = d["rooms"][0]["id"]
    d["faculty"][3]["id"] = "  "
    _, issues = check_institution(d)
    dup = located(issues, "rooms", "id")
    assert len(dup) == 1 and dup[0].index == 1 and "duplicate" in dup[0].message and "row 1" in dup[0].message
    req = located(issues, "faculty", "id")
    assert len(req) == 1 and req[0].index == 3 and "required" in req[0].message


def test_references_between_tables_name_the_row_and_field():
    d = sample_data()
    d["offerings"][0]["faculty_id"] = "NOBODY"
    d["offerings"][1]["course_code"] = "NOPE"
    d["offerings"][2]["batch_ids"] = ["CS-UG1", "GHOST"]
    d["batches"][5]["group_of"] = "MISSING"
    _, issues = check_institution(d)
    got = {(i.section, i.index, i.field) for i in issues}
    assert {("offerings", 0, "faculty_id"), ("offerings", 1, "course_code"),
            ("offerings", 2, "batch_ids"), ("batches", 5, "group_of")} <= got
    # each is reported once, with the offending value in the message
    assert any("NOBODY" in i.message for i in issues) and len(issues) == 4


def test_subgroup_rules():
    d = sample_data()
    idx = {b["id"]: i for i, b in enumerate(d["batches"])}
    d["batches"][idx["CS-UG1-A"]]["group_of"] = "CS-UG1-A"  # itself
    _, issues = check_institution(d)
    assert "itself" in located(issues, "batches", "group_of")[0].message

    d = sample_data()
    idx = {b["id"]: i for i, b in enumerate(d["batches"])}
    d["batches"][idx["CS-UG1"]]["group_of"] = "CS-UG3"  # CS-UG1-A would now be a sub-group of a sub-group
    _, issues = check_institution(d)
    assert any("sub-group" in i.message and i.section == "batches" for i in issues)


def test_a_cross_table_failure_is_never_silently_swallowed():
    d = sample_data()
    d["offerings"][0]["batch_ids"] = []  # an offering must have at least one batch
    inst, issues = check_institution(d)
    assert inst is None and located(issues, "offerings", "batch_ids")


# ---------- diagnostics ----------

def test_sample_has_no_diagnostic_errors():
    assert [i for i in diagnose(sample_institution()) if i.level == "error"] == []


def test_session_with_no_big_enough_room_is_an_error_on_that_offering():
    inst = sample_institution()
    for r in inst.rooms:
        if r.kind.value == "lab":
            r.capacity = 10
    errs = [i for i in diagnose(inst) if i.level == "error"]
    assert errs and all(e.section == "offerings" for e in errs)
    assert "lab" in errs[0].message and "10 seats" in errs[0].message


def test_no_lab_at_all_says_there_are_none():
    inst = sample_institution()
    inst.rooms = [r for r in inst.rooms if r.kind.value != "lab"]
    msg = [i.message for i in diagnose(inst) if i.level == "error"][0]
    assert "there are none" in msg


def test_faculty_with_more_lectures_than_free_slots():
    inst = sample_institution()
    inst.faculty[0].unavailable = [Slot(day=d, lecture=t) for d in range(6) for t in range(7)][:40]  # 2 free
    errs = [i for i in diagnose(inst) if i.level == "error" and i.section == "faculty"]
    assert errs and errs[0].index == 0 and "2 slots are free" in errs[0].message


def test_daily_limit_too_low_for_weekly_hours():
    inst = sample_institution()
    inst.faculty[1].max_lectures_per_day = 1
    errs = [i for i in diagnose(inst) if i.level == "error" and i.section == "faculty" and i.index == 1]
    assert errs and "daily limit" in errs[0].message


def test_block_that_cannot_fit_anywhere():
    inst = sample_institution()
    inst.calendar.lectures_per_day = 4
    inst.calendar.break_after = [1]  # the day is 2 + 2, so a 3-lecture block fits nowhere
    lab = next(o for o in inst.offerings if o.id.startswith("O-CS-UG1-LAB"))
    lab.sessions = [3]
    errs = [i for i in diagnose(inst) if i.level == "error" and i.section == "offerings"
            and "3-lecture block" in i.message]
    assert len(errs) == 1 and errs[0].field == "sessions" and lab.id in errs[0].message


def test_class_with_more_lectures_than_the_week_has_slots():
    inst = sample_institution()
    inst.calendar.day_names = ["Mon"]
    errs = [i for i in diagnose(inst) if i.level == "error" and i.section == "batches"]
    assert errs and "slots" in errs[0].message


def test_warnings_and_notes():
    inst = sample_institution()
    inst.faculty[0].avoid = [Slot(day=99, lecture=0)]
    inst.courses.append(sample_institution().courses[0].model_copy(update={"code": "UNUSED"}))
    got = {(i.level, i.section) for i in diagnose(inst)}
    assert ("warning", "faculty") in got  # out-of-range slot is ignored
    assert ("info", "courses") in got  # unused course


# ---------- endpoints ----------

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(str(tmp_path / "d.db"))) as c:
        yield c


def wait_done(client, jid):
    end = time.time() + 60
    while time.time() < end and client.get(f"/jobs/{jid}").json()["status"] not in ("done", "failed"):
        time.sleep(0.5)


def test_starter_data(client):
    blank = client.get("/institutions/starter").json()
    assert blank["rooms"] == [] and blank["weights"]["batch_gaps"] == 10 and blank["calendar"]["lectures_per_day"] == 7
    sample = client.get("/institutions/starter", params={"kind": "sample"}).json()
    assert len(sample["offerings"]) == 26
    assert client.post("/institutions/check", json=blank).json()["valid"] is True


def test_check_endpoint_reports_validity_and_issues(client):
    d = sample_data()
    ok = client.post("/institutions/check", json=d).json()
    assert ok["valid"] is True and not [i for i in ok["issues"] if i["level"] == "error"]
    d["rooms"][0]["capacity"] = -5
    bad = client.post("/institutions/check", json=d).json()
    assert bad["valid"] is False
    assert {"level": "error", "section": "rooms", "index": 0, "field": "capacity"}.items() <= bad["issues"][0].items()


def test_check_reports_impossible_data_but_stays_valid(client):
    d = sample_data()
    d["rooms"] = [r for r in d["rooms"] if r["kind"] != "lab"]
    r = client.post("/institutions/check", json=d).json()
    assert r["valid"] is True  # well formed, so it can be saved while the user keeps editing
    assert any(i["level"] == "error" and "lab" in i["message"] for i in r["issues"])


def test_create_update_and_structured_errors(client):
    created = client.post("/institutions", json=sample_data())
    assert created.status_code == 201
    assert [i for i in created.json()["issues"] if i["level"] == "error"] == []
    iid = created.json()["id"]

    d = sample_data()
    d["name"] = "Renamed"
    d["rooms"].append({"id": "C9", "name": "Classroom 9", "capacity": 80, "kind": "classroom"})
    r = client.put(f"/institutions/{iid}", json=d)
    assert r.status_code == 200 and r.json()["name"] == "Renamed"
    got = client.get(f"/institutions/{iid}").json()
    assert got["name"] == "Renamed" and got["rooms"][-1]["id"] == "C9"
    assert [i["name"] for i in client.get("/institutions").json()] == ["Renamed"]

    bad = sample_data()
    bad["rooms"][0]["capacity"] = 0
    r = client.put(f"/institutions/{iid}", json=bad)
    assert r.status_code == 422 and r.json()["detail"]["issues"][0]["section"] == "rooms"
    assert client.get(f"/institutions/{iid}").json()["name"] == "Renamed"  # nothing was changed
    assert client.post("/institutions", json=bad).status_code == 422
    assert client.put("/institutions/nope", json=sample_data()).status_code == 404


def test_delete_removes_institution_and_its_timetables(client):
    iid = client.post("/institutions", json=sample_data()).json()["id"]
    jid = client.post(f"/institutions/{iid}/solve", json={"time_limit_s": 4}).json()["job_id"]
    wait_done(client, jid)
    assert client.delete(f"/institutions/{iid}").status_code == 204
    assert client.get(f"/institutions/{iid}").status_code == 404
    assert client.get(f"/jobs/{jid}").status_code == 404
    assert client.delete(f"/institutions/{iid}").status_code == 404


def test_timetable_goes_stale_only_when_the_data_really_changes(client):
    iid = client.post("/institutions", json=sample_data()).json()["id"]
    jid = client.post(f"/institutions/{iid}/solve", json={"time_limit_s": 4}).json()["job_id"]
    wait_done(client, jid)
    assert client.get(f"/jobs/{jid}").json()["stale"] is False
    assert client.get(f"/institutions/{iid}/latest-job").json()["stale"] is False

    assert client.put(f"/institutions/{iid}", json=sample_data()).status_code == 200  # saved unchanged
    assert client.get(f"/jobs/{jid}").json()["stale"] is False

    d = sample_data()
    d["faculty"][0]["name"] = "Someone Else"
    client.put(f"/institutions/{iid}", json=d)
    assert client.get(f"/jobs/{jid}").json()["stale"] is True
    assert client.get(f"/institutions/{iid}/latest-job").json()["stale"] is True


def test_entered_data_can_be_downloaded_and_uploaded_again_unchanged(client):
    import io

    from automated_academics.excel_io import import_workbook

    d = sample_data()
    d["name"] = "Hand entered / test"
    d["faculty"][0]["avoid"] = [{"day": 5, "lecture": 6}]
    d["weights"]["batch_gaps"] = 25
    iid = client.post("/institutions", json=d).json()["id"]

    r = client.get(f"/institutions/{iid}/workbook.xlsx")
    assert r.status_code == 200 and "spreadsheetml" in r.headers["content-type"]
    assert 'filename="Hand_entered_test-data.xlsx"' in r.headers["content-disposition"]  # safe filename
    assert import_workbook(io.BytesIO(r.content)).model_dump() == client.get(f"/institutions/{iid}").json()

    again = client.post("/institutions/upload", files={"file": ("x.xlsx", r.content)})  # the app's own export re-imports
    assert again.status_code == 201
    assert client.get("/institutions/nope/workbook.xlsx").status_code == 404


def test_upload_reports_diagnostic_issues_too(client, tmp_path):
    from automated_academics.excel_io import export_workbook

    inst = sample_institution()
    inst.rooms = [r for r in inst.rooms if r.kind.value != "lab"]
    p = tmp_path / "x.xlsx"
    export_workbook(inst, p)
    r = client.post("/institutions/upload", files={"file": ("x.xlsx", p.read_bytes())})
    assert r.status_code == 201 and any(i["level"] == "error" for i in r.json()["issues"])


# ---------- storage ----------

def test_old_database_without_updated_at_is_migrated(tmp_path):
    db = str(tmp_path / "old.db")
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE institutions (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL);"
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, institution_id TEXT NOT NULL, status TEXT NOT NULL, "
        "time_limit_s REAL NOT NULL, created_at TEXT NOT NULL, finished_at TEXT, error TEXT, timetable TEXT);")
    con.execute("INSERT INTO institutions VALUES ('old1','Old','2026-10-01T10:00:00+00:00',?)",
                (sample_institution().model_dump_json(),))
    con.commit()
    con.close()

    store = Store(db)  # opening it migrates in place
    assert store.institution_updated_at("old1") == "2026-10-01T10:00:00+00:00"
    assert store.list_institutions()[0]["updated_at"] == "2026-10-01T10:00:00+00:00"
    assert store.get_institution("old1").name.startswith("Sample")
    new = store.add_institution(sample_institution())  # a positional INSERT would break after the migration
    assert store.institution_updated_at(new) is not None
    Store(db)  # and reopening is harmless
