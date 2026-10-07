import time

import pytest
from fastapi.testclient import TestClient

from automated_academics import auth as authlib
from automated_academics.api import create_app
from automated_academics.store import Store
from automated_academics.synthetic import sample_institution

PW = "correct horse battery"


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "t.db"), auth=True)


def client(app) -> TestClient:
    return TestClient(app)  # each has its own cookie jar, i.e. its own browser


@pytest.fixture
def admin(app):
    with TestClient(app) as c:  # the context manager runs the app's startup
        r = c.post("/auth/setup", json={"username": "Root", "password": PW, "display_name": "The Admin"})
        assert r.status_code == 201, r.text
        yield c


def add_user(admin, name, role="member"):
    r = admin.post("/users", json={"username": name, "password": PW, "role": role})
    assert r.status_code == 201, r.text


def signed_in(app, name) -> TestClient:
    c = client(app)
    r = c.post("/auth/login", json={"username": name, "password": PW})
    assert r.status_code == 200, r.text
    return c


def inst_json(name="Mine"):
    d = sample_institution().model_dump(mode="json")
    d["name"] = name
    return d


# ---------------------------------------------------------------- the pieces

def test_passwords_are_salted_hashes_and_verify():
    a, b = authlib.hash_password(PW), authlib.hash_password(PW)
    assert a != b and PW not in a
    assert authlib.verify_password(PW, a) and not authlib.verify_password(PW + "x", a)
    assert not authlib.verify_password(PW, "garbage") and not authlib.verify_password(PW, "md5$1$2$3$4$5")


def test_username_and_password_rules():
    assert authlib.normalise_username("  Dr.Rao@College ") == "dr.rao@college"
    for bad in ("ab", "-lead", "has space", "x" * 70, ""):
        with pytest.raises(ValueError):
            authlib.normalise_username(bad)
    with pytest.raises(ValueError):
        authlib.check_password_strength("short")


def test_throttle_locks_then_releases():
    now = [0.0]
    t = authlib.LoginThrottle(limit=3, lock_s=60, clock=lambda: now[0])
    for _ in range(2):
        t.failed("k")
    assert t.wait_s("k") == 0
    t.failed("k")
    assert 59 <= t.wait_s("k") <= 60
    assert t.wait_s("other") == 0
    now[0] = 61
    assert t.wait_s("k") == 0
    t.failed("k")  # the count restarts after the lock ran out
    assert t.wait_s("k") == 0
    t.failed("k"); t.failed("k")
    assert t.wait_s("k") > 0
    t.succeeded("k")
    assert t.wait_s("k") == 0


# ---------------------------------------------------------------- signing in

def test_everything_but_the_front_door_needs_a_login(app):
    with client(app) as c:
        assert c.get("/health").status_code == 200
        st = c.get("/auth/status").json()
        assert st == {"auth": True, "setup_needed": True, "user": None}
        for method, path in [("get", "/institutions"), ("get", "/institutions/template"), ("post", "/institutions/check"),
                             ("get", "/jobs/x"), ("get", "/users")]:
            assert getattr(c, method)(path).status_code == 401, path


def test_first_run_setup_creates_one_admin_and_signs_in(app):
    with client(app) as c:
        assert c.post("/auth/setup", json={"username": "Root", "password": "short"}).status_code == 422
        assert c.post("/auth/setup", json={"username": "no spaces", "password": PW}).status_code == 422
        r = c.post("/auth/setup", json={"username": "Root", "password": PW})
        assert r.status_code == 201 and r.json()["role"] == "admin" and r.json()["username"] == "root"
        assert c.get("/auth/status").json()["user"]["username"] == "root"
        assert c.get("/institutions").status_code == 200  # the cookie works
        assert client(app).post("/auth/setup", json={"username": "evil", "password": PW}).status_code == 409


def test_login_logout_and_wrong_password(app, admin):
    c = client(app)
    assert c.post("/auth/login", json={"username": "root", "password": "nope-nope"}).status_code == 401
    assert c.post("/auth/login", json={"username": "ghost", "password": PW}).status_code == 401
    assert c.get("/institutions").status_code == 401
    r = c.post("/auth/login", json={"username": "ROOT", "password": PW})
    assert r.status_code == 200
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=lax" in r.headers["set-cookie"].lower()
    assert c.get("/institutions").status_code == 200
    token = c.cookies.get("aa_session")
    assert c.post("/auth/logout").status_code == 204
    assert c.get("/institutions").status_code == 401
    assert client(app).get("/institutions", headers={"Authorization": f"Bearer {token}"}).status_code == 401  # revoked


