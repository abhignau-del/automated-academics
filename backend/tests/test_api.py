import time

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from automated_academics.api import create_app
from automated_academics.excel_io import export_workbook
from automated_academics.store import Store
from automated_academics.synthetic import sample_institution


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(str(tmp_path / "t.db"))) as c:
        yield c


@pytest.fixture
def xlsx_bytes(tmp_path):
    p = tmp_path / "i.xlsx"
    export_workbook(sample_institution(), p)
    return p.read_bytes()


def upload(client, data, name="i.xlsx"):
    return client.post("/institutions/upload", files={"file": (name, data)})


def wait(client, job_id, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.5)
    raise AssertionError("job did not finish")


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_template_download_is_a_valid_upload(client):
    r = client.get("/institutions/template")
    assert r.status_code == 200 and "spreadsheetml" in r.headers["content-type"]
    assert upload(client, r.content).status_code == 201


def test_upload_solve_and_view(client, xlsx_bytes):
    iid = upload(client, xlsx_bytes).json()["id"]
    assert client.get(f"/institutions/{iid}").json()["name"].startswith("Sample")
    assert [i["id"] for i in client.get("/institutions").json()] == [iid]

    r = client.post(f"/institutions/{iid}/solve", json={"time_limit_s": 4})
    assert r.status_code == 202
    job = wait(client, r.json()["job_id"])
    assert job["status"] == "done", job

    jid = job["id"]
    tt = client.get(f"/jobs/{jid}/timetable").json()
    assert len(tt["placements"]) == 68

    # a solver result must pass the independent validator
    check = client.post(f"/institutions/{iid}/validate", json=tt).json()
    assert (check["ok"], check["conflicts"], check["details"]) == (True, [], [])
    assert set(check["quality"]) == {"repeat_course_day", "batch_gaps", "faculty_gaps", "peak_day_load", "avoid_slot"}
    assert check["penalty"] == tt["penalty"]  # live scoring agrees with what the solver reported

    # a hand-edit that creates a clash is reported, naming the offerings involved
    tt["placements"][1].update({k: tt["placements"][0][k] for k in ("day", "start", "room_id")})
    bad = client.post(f"/institutions/{iid}/validate", json=tt).json()
    assert bad["ok"] is False and bad["conflicts"]
    assert any(tt["placements"][1]["offering_id"] in d["offering_ids"] for d in bad["details"])

    v = client.get(f"/jobs/{jid}/views/batch/MG-UG1").json()
    assert v["sessions"] and all("course_name" in s and "day_name" in s for s in v["sessions"])
    # the shared MDC elective appears in both attending batches' views
    for batch in ("MG-UG1", "CS-UG1"):
        sessions = client.get(f"/jobs/{jid}/views/batch/{batch}").json()["sessions"]
        assert sum(s["offering_id"] == "O-MDC101" for s in sessions) == 3
    assert client.get(f"/jobs/{jid}/views/faculty/CSF1").status_code == 200
    assert client.get(f"/jobs/{jid}/views/room/L1").status_code == 200
    assert client.get(f"/jobs/{jid}/views/room/NOPE").status_code == 404


def test_save_edit_and_reload_latest(client, xlsx_bytes):
    iid = upload(client, xlsx_bytes).json()["id"]
    assert client.get(f"/institutions/{iid}/latest-job").status_code == 404
    jid = client.post(f"/institutions/{iid}/solve", json={"time_limit_s": 4}).json()["job_id"]
    assert wait(client, jid)["status"] == "done"
    assert client.get(f"/institutions/{iid}/latest-job").json()["id"] == jid

    tt = client.get(f"/jobs/{jid}/timetable").json()
    first = tt["placements"][0]
    first["day"], first["start"] = (first["day"] + 1) % 6, 0 if first["length"] == 1 else 4
    report = client.put(f"/jobs/{jid}/timetable", json=tt).json()
    assert set(report) == {"ok", "conflicts", "details", "quality", "penalty"}
    saved = client.get(f"/jobs/{jid}/timetable").json()
    assert saved["status"] == "MANUAL"
    assert (saved["placements"][0]["day"], saved["placements"][0]["start"]) == (first["day"], first["start"])


def test_cannot_save_into_missing_or_failed_job(client):
    empty = {"placements": [], "status": "x"}
    assert client.put("/jobs/nope/timetable", json=empty).status_code == 404

    inst = sample_institution()
    inst.rooms = [r for r in inst.rooms if r.kind.value != "lab"]  # forces a failed job
    iid = client.post("/institutions", json=inst.model_dump(mode="json")).json()["id"]
    jid = client.post(f"/institutions/{iid}/solve").json()["job_id"]
    assert wait(client, jid)["status"] == "failed"
    assert client.put(f"/jobs/{jid}/timetable", json=empty).status_code == 409


