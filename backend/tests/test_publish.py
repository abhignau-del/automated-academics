import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from automated_academics.api import create_app
from automated_academics.store import Store
from automated_academics.synthetic import sample_institution

PW = "correct horse battery"


def wait_done(c, jid):
    for _ in range(80):
        if c.get(f"/jobs/{jid}").json()["status"] in ("done", "failed"):
            return
        time.sleep(0.5)


@pytest.fixture
def world(tmp_path):
    """An institution with a generated timetable, owned by 'owen', with a whole-institution editor, a CS-only one and a viewer."""
    path = str(tmp_path / "t.db")
    app = create_app(path, auth=True)
    with TestClient(app) as admin:
        admin.post("/auth/setup", json={"username": "root", "password": PW})
        for name in ("owen", "wendy", "coral", "vera", "zed"):
            assert admin.post("/users", json={"username": name, "password": PW}).status_code == 201

        def sign_in(name):
            c = TestClient(app)
            assert c.post("/auth/login", json={"username": name, "password": PW}).status_code == 200
            return c

        owen = sign_in("owen")
        iid = owen.post("/institutions", json=sample_institution().model_dump(mode="json")).json()["id"]
        for who, role, depts in (("wendy", "editor", None), ("coral", "editor", ["CS"]), ("vera", "viewer", None)):
            owen.put(f"/institutions/{iid}/members/{who}", json={"role": role, "departments": depts})
        jid = owen.post(f"/institutions/{iid}/solve", json={"time_limit_s": 4}).json()["job_id"]
        wait_done(owen, jid)
        yield {"app": app, "iid": iid, "jid": jid, "owen": owen, "wendy": sign_in("wendy"), "coral": sign_in("coral"),
               "vera": sign_in("vera"), "zed": sign_in("zed"), "path": path, "public": TestClient(app)}


def publish(w, who="owen", **body):
    return w[who].post(f"/institutions/{w['iid']}/publications", json=body)


# ---------- who may publish ----------

def test_editors_of_the_whole_institution_can_publish_and_others_cannot(world):
    assert publish(world, "owen").status_code == 201
    assert publish(world, "wendy").status_code == 201
    assert publish(world, "vera").status_code == 403  # a viewer
    assert publish(world, "zed").status_code == 404  # not a member: not even revealed to exist
    r = publish(world, "coral")  # limited to one department, but a link shows the whole institution
    assert r.status_code == 403 and r.json()["detail"]["scoped"] is True


def test_publishing_needs_shared_mode(tmp_path):
    with TestClient(create_app(str(tmp_path / "t.db"), auth=False)) as c:
        iid = c.post("/institutions", json=sample_institution().model_dump(mode="json")).json()["id"]
        r = c.post(f"/institutions/{iid}/publications", json={})
        assert r.status_code == 409 and "shared mode" in r.json()["detail"]


def test_it_needs_a_finished_timetable_of_this_institution(world):
    o, iid = world["owen"], world["iid"]
    assert o.post(f"/institutions/{iid}/publications", json={"job_id": "nope"}).status_code == 422
    other = o.post("/institutions", json=sample_institution().model_dump(mode="json")).json()["id"]
    assert o.post(f"/institutions/{other}/publications", json={}).status_code == 409  # nothing generated yet
    assert o.post(f"/institutions/{other}/publications", json={"job_id": world["jid"]}).status_code == 422  # someone else's timetable


# ---------- the link itself ----------

def test_the_link_is_long_random_and_listed_for_editors(world):
    a = publish(world, label="  Semester 1  ").json()
    b = publish(world).json()
    assert len(a["token"]) >= 40 and a["token"] != b["token"]
    assert a["path"] == f"/#/p/{a['token']}" and a["label"] == "Semester 1" and a["kinds"] == ["batch"]
    listed = world["wendy"].get(f"/institutions/{world['iid']}/publications").json()
    assert {x["id"] for x in listed} == {a["id"], b["id"]}
    assert world["vera"].get(f"/institutions/{world['iid']}/publications").status_code == 403  # viewers don't see the links


# ---------- what anyone with the link can see ----------

def test_no_account_is_needed_and_only_chosen_views_are_offered(world):
    link = publish(world, kinds=["batch"]).json()
    pub = world["public"]
    meta = pub.get(f"/public/{link['token']}")
    assert meta.status_code == 200
    m = meta.json()
    assert m["institution"].startswith("Sample") and m["kinds"] == ["batch"] and set(m["entities"]) == {"batch"}
    assert m["everything"] is False
    assert any(e["id"] == "CS-UG1" for e in m["entities"]["batch"])
    assert "faculty" not in m["entities"] and "room" not in m["entities"]  # the other views are not even listed
    v = pub.get(f"/public/{link['token']}/views/batch/CS-UG1")
    assert v.status_code == 200 and v.json()["sessions"]
    assert pub.get(f"/public/{link['token']}/views/faculty/CSF1").status_code == 404
    assert pub.get(f"/public/{link['token']}/views/room/C1").status_code == 404
    assert pub.get(f"/public/{link['token']}/views/batch/NOPE").status_code == 404


