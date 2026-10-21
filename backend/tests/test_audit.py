import csv
import io
import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from automated_academics.api import create_app
from automated_academics.changes import describe_changes, describe_timetable_edit
from automated_academics.models import Placement, Timetable
from automated_academics.synthetic import sample_institution

PW = "correct horse battery"


def wait_done(c, jid):
    for _ in range(80):
        if c.get(f"/jobs/{jid}").json()["status"] in ("done", "failed"):
            return
        time.sleep(0.5)


@pytest.fixture
def world(tmp_path):
    """root (admin), owen owns an institution, wendy edits it, coral edits only CS, vera views, zed is outside."""
    path = str(tmp_path / "t.db")
    app = create_app(path, auth=True)
    with TestClient(app) as admin:
        admin.post("/auth/setup", json={"username": "root", "password": PW, "display_name": "Root Admin"})
        for name in ("owen", "wendy", "coral", "vera", "zed"):
            assert admin.post("/users", json={"username": name, "password": PW, "display_name": name.title()}).status_code == 201

        def sign_in(name):
            c = TestClient(app)
            assert c.post("/auth/login", json={"username": name, "password": PW}).status_code == 200
            return c

        owen = sign_in("owen")
        data = sample_institution().model_dump(mode="json")
        iid = owen.post("/institutions", json=data).json()["id"]
        for who, role, depts in (("wendy", "editor", None), ("coral", "editor", ["CS"]), ("vera", "viewer", None)):
            owen.put(f"/institutions/{iid}/members/{who}", json={"role": role, "departments": depts})
        yield {"app": app, "iid": iid, "data": data, "admin": admin, "owen": owen, "wendy": sign_in("wendy"),
               "coral": sign_in("coral"), "vera": sign_in("vera"), "zed": sign_in("zed"), "path": path}


def log(c, **q):
    r = c.get("/audit", params={"limit": 200, **q})
    assert r.status_code == 200, r.text
    return r.json()


def actions(c, **q):
    return [e["action"] for e in log(c, **q)]


# ---------- what gets recorded ----------

def test_sign_in_events_record_who_and_never_the_password(world):
    app = world["app"]
    c = TestClient(app)
    assert c.post("/auth/login", json={"username": "wendy", "password": "the wrong one"}).status_code == 401
    assert c.post("/auth/login", json={"username": "nobody", "password": "whatever else"}).status_code == 401
    assert c.post("/auth/login", json={"username": "wendy", "password": PW}).status_code == 200
    c.post("/auth/logout")
    events = log(world["admin"])
    failed = [e for e in events if e["action"] == "auth.login_failed"]
    assert {e["actor"] for e in failed} == {"wendy", "nobody"}
    assert all(e["ip"] for e in failed)
    assert next(e for e in failed if e["actor"] == "nobody")["detail"] == {"known_user": False}
    assert "auth.login" in [e["action"] for e in events] and "auth.logout" in [e["action"] for e in events]
    assert events[0]["action"] == "auth.logout" and events[0]["actor"] == "wendy"
    # the first account was set up, by someone who was not yet signed in
    setup = [e for e in events if e["action"] == "auth.setup"][0]
    assert setup["actor"] == "root"


def test_no_secret_ever_reaches_the_log(world):
    o, iid = world["owen"], world["iid"]
    world["admin"].patch(f"/users/{[u for u in world['admin'].get('/users').json() if u['username'] == 'zed'][0]['id']}",
                         json={"password": "an entirely different secret"})
    link = o.post(f"/institutions/{iid}/publications", json={"label": "Sem 1"})
    # no finished timetable yet -> 409; make one by keeping an imported (empty) timetable
    assert link.status_code == 409
    jid = o.post(f"/institutions/{iid}/timetable/imported", json={"placements": []}).json()["job_id"]
    link = o.post(f"/institutions/{iid}/publications", json={"label": "Sem 1", "job_id": jid})
    assert link.status_code == 201
    token = link.json()["token"]
    o.post("/auth/password", json={"current": PW, "new": "yet another long passphrase"})
    world["admin"].close()
    dump = "\n".join(sqlite3.connect(world["path"]).iterdump())
    audit_rows = "\n".join(line for line in dump.splitlines() if line.startswith("INSERT INTO \"audit\"") or line.startswith("INSERT INTO audit"))
    assert audit_rows  # it did record things
    for secret in (PW, "the wrong one", "an entirely different secret", "yet another long passphrase", token):
        assert secret not in audit_rows


