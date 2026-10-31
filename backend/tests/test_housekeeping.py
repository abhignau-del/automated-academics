"""Housekeeping: trimming the activity log, backing up and restoring all the data, and "last opened" on published links."""

import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from automated_academics import api as api_mod
from automated_academics import store as store_mod
from automated_academics.api import _env_days, create_app
from automated_academics.store import Store
from automated_academics.synthetic import sample_institution

PW = "correct horse battery"


def ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="microseconds")


def seed(monkeypatch, path, ages):
    """Write activity-log entries as if they were made that many days ago."""
    s = Store(path)
    with monkeypatch.context() as m:
        for d in ages:
            m.setattr(store_mod, "_now", lambda d=d: ago(d))
            s.add_audit(None, "seed", "x.y", f"seed entry {d} days old")
    return s


def wait_done(c, jid):
    for _ in range(80):
        if c.get(f"/jobs/{jid}").json()["status"] in ("done", "failed"):
            return
        time.sleep(0.5)


def sign_in(app, name):
    c = TestClient(app)
    assert c.post("/auth/login", json={"username": name, "password": PW}).status_code == 200
    return c


@pytest.fixture
def shared(tmp_path):
    """Shared mode with an administrator and an ordinary member."""
    path = str(tmp_path / "t.db")
    app = create_app(path, auth=True)
    with TestClient(app) as admin:
        admin.post("/auth/setup", json={"username": "root", "password": PW})
        admin.post("/users", json={"username": "owen", "password": PW})
        yield {"app": app, "path": path, "admin": admin, "owen": sign_in(app, "owen"), "dir": tmp_path}


def entries(c, **q):
    return c.get("/audit", params={"limit": 200, **q}).json()


# ---------- trimming the activity log ----------

def test_deleting_old_entries_keeps_the_recent_ones_and_the_chain_still_verifies(monkeypatch, tmp_path):
    path = str(tmp_path / "t.db")
    seed(monkeypatch, path, [400, 300, 200, 10, 5])
    app = create_app(path, auth=True)
    with TestClient(app) as admin:
        admin.post("/auth/setup", json={"username": "root", "password": PW})
        before = entries(admin)
        r = admin.post("/audit/prune", json={"days": 90})
        assert r.status_code == 200 and r.json()["removed"] == 3 and r.json()["verify"]["ok"] is True
        after = entries(admin)
        assert not [e for e in after if "days old" in e["summary"] and int(e["summary"].split()[2]) > 90]
        assert len(after) == len(before) - 3 + 1  # three gone, and a line saying so
        assert after[0]["action"] == "audit.pruned" and "deleted 3" in after[0]["summary"]
        v = admin.get("/audit/verify").json()
        assert v["ok"] is True and v["pruned"]["removed"] == 3 and v["checked"] == len(after)


def test_the_newest_entry_is_never_deleted_and_logging_carries_on_from_it(monkeypatch, tmp_path):
    path = str(tmp_path / "t.db")
    s = seed(monkeypatch, path, [400, 300])
    assert s.prune_audit(30) == 1
    assert [e["summary"] for e in s.list_audit()] == ["seed entry 300 days old"]
    assert s.prune_audit(30) == 0  # nothing more it may delete
    s.add_audit(None, "seed", "x.y", "a new one")
    v = s.verify_audit()
    assert v["ok"] is True and v["checked"] == 2 and v["pruned"]["removed"] == 1


def test_trimming_twice_adds_up_and_a_trimmed_log_still_notices_tampering(monkeypatch, tmp_path):
    path = str(tmp_path / "t.db")
    s = seed(monkeypatch, path, [500, 400, 300, 200, 100, 5, 4])
    assert s.prune_audit(450) == 1 and s.prune_audit(150) == 3
    assert s.verify_audit()["pruned"]["removed"] == 4 and s.verify_audit()["ok"] is True
    rows = s.list_audit()
    with sqlite3.connect(path) as c:
        c.execute("UPDATE audit SET summary='edited' WHERE seq=?", (rows[0]["seq"],))
    bad = s.verify_audit()
    assert bad["ok"] is False and bad["first_bad"] == rows[0]["seq"]
    with sqlite3.connect(path) as c:  # put it back, then tamper with the starting point instead
        c.execute("UPDATE audit SET summary=? WHERE seq=?", (f"seed entry {4} days old", rows[0]["seq"]))
        c.execute("UPDATE audit_anchor SET hash=?", ("f" * 64,))
    broken = s.verify_audit()
    assert broken["ok"] is False and broken["first_bad"] == min(r["seq"] for r in rows)