def test_the_ordinary_api_still_needs_a_login(world):
    link = publish(world).json()
    pub = world["public"]
    for path in ("/institutions", f"/institutions/{world['iid']}", f"/jobs/{world['jid']}/timetable",
                 f"/institutions/{world['iid']}/publications"):
        assert pub.get(path).status_code == 401, path
    assert pub.get(f"/public/{link['token']}").status_code == 200  # but the link works


def test_downloads_follow_what_was_chosen(world):
    some = publish(world, kinds=["batch", "faculty"]).json()["token"]
    everything = publish(world, kinds=["batch", "faculty", "room"]).json()["token"]
    pub = world["public"]
    pdf = pub.get(f"/public/{some}/export.pdf", params={"kind": "batch", "id": "CS-UG1"})
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert pub.get(f"/public/{some}/export.pdf", params={"kind": "batch"}).status_code == 200  # every class
    assert pub.get(f"/public/{some}/export.pdf", params={"kind": "room"}).status_code == 404
    assert pub.get(f"/public/{some}/export.pdf").status_code == 404  # "everything" includes rooms
    assert pub.get(f"/public/{some}/export.xlsx").status_code == 404
    assert pub.get(f"/public/{everything}/export.pdf").status_code == 200
    xlsx = pub.get(f"/public/{everything}/export.xlsx")
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"
    assert pub.get(f"/public/{everything}/export.pdf", params={"id": "CS-UG1"}).status_code == 422
    assert pub.get(f"/public/{some}/export.pdf", params={"kind": "batch", "id": "NOPE"}).status_code == 404


def test_public_answers_are_not_cached_indexed_or_leaked_through_referrers(world):
    token = publish(world).json()["token"]
    for path in (f"/public/{token}", f"/public/{token}/views/batch/CS-UG1"):
        h = world["public"].get(path).headers
        assert h["cache-control"] == "no-store" and "noindex" in h["x-robots-tag"] and h["referrer-policy"] == "no-referrer"
    pdf = world["public"].get(f"/public/{token}/export.pdf", params={"kind": "batch", "id": "CS-UG1"})
    assert pdf.headers["cache-control"] == "no-store" and pdf.headers["referrer-policy"] == "no-referrer"
    assert "attachment" in pdf.headers["content-disposition"]
    # a downloaded file must not carry a length header from somewhere else: a real server would cut the connection
    for r in (pdf, world["public"].get(f"/public/{publish(world, kinds=['batch', 'faculty', 'room']).json()['token']}/export.xlsx")):
        assert int(r.headers.get("content-length", len(r.content))) == len(r.content)


# ---------- ending a link ----------

def test_revoking_ends_the_link_at_once(world):
    link = publish(world).json()
    assert world["public"].get(f"/public/{link['token']}").status_code == 200
    assert world["vera"].delete(f"/institutions/{world['iid']}/publications/{link['id']}").status_code == 403
    assert world["wendy"].delete(f"/institutions/{world['iid']}/publications/{link['id']}").status_code == 204
    assert world["public"].get(f"/public/{link['token']}").status_code == 404
    assert world["owen"].delete(f"/institutions/{world['iid']}/publications/{link['id']}").status_code == 404


def test_a_link_can_expire(world):
    link = publish(world, expires_days=30).json()
    assert link["expires_at"] and link["expired"] is False
    assert world["public"].get(f"/public/{link['token']}").status_code == 200
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="microseconds")
    Store(world["path"]).update_publication(link["id"], world["iid"], expires_at=past)
    r = world["public"].get(f"/public/{link['token']}")
    assert r.status_code == 410 and "expired" in r.json()["detail"]
    assert world["owen"].get(f"/institutions/{world['iid']}/publications").json()[0]["expired"] is True
    # extending it brings it back
    assert world["owen"].patch(f"/institutions/{world['iid']}/publications/{link['id']}", json={"expires_days": 7}).status_code == 200
    assert world["public"].get(f"/public/{link['token']}").status_code == 200
    # and null means never
    r = world["owen"].patch(f"/institutions/{world['iid']}/publications/{link['id']}", json={"expires_days": None})
    assert r.json()["expires_at"] is None


