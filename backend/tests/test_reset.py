import re
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from automated_academics.api import create_app
from automated_academics.mail import SmtpMailer, normalise_email

PW = "correct horse battery"
NEW = "a brand new passphrase"


class Outbox:
    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []
        self.fail = False

    def send(self, to, subject, body):
        if self.fail:
            raise OSError("mail server down")
        self.sent.append((to, subject, body))


def wait_for(box, n, secs=3.0):
    end = time.time() + secs
    while len(box.sent) < n and time.time() < end:
        time.sleep(0.02)
    return len(box.sent) >= n


def token_in(body):
    m = re.search(r"/#/reset/([A-Za-z0-9_-]+)", body)
    assert m, body
    return m.group(1)


@pytest.fixture
def world(tmp_path):
    box = Outbox()
    path = str(tmp_path / "t.db")
    app = create_app(path, auth=True, mailer=box, public_url="https://tt.example.edu/")
    with TestClient(app) as admin:
        admin.post("/auth/setup", json={"username": "root", "password": PW})
        admin.post("/users", json={"username": "amy", "password": PW, "display_name": "Amy A", "email": "Amy@Example.edu"})
        admin.post("/users", json={"username": "noemail", "password": PW})
        yield {"app": app, "admin": admin, "box": box, "path": path, "anon": TestClient(app)}


def users(admin):
    return {u["username"]: u for u in admin.get("/users").json()}


def test_it_is_off_without_a_mail_server(tmp_path):
    with TestClient(create_app(str(tmp_path / "t.db"), auth=True)) as c:
        assert c.get("/auth/status").json()["reset_enabled"] is False
        assert c.post("/auth/forgot", json={"who": "root"}).status_code == 409
        assert c.post("/auth/reset", json={"token": "x", "new": NEW}).status_code == 409


def test_it_is_off_without_a_public_address(tmp_path):
    """Links are never built from the request, so without a configured address there is no reset."""
    app = create_app(str(tmp_path / "t.db"), auth=True, mailer=Outbox(), public_url="")
    with TestClient(app) as c:
        assert c.get("/auth/status").json()["reset_enabled"] is False


def test_status_says_it_is_on(world):
    assert world["anon"].get("/auth/status").json()["reset_enabled"] is True


def test_the_whole_round_trip_by_username_or_email(world):
    box, anon, admin = world["box"], world["anon"], world["admin"]
    r = anon.post("/auth/forgot", json={"who": "Amy"})
    assert r.status_code == 202 and "on its way" in r.json()["message"]
    assert wait_for(box, 1)
    to, subject, body = box.sent[0]
    assert to == "amy@example.edu" and "new password" in subject.lower() and "Amy A" in body
    assert "https://tt.example.edu/#/reset/" in body and "60 minutes" in body
    token = token_in(body)
    assert len(token) >= 40

    # the old sign-in works until the link is used
    amy = TestClient(world["app"])
    assert amy.post("/auth/login", json={"username": "amy", "password": PW}).status_code == 200
    assert anon.post("/auth/reset", json={"token": token, "new": NEW}).status_code == 200
    assert amy.get("/institutions").status_code == 401  # signed out everywhere
    assert TestClient(world["app"]).post("/auth/login", json={"username": "amy", "password": PW}).status_code == 401
    assert TestClient(world["app"]).post("/auth/login", json={"username": "amy", "password": NEW}).status_code == 200
    # one use only
    again = anon.post("/auth/reset", json={"token": token, "new": "yet another passphrase"})
    assert again.status_code == 400
    # and the e-mail address works as the name too
    assert users(admin)["amy"]["email"] == "amy@example.edu"


def test_by_email_address(tmp_path):
    box = Outbox()
    with TestClient(create_app(str(tmp_path / "t.db"), auth=True, mailer=box, public_url="https://x.org")) as c:
        c.post("/auth/setup", json={"username": "root", "password": PW})
        c.post("/users", json={"username": "bob", "password": PW, "email": "bob@x.org"})
        assert TestClient(c.app).post("/auth/forgot", json={"who": "BOB@x.org"}).status_code == 202
        assert wait_for(box, 1) and box.sent[0][0] == "bob@x.org"