def test_only_an_administrator_may_trim_and_not_below_thirty_days(shared):
    assert shared["owen"].post("/audit/prune", json={"days": 90}).status_code == 403
    for days in (0, 29, 3651):
        assert shared["admin"].post("/audit/prune", json={"days": days}).status_code == 422
    assert shared["admin"].post("/audit/prune", json={"days": 30}).status_code == 200


def test_trimming_needs_shared_mode(tmp_path):
    with TestClient(create_app(str(tmp_path / "t.db"), auth=False)) as c:
        assert c.post("/audit/prune", json={"days": 90}).status_code == 409


def test_a_keep_days_setting_trims_by_itself_about_once_a_day(monkeypatch, tmp_path):
    path = str(tmp_path / "t.db")
    seed(monkeypatch, path, [400, 300, 5])
    app = create_app(path, auth=True, audit_keep_days=60)
    with TestClient(app) as admin:
        admin.post("/auth/setup", json={"username": "root", "password": PW})  # the first thing logged tidies the log
        log = entries(admin)
        left = [e["summary"] for e in log if e["actor"] == "seed"]
        assert left == ["seed entry 5 days old"]  # the 400- and 300-day-old ones went
        tidy = [e for e in log if e["action"] == "audit.pruned"]
        assert len(tidy) == 1 and tidy[0]["actor"] == "system" and "deleted 2" in tidy[0]["summary"]
        admin.post("/users", json={"username": "owen", "password": PW})
        assert len([e for e in entries(admin) if e["action"] == "audit.pruned"]) == 1  # not again within the day
        seed(monkeypatch, path, [500])  # an old entry that turns up later is left alone until the next day's check
        admin.post("/users", json={"username": "zed", "password": PW})
        assert [e for e in entries(admin) if e["actor"] == "seed" and "500 days" in e["summary"]]
        assert admin.get("/audit/verify").json()["ok"] is True


def test_the_keep_days_setting_is_read_from_the_environment_and_sensible_values_only(monkeypatch):
    for raw, want in [("", None), ("abc", None), ("10", None), ("29", None), ("30", 30), ("365", 365), (" 90 ", 90)]:
        monkeypatch.setenv("AA_AUDIT_KEEP_DAYS", raw)
        assert _env_days("AA_AUDIT_KEEP_DAYS") == want, raw
    monkeypatch.delenv("AA_AUDIT_KEEP_DAYS")
    assert _env_days("AA_AUDIT_KEEP_DAYS") is None


# ---------- backup ----------

def institution_names(path):
    with sqlite3.connect(path) as c:
        return sorted(r[0] for r in c.execute("SELECT name FROM institutions"))


def make_institution(c, name):
    data = sample_institution().model_dump(mode="json")
    data["name"] = name
    r = c.post("/institutions", json=data)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_an_administrator_can_download_a_backup_without_sessions(shared, tmp_path):
    make_institution(shared["owen"], "First")
    r = shared["admin"].get("/admin/backup")
    assert r.status_code == 200 and "automated-academics-backup-" in r.headers["content-disposition"] and r.headers["content-disposition"].endswith('.db"')
    copy = tmp_path / "copy.db"
    copy.write_bytes(r.content)
    assert institution_names(copy) == ["First"]
    with sqlite3.connect(copy) as c:
        assert c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
    with sqlite3.connect(shared["path"]) as c:
        assert c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] >= 1  # the live data still has them
    assert [e for e in entries(shared["admin"]) if e["action"] == "backup.downloaded"]


def test_a_backup_is_for_administrators_only(shared):
    assert shared["owen"].get("/admin/backup").status_code == 403
    assert TestClient(shared["app"]).get("/admin/backup").status_code in (401, 403)


