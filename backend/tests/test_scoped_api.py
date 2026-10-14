import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from automated_academics.api import create_app
from automated_academics.store import Store
from automated_academics.synthetic import sample_institution

PW = "correct horse battery"


@pytest.fixture
def world(tmp_path):
    """An institution owned by 'owen' with a whole-institution editor, a CS-only editor, a CS+MG editor and a viewer."""
    app = create_app(str(tmp_path / "t.db"), auth=True)
    with TestClient(app) as admin:
        admin.post("/auth/setup", json={"username": "root", "password": PW})
        for name in ("owen", "wendy", "coral", "pair", "vera"):
            assert admin.post("/users", json={"username": name, "password": PW}).status_code == 201

        def sign_in(name):
            c = TestClient(app)
            assert c.post("/auth/login", json={"username": name, "password": PW}).status_code == 200
            return c

        owen = sign_in("owen")
        iid = owen.post("/institutions", json=sample_institution().model_dump(mode="json")).json()["id"]
        for who, role, depts in (("wendy", "editor", None), ("coral", "editor", ["CS"]), ("pair", "editor", ["MG", "CS"]),
                                 ("vera", "viewer", None)):
            r = owen.put(f"/institutions/{iid}/members/{who}", json={"role": role, "departments": depts})
            assert r.status_code == 200, r.text
        yield {"iid": iid, "owen": owen, "wendy": sign_in("wendy"), "coral": sign_in("coral"), "pair": sign_in("pair"),
               "vera": sign_in("vera"), "path": str(tmp_path / "t.db")}


def data(c, iid):
    return c.get(f"/institutions/{iid}").json()


def put(c, iid, d):
    return c.put(f"/institutions/{iid}", json=d)


def finish(c, jid):
    for _ in range(80):
        if c.get(f"/jobs/{jid}").json()["status"] in ("done", "failed"):
            return
        time.sleep(0.5)


# ---------- who has what scope ----------

def test_the_scope_is_visible_to_the_owner_and_in_the_persons_own_list(world):
    members = {m["username"]: m["departments"] for m in world["owen"].get(f"/institutions/{world['iid']}/members").json()}
    assert members == {"owen": None, "wendy": None, "coral": ["CS"], "pair": ["CS", "MG"], "vera": None}
    mine = world["coral"].get("/institutions").json()[0]
    assert mine["role"] == "editor" and mine["departments"] == ["CS"]
    assert world["wendy"].get("/institutions").json()[0]["departments"] is None


def test_only_an_editor_can_be_limited_and_blanks_mean_everything(world):
    o, iid = world["owen"], world["iid"]
    assert o.put(f"/institutions/{iid}/members/vera", json={"role": "viewer", "departments": ["CS"]}).status_code == 422
    r = o.put(f"/institutions/{iid}/members/coral", json={"role": "editor", "departments": ["  ", ""]})
    assert r.status_code == 200
    assert {m["username"]: m["departments"] for m in r.json()}["coral"] is None  # no real department named = unlimited
    o.put(f"/institutions/{iid}/members/coral", json={"role": "editor", "departments": ["MG", "CS", "MG"]})
    assert {m["username"]: m["departments"] for m in o.get(f"/institutions/{iid}/members").json()}["coral"] == ["CS", "MG"]


# ---------- editing data ----------

def test_a_coordinator_can_maintain_their_own_department(world):
    iid, c = world["iid"], world["coral"]
    d = data(c, iid)
    assert any(f["department"] == "MG" for f in d["faculty"])  # they can see everything
    d["faculty"] = [{**f, "name": "Dr Renamed"} if f["id"] == "CSF1" else f for f in d["faculty"]]
    d["courses"].append({"code": "CSX", "name": "New CS course", "department": "CS", "credits": 3, "category": "DSC", "room_kind": "classroom"})
    r = put(c, iid, d)
    assert r.status_code == 200, r.text
    assert next(f for f in data(world["owen"], iid)["faculty"] if f["id"] == "CSF1")["name"] == "Dr Renamed"


def test_the_same_person_is_refused_for_another_department_and_nothing_is_saved(world):
    iid, c = world["iid"], world["coral"]
    d = data(c, iid)
    d["faculty"] = [{**f, "name": "Hacked"} if f["id"] == "MGF1" else f for f in d["faculty"]]
    r = put(c, iid, d)
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["scoped"] is True and "MGF1" in detail["message"] and "CS" in detail["message"]
    assert next(f for f in data(world["owen"], iid)["faculty"] if f["id"] == "MGF1")["name"] != "Hacked"


def test_shared_things_are_refused_to_a_scoped_editor(world):
    iid, c = world["iid"], world["coral"]
    d = data(c, iid)
    rooms = {**d, "rooms": d["rooms"][:-1]}
    assert put(c, iid, rooms).status_code == 403
    settings = {**d, "calendar": {**d["calendar"], "lectures_per_day": 6}}
    assert put(c, iid, settings).status_code == 403
    renamed = {**d, "name": "Mine now"}
    assert put(c, iid, renamed).status_code == 403