def test_a_link_can_be_changed_and_pointed_at_a_newer_timetable(world):
    o, iid = world["owen"], world["iid"]
    link = publish(world, kinds=["batch"]).json()
    new_job = o.post(f"/institutions/{iid}/solve", json={"time_limit_s": 4}).json()["job_id"]
    wait_done(o, new_job)
    r = o.patch(f"/institutions/{iid}/publications/{link['id']}", json={"job_id": new_job, "kinds": ["faculty", "batch"], "label": "Term 2"})
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"] == new_job and body["kinds"] == ["batch", "faculty"] and body["label"] == "Term 2"
    assert body["token"] == link["token"]  # the same address keeps working
    assert world["public"].get(f"/public/{link['token']}/views/faculty/CSF1").status_code == 200
    assert o.patch(f"/institutions/{iid}/publications/{link['id']}", json={"job_id": "ghost"}).status_code == 422
    assert o.patch(f"/institutions/{iid}/publications/ghost", json={"label": "x"}).status_code == 404
    assert o.patch(f"/institutions/{iid}/publications/{link['id']}", json={"kinds": []}).status_code == 422


def test_deleting_the_institution_ends_its_links(world):
    link = publish(world).json()
    assert world["owen"].delete(f"/institutions/{world['iid']}").status_code == 204
    assert world["public"].get(f"/public/{link['token']}").status_code == 404
    assert Store(world["path"]).list_publications(world["iid"]) == []


# ---------- guessing ----------

def test_wrong_addresses_are_not_revealed_and_guessing_is_slowed(world):
    pub = world["public"]
    assert pub.get("/public/not-a-real-token").status_code == 404
    for _ in range(40):
        pub.get("/public/another-wrong-one")
    r = pub.get("/public/yet-another")
    assert r.status_code == 429
    good = publish(world).json()["token"]
    assert pub.get(f"/public/{good}").status_code == 429  # a guesser is slowed down on every address, good or bad
    other = TestClient(world["app"], client=("203.0.113.9", 5000))
    assert other.get(f"/public/{good}").status_code == 200  # but someone else is unaffected


def test_a_database_from_before_publishing_gains_the_table(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    Store(str(db))
    with sqlite3.connect(db) as c:
        c.execute("DROP TABLE publications")
    store = Store(str(db))
    assert store.list_publications("none") == []


def test_a_timetable_that_is_not_finished_cannot_be_published(world):
    queued = Store(world["path"]).create_job(world["iid"], 10)  # created, never finished
    r = world["owen"].post(f"/institutions/{world['iid']}/publications", json={"job_id": queued})
    assert r.status_code == 409 and "isn't finished" in r.json()["detail"]
    r = world["owen"].patch(f"/institutions/{world['iid']}/publications/nope", json={"job_id": queued})
    assert r.status_code == 404


def test_the_chosen_views_are_kept_once_each_in_a_fixed_order(world):
    link = publish(world, kinds=["room", "batch", "batch"]).json()
    assert link["kinds"] == ["batch", "room"]
    assert world["public"].get(f"/public/{link['token']}").json()["kinds"] == ["batch", "room"]


def test_a_classs_own_clock_is_offered_so_its_times_can_be_shown(world):
    o, iid = world["owen"], world["iid"]
    d = o.get(f"/institutions/{iid}").json()
    t = lambda a, b: {"start": a, "end": b}  # noqa: E731
    d["calendar"]["times"] = [t(f"{8 + i:02d}:00", f"{8 + i:02d}:50") for i in range(7)]
    d["calendar"]["clocks"] = {"short": [t(f"{8 + i:02d}:00", f"{8 + i:02d}:40") for i in range(7)]}
    d["batches"] = [{**b, "clock": "short"} if b["id"] == "MG-UG1" else b for b in d["batches"]]
    assert o.put(f"/institutions/{iid}", json=d).status_code == 200
    token = publish(world, kinds=["batch", "faculty"]).json()["token"]
    meta = world["public"].get(f"/public/{token}").json()
    clocks = {e["id"]: e["clock"] for e in meta["entities"]["batch"]}
    assert clocks["MG-UG1"] == "short" and clocks["CS-UG1"] is None
    assert "clock" not in meta["entities"]["faculty"][0]
    assert meta["calendar"]["clocks"]["short"][0]["end"] == "08:40"
    sessions = world["public"].get(f"/public/{token}/views/batch/MG-UG1").json()["sessions"]
    own = [x for x in sessions if x["batch_ids"] == ["MG-UG1"]]  # (a joint class follows its first class's clock)
    assert own and all(x["time"].endswith(("8:40", "9:40", "10:40", "11:40", "12:40", "13:40", "14:40")) for x in own)