def test_the_single_user_app_can_back_up_without_signing_in(tmp_path):
    path = str(tmp_path / "t.db")
    with TestClient(create_app(path, auth=False)) as c:
        make_institution(c, "Mine")
        got = c.get("/admin/backup")
        assert got.status_code == 200
        (tmp_path / "b.db").write_bytes(got.content)
        assert institution_names(tmp_path / "b.db") == ["Mine"]


# ---------- restore ----------

def test_restoring_puts_back_the_backed_up_data_and_keeps_a_copy_of_what_was_there(shared):
    make_institution(shared["owen"], "Keep me")
    backup = shared["admin"].get("/admin/backup").content
    make_institution(shared["owen"], "Added later")
    assert institution_names(shared["path"]) == ["Added later", "Keep me"]
    r = shared["admin"].post("/admin/restore", content=backup)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["restored"] is True and body["summary"]["institutions"] == 1 and body["summary"]["people"] == 2
    assert institution_names(shared["path"]) == ["Keep me"]
    safety = shared["dir"] / "backups" / body["safety_copy"]
    assert safety.exists() and institution_names(safety) == ["Added later", "Keep me"]
    # everyone has to sign in again, and the accounts from the backup work
    assert shared["admin"].get("/institutions").status_code == 401
    again = sign_in(shared["app"], "root")
    assert [e for e in entries(again) if e["action"] == "backup.restored"]
    assert again.get("/audit/verify").json()["ok"] is True


def test_a_dry_run_only_says_what_the_backup_holds(shared):
    make_institution(shared["owen"], "Keep me")
    backup = shared["admin"].get("/admin/backup").content
    make_institution(shared["owen"], "Added later")
    r = shared["admin"].post("/admin/restore", params={"dry_run": True}, content=backup)
    assert r.status_code == 200 and r.json()["restored"] is False and r.json()["summary"]["institutions"] == 1
    assert institution_names(shared["path"]) == ["Added later", "Keep me"]
    assert not (shared["dir"] / "backups").exists()


def test_restore_refuses_things_that_are_not_backups(shared, monkeypatch):
    admin = shared["admin"]
    for junk in (b"", b"hello, this is not a database", b"SQLite format 3\x00" + b"\x00" * 200):
        r = admin.post("/admin/restore", content=junk)
        assert r.status_code == 422, (junk[:12], r.text)
    empty = shared["dir"] / "other.db"
    with sqlite3.connect(empty) as c:
        c.execute("CREATE TABLE something_else (x)")
    assert admin.post("/admin/restore", content=empty.read_bytes()).status_code == 422
    assert institution_names(shared["path"]) == []
    monkeypatch.setattr(api_mod, "MAX_RESTORE_BYTES", 100)
    assert admin.post("/admin/restore", content=b"x" * 500).status_code == 413


def test_restore_is_for_administrators_and_waits_for_a_timetable_being_generated(shared):
    backup = shared["admin"].get("/admin/backup").content
    assert shared["owen"].post("/admin/restore", content=backup).status_code == 403
    iid = make_institution(shared["owen"], "Busy")
    Store(shared["path"]).create_job(iid, 5)  # queued: a timetable is on its way
    r = shared["admin"].post("/admin/restore", content=backup)
    assert r.status_code == 409 and "being generated" in r.json()["detail"]
    assert institution_names(shared["path"]) == ["Busy"]


def test_a_backup_from_an_older_version_is_brought_up_to_date_when_restored(shared, tmp_path):
    old = tmp_path / "old.db"
    Store(old)
    with sqlite3.connect(old) as c:
        c.execute("ALTER TABLE publications DROP COLUMN last_opened_at")
        c.execute("ALTER TABLE publications DROP COLUMN no_class")
        c.execute("DROP TABLE audit_anchor")
        c.execute("DROP TABLE sessions")
        c.execute("DROP TABLE password_resets")
        c.execute("INSERT INTO institutions (id, name, created_at, data) VALUES ('x','From long ago','2020-01-01','{}')")
        c.commit()
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # a file taken from a running copy keeps recent rows in a side file
    r = shared["admin"].post("/admin/restore", content=old.read_bytes())
    assert r.status_code == 200, r.text
    with sqlite3.connect(shared["path"]) as c:
        cols = {row[1] for row in c.execute("PRAGMA table_info(publications)")}
        assert {"last_opened_at", "no_class"} <= cols
        assert c.execute("SELECT name FROM sqlite_master WHERE name='audit_anchor'").fetchone()
    assert institution_names(shared["path"]) == ["From long ago"]