def test_user_changes_are_recorded_with_what_changed(world):
    a = world["admin"]
    uid = {u["username"]: u["id"] for u in a.get("/users").json()}["zed"]
    a.post("/users", json={"username": "newbie", "password": PW})
    a.patch(f"/users/{uid}", json={"role": "admin", "disabled": False, "display_name": "Zed Z"})
    a.patch(f"/users/{uid}", json={"disabled": True})
    nid = {u["username"]: u["id"] for u in a.get("/users").json()}["newbie"]
    a.delete(f"/users/{nid}")
    by = {}
    for e in reversed(log(a)):  # oldest first, so the newest of each kind wins
        by[e["action"]] = e
    assert "newbie" in by["user.created"]["summary"]
    texts = " | ".join(e["summary"] for e in log(a) if e["action"] == "user.changed")
    assert "made them an administrator" in texts and "switched the account off" in texts and "renamed" in texts
    assert "newbie" in by["user.deleted"]["summary"]
    assert by["user.created"]["summary"].startswith("Root Admin ")


def test_institution_lifecycle_and_who_did_what(world):
    w, iid = world["wendy"], world["iid"]
    data = w.get(f"/institutions/{iid}").json()
    data["faculty"][0]["name"] = "Renamed Teacher"
    data["faculty"][1]["name"] = "Another Rename"
    r = w.put(f"/institutions/{iid}", json=data)
    assert r.status_code == 200
    ev = [e for e in log(world["owen"] if False else world["admin"], institution=iid) if e["action"] == "institution.updated"][0]
    assert ev["actor"] == "wendy" and ev["institution"] == data["name"]
    assert "changed 2 teachers" in ev["summary"] and data["faculty"][0]["id"] in ev["summary"]
    assert ev["detail"]["faculty"]["changed"] == sorted([data["faculty"][0]["id"], data["faculty"][1]["id"]])
    # saving with nothing changed leaves no entry
    n = len(log(world["admin"]))
    again = w.get(f"/institutions/{iid}").json()
    assert w.put(f"/institutions/{iid}", json=again).status_code == 200
    assert len(log(world["admin"])) == n
    # deletion keeps the name, since the institution itself is gone
    assert world["owen"].delete(f"/institutions/{iid}").status_code == 204
    deleted = [e for e in log(world["admin"]) if e["action"] == "institution.deleted"][0]
    assert data["name"] in deleted["summary"] and deleted["institution"] == data["name"]
    assert any(e["action"] == "institution.created" and e["actor"] == "owen" for e in log(world["admin"]))


def test_generating_and_the_solver_finishing_are_both_recorded(world):
    o, iid = world["owen"], world["iid"]
    jid = o.post(f"/institutions/{iid}/solve", json={"time_limit_s": 3}).json()["job_id"]
    wait_done(o, jid)
    time.sleep(0.3)
    events = {e["action"]: e for e in log(world["admin"], institution=iid)}
    assert events["timetable.generated"]["actor"] == "owen"
    assert events["timetable.finished"]["actor"] == "system" and events["timetable.finished"]["detail"]["job"] == jid