def test_the_answer_never_reveals_whether_an_account_exists(world):
    anon, box = world["anon"], world["box"]
    answers = [anon.post("/auth/forgot", json={"who": w}) for w in ("amy", "nobody-here", "noemail", "x@nowhere.org")]
    assert {(a.status_code, a.text) for a in answers} == {(202, answers[0].text)}
    wait_for(box, 1)
    time.sleep(0.2)
    assert [m[0] for m in box.sent] == ["amy@example.edu"]  # only the real one got mail


def test_a_disabled_account_gets_nothing_and_cannot_use_an_old_link(world):
    admin, anon, box = world["admin"], world["anon"], world["box"]
    anon.post("/auth/forgot", json={"who": "amy"})
    assert wait_for(box, 1)
    token = token_in(box.sent[0][2])
    admin.patch(f"/users/{users(admin)['amy']['id']}", json={"disabled": True})
    assert anon.post("/auth/reset", json={"token": token, "new": NEW}).status_code == 400
    n = len(box.sent)
    anon.post("/auth/forgot", json={"who": "amy"})
    time.sleep(0.3)
    assert len(box.sent) == n


def test_links_expire_and_a_newer_one_replaces_the_older(world):
    anon, box = world["anon"], world["box"]
    anon.post("/auth/forgot", json={"who": "amy"})
    assert wait_for(box, 1)
    first = token_in(box.sent[0][2])
    db = sqlite3.connect(world["path"])
    db.execute("UPDATE password_resets SET created_at='2000-01-01T00:00:00.000000+00:00'")  # the 60-second cool-down is over
    db.commit()
    anon.post("/auth/forgot", json={"who": "amy"})
    assert wait_for(box, 2)
    second = token_in(box.sent[1][2])
    assert first != second
    assert anon.post("/auth/reset", json={"token": first, "new": NEW}).status_code == 400  # replaced
    db.execute("UPDATE password_resets SET expires_at='2000-01-01T00:00:00.000000+00:00'")
    db.commit()
    db.close()
    assert anon.post("/auth/reset", json={"token": second, "new": NEW}).status_code == 400  # expired


def test_asking_twice_at_once_sends_one_email(world):
    anon, box = world["anon"], world["box"]
    anon.post("/auth/forgot", json={"who": "amy"})
    anon.post("/auth/forgot", json={"who": "amy"})
    assert wait_for(box, 1)
    time.sleep(0.3)
    assert len(box.sent) == 1


def test_a_weak_new_password_is_refused_and_the_link_survives(world):
    anon, box = world["anon"], world["box"]
    anon.post("/auth/forgot", json={"who": "amy"})
    assert wait_for(box, 1)
    token = token_in(box.sent[0][2])
    assert anon.post("/auth/reset", json={"token": token, "new": "short"}).status_code == 422
    assert anon.post("/auth/reset", json={"token": token, "new": NEW}).status_code == 200


def test_requests_and_wrong_links_are_throttled(world):
    anon = world["anon"]
    codes = [anon.post("/auth/forgot", json={"who": f"ghost{i}"}).status_code for i in range(7)]
    assert codes[:5] == [202] * 5 and codes[5:] == [429, 429]
    other = TestClient(world["app"])
    bad = [other.post("/auth/reset", json={"token": f"nope{i}", "new": NEW}).status_code for i in range(12)]
    assert bad[:10] == [400] * 10 and bad[10:] == [429, 429]