def test_only_the_last_few_safety_copies_are_kept(tmp_path):
    path = str(tmp_path / "t.db")
    with TestClient(create_app(path, auth=False)) as c:
        make_institution(c, "Mine")
        backup = c.get("/admin/backup").content
        for _ in range(api_mod.KEEP_SAFETY_COPIES + 2):
            assert c.post("/admin/restore", content=backup).status_code == 200
    assert len(list((tmp_path / "backups").glob("before-restore-*.db"))) == api_mod.KEEP_SAFETY_COPIES


# ---------- last opened ----------

@pytest.fixture
def published(shared):
    owen = shared["owen"]
    iid = make_institution(owen, "Linked")
    jid = owen.post(f"/institutions/{iid}/solve", json={"time_limit_s": 4}).json()["job_id"]
    wait_done(owen, jid)
    r = owen.post(f"/institutions/{iid}/publications", json={"job_id": jid, "kinds": ["batch"]})
    assert r.status_code == 201, r.text
    return {**shared, "iid": iid, "jid": jid, "link": r.json(), "public": TestClient(shared["app"])}


def stamp(w):
    return next(p for p in w["owen"].get(f"/institutions/{w['iid']}/publications").json() if p["id"] == w["link"]["id"])["last_opened_at"]


def test_a_link_nobody_opened_says_so_and_opening_it_is_remembered(published):
    w = published
    assert w["link"]["last_opened_at"] is None and stamp(w) is None
    assert w["public"].get(f"/public/{w['link']['token']}").status_code == 200
    first = stamp(w)
    assert first and abs((datetime.now(timezone.utc) - datetime.fromisoformat(first)).total_seconds()) < 60


def test_opening_it_again_within_a_minute_does_not_write_again_but_later_does(published):
    w = published
    url = f"/public/{w['link']['token']}"
    w["public"].get(url)
    first = stamp(w)
    w["public"].get(url)
    w["public"].get(f"{url}/export.pdf")
    assert stamp(w) == first
    with sqlite3.connect(w["path"]) as c:
        c.execute("UPDATE publications SET last_opened_at=?", (ago(0.01),))  # a quarter of an hour ago
    w["public"].get(url)
    assert datetime.fromisoformat(stamp(w)) > datetime.now(timezone.utc) - timedelta(seconds=30)


def test_wrong_withdrawn_and_expired_links_do_not_count_as_opened(published):
    w = published
    assert w["public"].get("/public/not-a-real-token").status_code == 404
    assert stamp(w) is None
    with sqlite3.connect(w["path"]) as c:
        c.execute("UPDATE publications SET expires_at=?", (ago(1),))
    assert w["public"].get(f"/public/{w['link']['token']}").status_code == 410
    assert stamp(w) is None


def test_a_database_hiccup_while_noting_the_visit_does_not_break_the_page(published, monkeypatch):
    w = published

    def boom(self, pid):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(Store, "touch_publication", boom)
    assert w["public"].get(f"/public/{w['link']['token']}").status_code == 200


def test_only_people_who_can_manage_links_see_when_they_were_opened(published):
    w = published
    w["public"].get(f"/public/{w['link']['token']}")
    meta = w["public"].get(f"/public/{w['link']['token']}").json()
    assert "last_opened_at" not in meta and "last_opened_at" not in str(meta)


def test_new_entries_continue_the_chain_from_the_remembered_hash_if_the_log_is_empty(monkeypatch, tmp_path):
    path = str(tmp_path / "t.db")
    s = seed(monkeypatch, path, [400, 300])
    assert s.prune_audit(30) == 1
    with sqlite3.connect(path) as c:
        c.execute("DELETE FROM audit")
    s.add_audit(None, "seed", "x.y", "after emptying")
    v = s.verify_audit()
    assert v["ok"] is True and v["checked"] == 1