def test_hand_edits_publications_and_sharing_are_recorded(world):
    o, iid = world["owen"], world["iid"]
    inst = o.get(f"/institutions/{iid}").json()
    jid = o.post(f"/institutions/{iid}/solve", json={"time_limit_s": 3}).json()["job_id"]
    wait_done(o, jid)
    tt = o.get(f"/jobs/{jid}/timetable").json()
    tt["placements"][0]["day"] = (tt["placements"][0]["day"] + 1) % len(inst["calendar"]["day_names"])
    assert o.put(f"/jobs/{jid}/timetable", json=tt).status_code == 200
    assert o.put(f"/jobs/{jid}/timetable", json=tt).status_code == 200  # same again: nothing moved
    link = o.post(f"/institutions/{iid}/publications", json={"label": "Semester 1"}).json()
    o.patch(f"/institutions/{iid}/publications/{link['id']}", json={"label": "Semester 1 final"})
    o.delete(f"/institutions/{iid}/publications/{link['id']}")
    o.put(f"/institutions/{iid}/members/zed", json={"role": "viewer"})
    o.put(f"/institutions/{iid}/members/coral", json={"role": "editor", "departments": ["CS", "EE"]})
    o.delete(f"/institutions/{iid}/members/zed")
    got = actions(world["admin"], institution=iid)
    assert got.count("timetable.edited") == 1
    for a in ("publication.created", "publication.changed", "publication.withdrawn", "member.set", "member.removed"):
        assert a in got
    texts = [e["summary"] for e in log(world["admin"], institution=iid)]
    assert any("moved 1 session" in t for t in texts)
    assert any("limited to CS, EE" in t for t in texts)
    assert not any(link["token"] in json.dumps(e) for e in log(world["admin"]))


def test_refused_attempts_are_recorded(world):
    c, iid = world["coral"], world["iid"]
    data = c.get(f"/institutions/{iid}").json()
    data["rooms"][0]["name"] = "Sneaky"  # rooms are shared by every department
    r = c.put(f"/institutions/{iid}", json=data)
    assert r.status_code == 403 and r.json()["detail"]["scoped"]
    assert c.post(f"/institutions/{iid}/solve", json={}).status_code == 403
    refused = [e for e in log(world["admin"]) if e["action"] == "access.refused"]
    assert len(refused) == 2 and all(e["actor"] == "coral" for e in refused)


def test_public_link_views_are_not_logged(world):
    o, iid = world["owen"], world["iid"]
    jid = o.post(f"/institutions/{iid}/timetable/imported", json={"placements": []}).json()["job_id"]
    token = o.post(f"/institutions/{iid}/publications", json={"job_id": jid}).json()["token"]
    n = len(log(world["admin"]))
    anon = TestClient(world["app"])
    assert anon.get(f"/public/{token}").status_code == 200
    assert len(log(world["admin"])) == n


# ---------- who can read it ----------

def test_who_may_read_the_log(world):
    iid = world["iid"]
    assert world["admin"].get("/audit").status_code == 200
    for who in ("owen", "wendy", "vera", "zed"):
        assert world[who].get("/audit").status_code == 403  # the whole log is for administrators
    assert world["owen"].get(f"/institutions/{iid}/audit").status_code == 200  # the owner sees their institution's
    assert world["admin"].get(f"/institutions/{iid}/audit").status_code == 200
    assert world["wendy"].get(f"/institutions/{iid}/audit").status_code == 403  # editors and viewers don't
    assert world["vera"].get(f"/institutions/{iid}/audit.csv").status_code == 403
    assert world["zed"].get(f"/institutions/{iid}/audit").status_code == 404  # outsiders are not told it exists
    assert TestClient(world["app"]).get("/audit").status_code == 401


def test_an_owner_sees_only_their_institutions_entries(world):
    o, iid = world["owen"], world["iid"]
    other = world["wendy"].post("/institutions", json=world["data"]).json()["id"]
    mine = o.get(f"/institutions/{iid}/audit", params={"limit": 200}).json()
    assert mine and all(e["institution_id"] == iid for e in mine)
    assert other not in {e["institution_id"] for e in mine}


def test_everything_is_off_without_sign_in(tmp_path):
    with TestClient(create_app(str(tmp_path / "t.db"), auth=False)) as c:
        iid = c.post("/institutions", json=sample_institution().model_dump(mode="json")).json()["id"]
        for url in ("/audit", "/audit.csv", "/audit/verify", f"/institutions/{iid}/audit", f"/institutions/{iid}/audit.csv"):
            assert c.get(url).status_code == 409
    rows = sqlite3.connect(str(tmp_path / "t.db")).execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    assert rows == 0  # nothing was recorded