def test_malformed_edits_are_reported_not_crashed(client, xlsx_bytes):
    iid = upload(client, xlsx_bytes).json()["id"]
    bad = {"status": "x", "placements": [
        {"offering_id": "GHOST", "session_index": 0, "day": 0, "start": 0, "length": 1, "room_id": "C1"},
        {"offering_id": "O-MDC101", "session_index": 0, "day": 99, "start": 0, "length": 1, "room_id": "C1"},
        {"offering_id": "O-MDC101", "session_index": 0, "day": 0, "start": 0, "length": 1, "room_id": "ZZ"},
    ]}
    r = client.post(f"/institutions/{iid}/validate", json=bad)
    assert r.status_code == 200
    text = " ".join(r.json()["conflicts"])
    assert "unknown offering GHOST" in text and "outside the week" in text and "unknown room ZZ" in text


def test_upload_reports_row_level_problems(client, xlsx_bytes, tmp_path):
    p = tmp_path / "bad.xlsx"
    p.write_bytes(xlsx_bytes)
    wb = load_workbook(p)
    wb["Rooms"]["C2"] = "lots"
    wb.save(p)
    r = upload(client, p.read_bytes())
    assert r.status_code == 422
    issue = r.json()["detail"]["issues"][0]
    assert issue["sheet"] == "Rooms" and issue["row"] == 2 and "capacity" in issue["message"]


def test_upload_rejects_non_xlsx_and_oversize(client):
    assert upload(client, b"this is not a spreadsheet").status_code == 400
    assert upload(client, b"0" * (5 * 1024 * 1024 + 10)).status_code == 413


def test_unknown_ids_return_404(client):
    assert client.get("/institutions/nope").status_code == 404
    assert client.post("/institutions/nope/solve").status_code == 404
    assert client.get("/jobs/nope").status_code == 404


def test_infeasible_job_fails_cleanly(client):
    inst = sample_institution()
    inst.rooms = [r for r in inst.rooms if r.kind.value != "lab"]  # labs have nowhere to go
    iid = client.post("/institutions", json=inst.model_dump(mode="json")).json()["id"]
    jid = client.post(f"/institutions/{iid}/solve").json()["job_id"]
    job = wait(client, jid)
    assert job["status"] == "failed"
    assert "needs a lab" in job["error"] and "there are none" in job["error"]  # names the actual cause
    assert client.get(f"/jobs/{jid}/timetable").status_code == 409


def test_solve_time_limit_is_bounded(client):
    iid = client.post("/institutions", json=sample_institution().model_dump(mode="json")).json()["id"]
    assert client.post(f"/institutions/{iid}/solve", json={"time_limit_s": 99999}).status_code == 422


def test_unfinished_jobs_fail_on_restart(tmp_path):
    db = str(tmp_path / "r.db")
    store = Store(db)
    iid = store.add_institution(sample_institution())
    jid = store.create_job(iid, 10)
    with TestClient(create_app(db)) as c:
        job = c.get(f"/jobs/{jid}").json()
    assert job["status"] == "failed" and "restarted" in job["error"]


def test_pins_save_without_making_the_timetable_stale(client):
    inst = sample_institution()
    iid = client.post("/institutions", json=inst.model_dump(mode="json")).json()["id"]
    jid = client.post(f"/institutions/{iid}/solve", json={"time_limit_s": 4}).json()["job_id"]
    assert wait(client, jid)["status"] == "done"

    data = client.get(f"/institutions/{iid}").json()
    data["pins"] = [{"offering_id": "O-MDC101", "session_index": 0, "day": 0, "start": 0, "room_id": None}]
    assert client.put(f"/institutions/{iid}", json=data).status_code == 200
    assert client.get(f"/institutions/{iid}").json()["pins"] == data["pins"]
    assert client.get(f"/jobs/{jid}").json()["stale"] is False  # pins only steer the next Generate

    data["name"] = "Renamed"  # any other change still does
    client.put(f"/institutions/{iid}", json=data)
    assert client.get(f"/jobs/{jid}").json()["stale"] is True

    data["pins"] = [{"offering_id": "GHOST", "session_index": 0, "day": 0, "start": 0, "room_id": None}]
    assert client.put(f"/institutions/{iid}", json=data).status_code == 422  # dangling pin is refused