def test_the_database_never_holds_the_token_or_the_password(app, admin, tmp_path):
    token = admin.cookies.get("aa_session")
    dump = b"".join(p.read_bytes() for p in tmp_path.glob("t.db*"))
    assert token.encode() not in dump and PW.encode() not in dump


def test_guessing_is_slowed_down(app, admin):
    c = client(app)
    for _ in range(5):
        assert c.post("/auth/login", json={"username": "root", "password": "wrong-one"}).status_code == 401
    assert c.post("/auth/login", json={"username": "root", "password": PW}).status_code == 429  # even the right one waits


def test_bearer_token_works_for_scripts(app, admin):
    token = admin.cookies.get("aa_session")
    assert client(app).get("/institutions", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_changing_your_password_signs_everyone_out(app, admin):
    other = signed_in(app, "root")
    assert admin.post("/auth/password", json={"current": "wrong", "new": "another long one"}).status_code == 403
    assert admin.post("/auth/password", json={"current": PW, "new": "short"}).status_code == 422
    assert admin.post("/auth/password", json={"current": PW, "new": "another long one"}).status_code == 204
    assert other.get("/institutions").status_code == 401
    assert client(app).post("/auth/login", json={"username": "root", "password": "another long one"}).status_code == 200


# ---------------------------------------------------------------- users

def test_only_admins_manage_users_and_the_last_admin_stays(app, admin):
    add_user(admin, "bob")
    bob = signed_in(app, "bob")
    assert bob.get("/users").status_code == 403
    assert bob.post("/users", json={"username": "eve", "password": PW}).status_code == 403
    assert admin.post("/users", json={"username": "bob", "password": PW}).status_code == 409  # taken
    users = {u["username"]: u for u in admin.get("/users").json()}
    assert set(users) == {"root", "bob"}
    root_id = users["root"]["id"]
    assert admin.patch(f"/users/{root_id}", json={"role": "member"}).status_code == 409
    assert admin.patch(f"/users/{root_id}", json={"disabled": True}).status_code == 409
    assert admin.delete(f"/users/{root_id}").status_code == 409
    add_user(admin, "second", "admin")
    assert admin.patch(f"/users/{root_id}", json={"role": "member"}).status_code == 200  # no longer the last one


def test_disabling_a_user_ends_their_session(app, admin):
    add_user(admin, "bob")
    bob = signed_in(app, "bob")
    bob_id = next(u["id"] for u in admin.get("/users").json() if u["username"] == "bob")
    assert admin.patch(f"/users/{bob_id}", json={"disabled": True}).status_code == 200
    assert bob.get("/institutions").status_code == 401
    assert client(app).post("/auth/login", json={"username": "bob", "password": PW}).status_code == 401
    assert admin.patch(f"/users/{bob_id}", json={"disabled": False, "password": "a fresh password"}).status_code == 200
    assert client(app).post("/auth/login", json={"username": "bob", "password": "a fresh password"}).status_code == 200


# ---------------------------------------------------------------- who sees what

def test_an_institution_belongs_to_its_creator_until_shared(app, admin):
    add_user(admin, "bob"); add_user(admin, "carol")
    bob, carol = signed_in(app, "bob"), signed_in(app, "carol")
    iid = bob.post("/institutions", json=inst_json("Bob's")).json()["id"]
    assert [i["name"] for i in bob.get("/institutions").json()] == ["Bob's"]
    assert carol.get("/institutions").json() == []
    assert iid in {i["id"] for i in admin.get("/institutions").json()}  # administrators see everything
    for call in (lambda: carol.get(f"/institutions/{iid}"), lambda: carol.put(f"/institutions/{iid}", json=inst_json()),
                 lambda: carol.delete(f"/institutions/{iid}"), lambda: carol.post(f"/institutions/{iid}/solve"),
                 lambda: carol.get(f"/institutions/{iid}/workbook.xlsx"), lambda: carol.get(f"/institutions/{iid}/members")):
        assert call().status_code == 404  # not even revealed to exist
    assert carol.get("/institutions/nope").status_code == 404


def test_viewer_editor_owner_levels(app, admin):
    add_user(admin, "bob"); add_user(admin, "vivek"); add_user(admin, "edith")
    bob, vivek, ed = signed_in(app, "bob"), signed_in(app, "vivek"), signed_in(app, "edith")
    iid = bob.post("/institutions", json=inst_json()).json()["id"]
    assert bob.put(f"/institutions/{iid}/members/vivek", json={"role": "viewer"}).status_code == 200
    assert bob.put(f"/institutions/{iid}/members/edith", json={"role": "editor"}).status_code == 200
    members = {m["username"]: m["role"] for m in bob.get(f"/institutions/{iid}/members").json()}
    assert members == {"bob": "owner", "vivek": "viewer", "edith": "editor"}

    jid = bob.post(f"/institutions/{iid}/solve", json={"time_limit_s": 4}).json()["job_id"]
    for _ in range(60):
        if bob.get(f"/jobs/{jid}").json()["status"] in ("done", "failed"):
            break
        time.sleep(0.5)
    tt = bob.get(f"/jobs/{jid}/timetable").json()

    # a viewer can look and export, but not change anything
    assert vivek.get(f"/institutions/{iid}").status_code == 200
    assert vivek.get(f"/jobs/{jid}/timetable").status_code == 200
    assert vivek.get(f"/jobs/{jid}/export.pdf").status_code == 200
    assert vivek.get(f"/jobs/{jid}/views/batch/MG-UG1").status_code == 200
    assert vivek.post(f"/institutions/{iid}/validate", json=tt).status_code == 200
    assert vivek.put(f"/institutions/{iid}", json=inst_json("x")).status_code == 403
    assert vivek.post(f"/institutions/{iid}/solve").status_code == 403
    assert vivek.put(f"/jobs/{jid}/timetable", json=tt).status_code == 403
    assert vivek.delete(f"/institutions/{iid}").status_code == 403
    assert vivek.get(f"/institutions/{iid}/members").status_code == 403

    # an editor can change data and timetables, but not delete or share
    assert ed.put(f"/institutions/{iid}", json=inst_json("Renamed")).status_code == 200
    assert ed.put(f"/jobs/{jid}/timetable", json=tt).status_code == 200
    assert ed.post(f"/institutions/{iid}/solve", json={"time_limit_s": 4}).status_code == 202
    assert ed.delete(f"/institutions/{iid}").status_code == 403
    assert ed.put(f"/institutions/{iid}/members/vivek", json={"role": "owner"}).status_code == 403

    # revoking access takes effect at once
    assert bob.delete(f"/institutions/{iid}/members/vivek").status_code == 200
    assert vivek.get(f"/institutions/{iid}").status_code == 404
    assert vivek.get(f"/jobs/{jid}/timetable").status_code == 404
    assert bob.delete(f"/institutions/{iid}").status_code == 204


def test_an_institution_always_keeps_an_owner(app, admin):
    add_user(admin, "bob"); add_user(admin, "edith")
    bob = signed_in(app, "bob")
    iid = bob.post("/institutions", json=inst_json()).json()["id"]
    assert bob.put(f"/institutions/{iid}/members/bob", json={"role": "editor"}).status_code == 409
    assert bob.delete(f"/institutions/{iid}/members/bob").status_code == 409
    assert bob.put(f"/institutions/{iid}/members/ghost", json={"role": "viewer"}).status_code == 404
    assert bob.put(f"/institutions/{iid}/members/edith", json={"role": "owner"}).status_code == 200
    assert bob.put(f"/institutions/{iid}/members/bob", json={"role": "editor"}).status_code == 200  # now edith is an owner too
    assert bob.put(f"/institutions/{iid}/members/edith", json={"role": "viewer"}).status_code == 403  # bob is only an editor now


def test_uploads_and_imports_make_you_the_owner(app, admin, tmp_path):
    from automated_academics.excel_io import export_workbook
    add_user(admin, "bob")
    bob = signed_in(app, "bob")
    path = tmp_path / "i.xlsx"
    export_workbook(sample_institution(), path)
    r = bob.post("/institutions/upload", files={"file": ("i.xlsx", path.read_bytes())})
    assert r.status_code == 201
    assert [i["id"] for i in bob.get("/institutions").json()] == [r.json()["id"]]


def test_deleting_an_institution_clears_its_sharing(app, admin, tmp_path):
    add_user(admin, "bob")
    bob = signed_in(app, "bob")
    iid = bob.post("/institutions", json=inst_json()).json()["id"]
    assert bob.delete(f"/institutions/{iid}").status_code == 204
    assert Store(str(tmp_path / "t.db")).list_members(iid) == []


# ---------------------------------------------------------------- two people editing

def test_a_stale_save_is_refused_instead_of_overwriting(app, admin):
    add_user(admin, "bob")
    bob = signed_in(app, "bob")
    iid = bob.post("/institutions", json=inst_json("v0")).json()["id"]
    assert admin.get(f"/institutions/{iid}").status_code == 200  # the administrator is not a member but sees everything
    r = bob.get(f"/institutions/{iid}")
    v0 = r.headers["etag"]
    assert bob.put(f"/institutions/{iid}", json=inst_json("v1"), headers={"If-Match": v0}).status_code == 200
    # a second person still holding v0 tries to save
    r = admin.put(f"/institutions/{iid}", json=inst_json("theirs"), headers={"If-Match": v0})
    assert r.status_code == 409 and r.json()["detail"]["conflict"] is True
    assert bob.get(f"/institutions/{iid}").json()["name"] == "v1"  # nothing was overwritten
    v1 = bob.get(f"/institutions/{iid}").headers["etag"]
    assert v1 != v0
    assert admin.put(f"/institutions/{iid}", json=inst_json("theirs"), headers={"If-Match": v1}).status_code == 200


def test_pin_only_changes_also_count_as_changes(app, admin):
    inst = inst_json()
    iid = admin.post("/institutions", json=inst).json()["id"]
    v0 = admin.get(f"/institutions/{iid}").headers["etag"]
    inst["pins"] = [{"offering_id": "O-MDC101", "session_index": 0, "day": 0, "start": 0, "room_id": None}]
    r = admin.put(f"/institutions/{iid}", json=inst, headers={"If-Match": v0})
    assert r.status_code == 200 and r.headers["etag"] != v0
    assert admin.put(f"/institutions/{iid}", json=inst, headers={"If-Match": v0}).status_code == 409


def test_saving_without_if_match_still_works_and_junk_is_rejected(app, admin):
    iid = admin.post("/institutions", json=inst_json()).json()["id"]
    assert admin.put(f"/institutions/{iid}", json=inst_json("a")).status_code == 200
    assert admin.put(f"/institutions/{iid}", json=inst_json("b"), headers={"If-Match": "abc"}).status_code == 400


def test_unchanged_saves_keep_the_version(app, admin):
    inst = inst_json()
    iid = admin.post("/institutions", json=inst).json()["id"]
    v0 = admin.get(f"/institutions/{iid}").headers["etag"]
    assert admin.put(f"/institutions/{iid}", json=inst, headers={"If-Match": v0}).headers["etag"] == v0


# ---------------------------------------------------------------- the single-user app is unchanged

def test_with_sign_in_off_nothing_changes(tmp_path):
    with TestClient(create_app(str(tmp_path / "t.db"), auth=False)) as c:
        assert c.get("/auth/status").json() == {"auth": False, "setup_needed": False, "user": None}
        iid = c.post("/institutions", json=inst_json()).json()["id"]
        assert c.get("/institutions").json()[0]["id"] == iid
        assert c.post("/auth/login", json={"username": "a", "password": PW}).status_code == 409
        assert c.get("/users").status_code == 403  # no accounts to manage
        assert c.delete(f"/institutions/{iid}").status_code == 204


def test_old_databases_gain_the_new_tables(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    with sqlite3.connect(db) as c:
        c.executescript("CREATE TABLE institutions (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL);"
                        "CREATE TABLE jobs (id TEXT PRIMARY KEY, institution_id TEXT, status TEXT, time_limit_s REAL, created_at TEXT, finished_at TEXT, error TEXT, timetable TEXT);")
        c.execute("INSERT INTO institutions VALUES ('old1', 'Old', '2026-01-01T00:00:00', ?)", (sample_institution().model_dump_json(),))
    store = Store(str(db))
    assert store.institution_version("old1") == 0
    assert store.count_users() == 0 and [i["id"] for i in store.list_institutions()] == ["old1"]


def test_a_disabled_account_is_refused_even_if_its_session_survived(app, admin, tmp_path):
    add_user(admin, "bob")
    bob = signed_in(app, "bob")
    bob_id = next(u["id"] for u in admin.get("/users").json() if u["username"] == "bob")
    Store(str(tmp_path / "t.db")).update_user(bob_id, disabled=1)  # behind the API's back: the session row is still there
    assert bob.get("/institutions").status_code == 401


def test_an_administrator_resetting_a_password_signs_that_person_out(app, admin):
    add_user(admin, "bob")
    bob = signed_in(app, "bob")
    bob_id = next(u["id"] for u in admin.get("/users").json() if u["username"] == "bob")
    assert bob.get("/institutions").status_code == 200
    assert admin.patch(f"/users/{bob_id}", json={"password": "a brand new one"}).status_code == 200
    assert bob.get("/institutions").status_code == 401