def test_whole_institution_and_both_department_editors_are_not_limited(world):
    iid = world["iid"]
    d = data(world["wendy"], iid)
    d["rooms"] = d["rooms"][:-1]
    d = {**d, "rooms": d["rooms"] + [{"id": "NEW", "name": "New room", "capacity": 50, "kind": "classroom"}]}
    assert put(world["wendy"], iid, d).status_code == 200
    d2 = data(world["pair"], iid)
    d2["offerings"] = [{**o, "sessions": [1, 1]} if o["id"] == "O-MDC101" else o for o in d2["offerings"]]  # joint CS + MG
    assert put(world["pair"], iid, d2).status_code == 200


def test_a_joint_class_between_departments_needs_both(world):
    iid, c = world["iid"], world["coral"]
    d = data(c, iid)
    d["offerings"] = [{**o, "sessions": [1, 1]} if o["id"] == "O-MDC101" else o for o in d["offerings"]]
    r = put(c, iid, d)
    assert r.status_code == 403 and "O-MDC101" in r.json()["detail"]["message"]


def test_a_viewer_still_cannot_edit_and_pins_follow_the_offering(world):
    iid = world["iid"]
    d = data(world["vera"], iid)
    assert put(world["vera"], iid, d).status_code == 403
    c = world["coral"]
    d = data(c, iid)
    d["pins"] = [{"offering_id": "O-CS-UG1-T1", "session_index": 0, "day": 0, "start": 0, "room_id": None}]
    assert put(c, iid, d).status_code == 200
    d["pins"].append({"offering_id": "O-MG-UG1-T1", "session_index": 0, "day": 1, "start": 0, "room_id": None})
    assert put(c, iid, d).status_code == 403


# ---------- generating and moving sessions ----------

def test_a_scoped_editor_cannot_regenerate_but_a_whole_one_can(world):
    iid = world["iid"]
    r = world["coral"].post(f"/institutions/{iid}/solve", json={"time_limit_s": 4})
    assert r.status_code == 403 and r.json()["detail"]["scoped"] is True
    ok = world["wendy"].post(f"/institutions/{iid}/solve", json={"time_limit_s": 4})
    assert ok.status_code == 202


def test_a_scoped_editor_moves_only_their_own_sessions(world):
    iid = world["iid"]
    jid = world["wendy"].post(f"/institutions/{iid}/solve", json={"time_limit_s": 4}).json()["job_id"]
    finish(world["wendy"], jid)
    tt = world["coral"].get(f"/jobs/{jid}/timetable").json()
    mine = next(p for p in tt["placements"] if p["offering_id"] == "O-CS-UG1-T1" and p["session_index"] == 0)
    theirs = next(p for p in tt["placements"] if p["offering_id"] == "O-MG-UG1-T1" and p["session_index"] == 0)

    moved_mine = {**tt, "placements": [{**p, "day": (p["day"] + 1) % 6} if p is mine else p for p in tt["placements"]]}
    assert world["coral"].put(f"/jobs/{jid}/timetable", json=moved_mine).status_code == 200

    moved_theirs = {**tt, "placements": [{**p, "day": (p["day"] + 1) % 6} if p is theirs else p for p in tt["placements"]]}
    r = world["coral"].put(f"/jobs/{jid}/timetable", json=moved_theirs)
    assert r.status_code == 403 and "O-MG-UG1-T1" in r.json()["detail"]["message"]
    assert world["wendy"].put(f"/jobs/{jid}/timetable", json=moved_theirs).status_code == 200


def test_keeping_an_imported_timetable_is_a_whole_institution_action(world):
    iid = world["iid"]
    assert world["coral"].post(f"/institutions/{iid}/timetable/imported", json={"placements": []}).status_code == 403
    assert world["wendy"].post(f"/institutions/{iid}/timetable/imported", json={"placements": []}).status_code == 201


# ---------- databases that predate scopes ----------

def test_an_older_members_table_gains_the_column_and_means_the_whole_institution(tmp_path):
    db = tmp_path / "old.db"
    Store(str(db))  # current schema
    with sqlite3.connect(db) as c:
        c.execute("DROP TABLE members")
        c.execute("CREATE TABLE members (institution_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL, "
                  "PRIMARY KEY (institution_id, user_id))")
        c.execute("INSERT INTO members VALUES ('i1', 'u1', 'editor')")
    store = Store(str(db))
    assert store.member_role("i1", "u1") == "editor"
    assert store.member_departments("i1", "u1") is None
    store.set_member("i1", "u1", "editor", ["CS"])
    assert store.member_departments("i1", "u1") == ["CS"]