def test_only_a_hash_of_the_link_is_stored_and_the_log_has_no_token_or_password(world):
    anon, box = world["anon"], world["box"]
    anon.post("/auth/forgot", json={"who": "amy"})
    assert wait_for(box, 1)
    token = token_in(box.sent[0][2])
    anon.post("/auth/reset", json={"token": token, "new": NEW})
    world["admin"].post("/users", json={"username": "kept", "password": PW, "email": "k@x.org"})
    anon.post("/auth/forgot", json={"who": "kept"})
    assert wait_for(box, 2)
    live = token_in(box.sent[1][2])
    dump = "\n".join(sqlite3.connect(world["path"]).iterdump())
    assert token not in dump and live not in dump and NEW not in dump
    log = " ".join(e["summary"] + str(e["detail"]) for e in world["admin"].get("/audit", params={"limit": 200}).json())
    assert "password-reset" in log and "chose a new password" in log
    assert token not in log and live not in log and NEW not in log


def test_a_mail_server_failure_is_invisible_to_the_person_asking(world):
    world["box"].fail = True
    r = world["anon"].post("/auth/forgot", json={"who": "amy"})
    assert r.status_code == 202
    time.sleep(0.3)
    assert world["box"].sent == []


# ---------- e-mail addresses on accounts ----------

def test_only_an_administrator_manages_addresses_and_they_are_unique(world):
    admin = world["admin"]
    amy = users(admin)["amy"]
    assert amy["email"] == "amy@example.edu"  # stored lower-case
    other = users(admin)["noemail"]["id"]
    assert admin.patch(f"/users/{other}", json={"email": "AMY@example.edu"}).status_code == 409
    assert admin.patch(f"/users/{other}", json={"email": "not an address"}).status_code == 422
    assert admin.patch(f"/users/{other}", json={"email": "new@example.edu"}).status_code == 200
    assert admin.patch(f"/users/{other}", json={"email": ""}).status_code == 200  # removing it is allowed
    assert admin.patch(f"/users/{amy['id']}", json={"email": "amy@example.edu"}).status_code == 200  # same address, same person
    assert admin.post("/users", json={"username": "dup", "password": PW, "email": "amy@example.edu"}).status_code == 409
    amy_client = TestClient(world["app"])
    amy_client.post("/auth/login", json={"username": "amy", "password": PW})
    assert amy_client.get("/users").status_code == 403


def test_an_old_database_gains_the_column_and_table(tmp_path):
    path = str(tmp_path / "old.db")
    with TestClient(create_app(path, auth=True)) as c:
        c.post("/auth/setup", json={"username": "root", "password": PW})
    db = sqlite3.connect(path)
    db.execute("ALTER TABLE users DROP COLUMN email")
    db.execute("DROP TABLE password_resets")
    db.commit()
    db.close()
    with TestClient(create_app(path, auth=True, mailer=Outbox(), public_url="https://x.org")) as c:
        assert c.post("/auth/login", json={"username": "root", "password": PW}).status_code == 200
        assert c.get("/users").json()[0]["email"] == ""


def test_mail_settings_from_the_environment(monkeypatch):
    for k in ("AA_SMTP_HOST", "AA_SMTP_PORT", "AA_SMTP_USER", "AA_SMTP_PASSWORD", "AA_SMTP_FROM", "AA_SMTP_SECURITY"):
        monkeypatch.delenv(k, raising=False)
    assert SmtpMailer.from_env() is None
    monkeypatch.setenv("AA_SMTP_HOST", "smtp.example.edu")
    with pytest.raises(ValueError, match="AA_SMTP_FROM"):
        SmtpMailer.from_env()
    monkeypatch.setenv("AA_SMTP_FROM", "timetable@example.edu")
    m = SmtpMailer.from_env()
    assert (m.host, m.port, m.security, m.sender) == ("smtp.example.edu", 587, "starttls", "timetable@example.edu")
    monkeypatch.setenv("AA_SMTP_SECURITY", "ssl")
    assert SmtpMailer.from_env().port == 465
    monkeypatch.setenv("AA_SMTP_SECURITY", "plain")
    with pytest.raises(ValueError):
        SmtpMailer.from_env()


def test_email_normalising():
    assert normalise_email("  A@B.org ") == "a@b.org" and normalise_email("") == ""
    for bad in ("a", "a@b", "a b@c.org", "@x.org", "a@@x.org"):
        with pytest.raises(ValueError):
            normalise_email(bad)