# ---------- filters, paging, csv ----------

def test_filters_paging_and_search(world):
    a = world["admin"]
    for i in range(7):
        world["owen"].post("/auth/login", json={"username": "owen", "password": "wrong password %d" % i})
    everything = log(a)
    seqs = [e["seq"] for e in everything]
    assert seqs == sorted(seqs, reverse=True)  # newest first
    page1 = a.get("/audit", params={"limit": 3}).json()
    page2 = a.get("/audit", params={"limit": 3, "before": page1[-1]["seq"]}).json()
    assert [e["seq"] for e in page1 + page2] == seqs[:6]
    assert len(a.get("/audit", params={"limit": 9999}).json()) == len(everything)  # capped at 200, not an error
    assert set(actions(a, user="wendy")) <= {"auth.login"}
    assert set(actions(a, action="auth.login_failed")) == {"auth.login_failed"}
    assert set(actions(a, action="auth")) >= {"auth.login", "auth.login_failed", "auth.setup"}  # a prefix
    assert all(e["action"].startswith("institution.") for e in log(a, action="institution"))
    assert all("wendy" in (e["summary"] + e["actor"]).lower() for e in log(a, q="wendy"))
    assert log(a, q="50%_never") == []  # wildcards are not special
    assert log(a, since="2999-01-01") == [] and len(log(a, until="2999-01-01")) == len(everything)


def test_csv_export_matches_and_neutralises_formulas(world):
    a = world["admin"]
    evil = "=HYPERLINK(\"http://x\")"
    TestClient(world["app"]).post("/auth/login", json={"username": evil, "password": "nope nope nope"})
    TestClient(world["app"]).post("/auth/login", json={"username": "+cmd", "password": "nope nope nope"})
    r = a.get("/audit.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0][:3] == ["When (UTC)", "Who", "What"]
    assert len(rows) - 1 == len(log(a))
    who = [row[1] for row in rows[1:]]
    assert "'" + evil.lower() in who and "'+cmd" in who
    assert not any(cell[:1] in "=+-@" for row in rows[1:] for cell in row if cell)
    assert len(world["owen"].get(f"/institutions/{world['iid']}/audit.csv").content) > 50


# ---------- the tamper-evident chain ----------

def test_the_chain_verifies_and_notices_edits_and_removals(world):
    a, path = world["admin"], world["path"]
    v = a.get("/audit/verify").json()
    assert v["ok"] is True and v["checked"] == len(log(a)) and v["first_bad"] is None
    assert world["owen"].get("/audit/verify").status_code == 403
    db = sqlite3.connect(path)
    mid = log(a)[3]["seq"]
    db.execute("UPDATE audit SET summary='Nobody did anything' WHERE seq=?", (mid,))
    db.commit()
    bad = a.get("/audit/verify").json()
    assert bad["ok"] is False and bad["first_bad"] == mid
    assert bad["checked"] == len([e for e in log(a) if e["seq"] < mid])
    db.close()


def test_deleting_an_entry_in_the_middle_is_noticed(world):
    a, path = world["admin"], world["path"]
    seqs = sorted(e["seq"] for e in log(a))
    victim = seqs[len(seqs) // 2]
    db = sqlite3.connect(path)
    db.execute("DELETE FROM audit WHERE seq=?", (victim,))
    db.commit()
    db.close()
    v = a.get("/audit/verify").json()
    after = min(x for x in seqs if x > victim)  # the entry that now points at a missing one
    assert v["ok"] is False and v["first_bad"] == after


def test_concurrent_writers_keep_one_unbroken_chain(world):
    import threading
    app = world["app"]
    errors = []

    def hammer(n):
        c = TestClient(app)
        for i in range(8):
            try:
                c.post("/auth/login", json={"username": f"ghost{n}", "password": "nope nope"})
            except Exception as e:  # noqa: BLE001
                errors.append(e)

    ts = [threading.Thread(target=hammer, args=(n,)) for n in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors
    assert world["admin"].get("/audit/verify").json()["ok"] is True


# ---------- robustness ----------

def test_a_failing_log_never_stops_the_work(world, monkeypatch):
    import automated_academics.store as store_mod

    def boom(self, *a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store_mod.Store, "add_audit", boom)
    o, iid = world["owen"], world["iid"]
    data = o.get(f"/institutions/{iid}").json()
    data["faculty"][0]["name"] = "Still saved"
    assert o.put(f"/institutions/{iid}", json=data).status_code == 200
    assert o.get(f"/institutions/{iid}").json()["faculty"][0]["name"] == "Still saved"
    assert TestClient(world["app"]).post("/auth/login", json={"username": "wendy", "password": PW}).status_code == 200


def test_an_old_database_gains_the_table(tmp_path):
    path = str(tmp_path / "old.db")
    with TestClient(create_app(path, auth=True)) as c:
        c.post("/auth/setup", json={"username": "root", "password": PW})
    db = sqlite3.connect(path)
    db.execute("DROP TABLE audit")
    db.commit()
    db.close()
    with TestClient(create_app(path, auth=True)) as c:
        assert c.post("/auth/login", json={"username": "root", "password": PW}).status_code == 200
        assert c.get("/audit/verify").json()["ok"] is True
        assert c.get("/audit").json()[0]["action"] == "auth.login"


# ---------- the plain-language summaries ----------

def test_describe_changes_wording():
    old = sample_institution()
    assert describe_changes(old, old.model_copy(deep=True)) == ("", {})
    new = old.model_copy(deep=True)
    new.faculty = new.faculty[2:]  # two removed
    new.batches[0] = new.batches[0].model_copy(update={"strength": new.batches[0].strength + 1})
    what, detail = describe_changes(old, new)
    assert "removed 2 teachers" in what and "changed 1 class" in what
    assert detail["faculty"]["removed"] == sorted(f.id for f in old.faculty[:2])
    renamed = old.model_copy(update={"name": "Elsewhere"})
    assert describe_changes(old, renamed)[0] == "renamed the institution"


def test_describe_timetable_edit_wording():
    a = Timetable(placements=[Placement(offering_id="o1", session_index=0, day=0, start=0, length=1, room_id="r"),
                              Placement(offering_id="o2", session_index=0, day=0, start=1, length=1, room_id="r")], status="OPTIMAL")
    assert describe_timetable_edit(a, a) == ("", {})
    b = Timetable(placements=[a.placements[0].model_copy(update={"day": 2})], status="OPTIMAL")
    what, detail = describe_timetable_edit(a, b)
    assert what == "moved 1 session, removed 1" and detail["offerings"] == ["o1", "o2"]


# ---------- gaps found by mutation checks ----------

def test_a_page_never_holds_more_than_200_rows(world):
    from automated_academics.store import Store
    s = Store(world["path"])
    for i in range(210):
        s.add_audit(None, "system", "test.filler", f"filler {i}")
    assert len(world["admin"].get("/audit", params={"limit": 100000}).json()) == 200
    assert len(world["admin"].get("/audit", params={"limit": 0}).json()) == 1


def test_search_text_is_literal_not_a_wildcard(world):
    assert log(world["admin"], q="%") == []
    assert log(world["admin"], q="_") == []
    from automated_academics.store import Store
    Store(world["path"]).add_audit(None, "system", "test.x", "grew by 50% today")
    assert len(log(world["admin"], q="50%")) == 1


def test_an_altered_entry_with_its_own_hash_recomputed_is_still_noticed(world):
    """Someone who edits an entry and fixes up that entry's hash is caught by the entry after it."""
    from automated_academics.store import _audit_hash
    db = sqlite3.connect(world["path"])
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT * FROM audit ORDER BY seq").fetchall()
    victim = dict(rows[2])
    victim["summary"] = "Nothing happened here"
    db.execute("UPDATE audit SET summary=?, hash=? WHERE seq=?",
               (victim["summary"], _audit_hash(victim["prev_hash"], victim), victim["seq"]))
    db.commit()
    db.close()
    v = world["admin"].get("/audit/verify").json()
    assert v["ok"] is False and v["first_bad"] == rows[3]["seq"]
