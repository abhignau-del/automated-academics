"""A published link's calendar file (.ics) and the "what changed" notice."""

import re
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from automated_academics.api import create_app
from automated_academics.calendar_ics import weekday_of
from automated_academics.synthetic import sample_institution

PW = "correct horse battery"
TIMES = [{"start": f"{9 + i}:00".zfill(5), "end": f"{9 + i}:50".zfill(5)} for i in range(7)]


def wait_done(c, jid):
    for _ in range(80):
        if c.get(f"/jobs/{jid}").json()["status"] in ("done", "failed"):
            return
        time.sleep(0.5)


@pytest.fixture
def world(tmp_path):
    path = str(tmp_path / "t.db")
    app = create_app(path, auth=True)
    with TestClient(app) as admin:
        admin.post("/auth/setup", json={"username": "root", "password": PW})
        admin.post("/users", json={"username": "owen", "password": PW})
        owen = TestClient(app)
        owen.post("/auth/login", json={"username": "owen", "password": PW})
        data = sample_institution().model_dump(mode="json")
        data["calendar"]["times"] = TIMES
        iid = owen.post("/institutions", json=data).json()["id"]
        jid = owen.post(f"/institutions/{iid}/solve", json={"time_limit_s": 4}).json()["job_id"]
        wait_done(owen, jid)
        yield {"app": app, "iid": iid, "jid": jid, "owen": owen, "path": path, "public": TestClient(app), "data": data}


def link(w, **body):
    r = w["owen"].post(f"/institutions/{w['iid']}/publications", json={"job_id": w["jid"], "kinds": ["batch", "faculty"], **body})
    assert r.status_code == 201, r.text
    return r.json()


def ics(w, token, kind="batch", ident=None):
    ident = ident or w["data"]["batches"][0]["id"]
    return w["public"].get(f"/public/{token}/calendar.ics", params={"kind": kind, "id": ident})


def events(text):
    unfolded = text.replace("\r\n ", "").replace("\r\n", "\n")
    return [dict(re.findall(r"^([A-Z-]+)[;:](.*)$", block, re.M)) for block in unfolded.split("BEGIN:VEVENT")[1:]]


# ---------- the calendar file ----------

def test_a_class_calendar_has_one_weekly_event_per_session(world):
    token = link(world, term_start="2026-11-02", term_end="2027-02-27")["token"]  # a Monday to a Saturday
    bid = world["data"]["batches"][0]["id"]
    r = ics(world, token)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    assert r.headers["cache-control"] == "no-store" and r.headers["x-robots-tag"].startswith("noindex")
    text = r.text
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n") and "\n" not in text.replace("\r\n", "")
    expected = world["public"].get(f"/public/{token}/views/batch/{bid}").json()["sessions"]
    evs = events(text)
    assert len(evs) == len(expected) > 0
    first = expected[0]
    wanted = {(s["day"], s["start"]) for s in expected}
    for e in evs:
        assert e["RRULE"] == "FREQ=WEEKLY;UNTIL=20270227T235959"
        assert "T" in e["DTSTART"] and e["DTSTART"][:8] >= "20261102"
    # the first event of the first session lands on the right weekday and clock time
    by_time = {e["DTSTART"] for e in evs}
    hh = TIMES[first["start"]]["start"].replace(":", "")
    day = date(2026, 11, 2) + timedelta(days=weekday_of(world["data"]["calendar"]["day_names"], first["day"]))
    assert f"{day:%Y%m%d}T{hh}00" in by_time
    assert len(wanted) <= len(evs)
    assert all(e["UID"].endswith("@automated-academics") for e in evs) and len({e["UID"] for e in evs}) == len(evs)
    assert f"X-WR-CALNAME:{world['data']['name']}" in text


def test_no_line_is_longer_than_75_bytes_and_text_is_escaped(world):
    token = link(world, term_start="2026-11-02", term_end="2027-02-27")["token"]
    text = ics(world, token).text
    assert all(len(line.encode("utf-8")) <= 75 for line in text.split("\r\n"))
    from automated_academics.calendar_ics import _escape, _fold
    assert _escape("a,b;c\\d\ne") == "a\\,b\\;c\\\\d\\ne"
    folded = _fold("X:" + "é" * 80)
    assert all(len(x.encode("utf-8")) <= 75 for x in folded) and "".join(x.lstrip(" ") if i else x for i, x in enumerate(folded)) == "X:" + "é" * 80


def test_teachers_and_rooms_follow_the_views_the_link_offers(world):
    token = link(world, term_start="2026-11-02", term_end="2027-02-27")["token"]  # classes and teachers, not rooms
    fid = world["data"]["faculty"][0]["id"]
    assert ics(world, token, "faculty", fid).status_code == 200
    assert ics(world, token, "room", world["data"]["rooms"][0]["id"]).status_code == 404
    assert ics(world, token, "batch", "no-such-class").status_code == 404


def test_the_same_session_keeps_its_id_so_a_calendar_updates_instead_of_duplicating(world):
    token = link(world, term_start="2026-11-02", term_end="2027-02-27")["token"]
    first = {e["UID"] for e in events(ics(world, token).text)}
    again = {e["UID"] for e in events(ics(world, token).text)}
    assert first == again
    other = {e["UID"] for e in events(ics(world, token, "batch", world["data"]["batches"][1]["id"]).text)}
    assert not first & other


def test_a_term_that_starts_midweek_and_a_term_that_ends_early(world):
    # starting on a Wednesday, Monday sessions begin the following Monday
    token = link(world, term_start="2026-11-04", term_end="2027-02-27")["token"]
    for e in events(ics(world, token).text):
        d = datetime.strptime(e["DTSTART"][:8], "%Y%m%d").date()
        assert d >= date(2026, 11, 4)
    # a one-day term holds only the sessions on that weekday
    one = link(world, term_start="2026-11-04", term_end="2026-11-04")["token"]
    evs = events(ics(world, one).text)
    assert all(e["DTSTART"].startswith("20261104") for e in evs)
    assert len(evs) < len(events(ics(world, token).text))


def test_there_is_no_file_without_term_dates_or_lecture_times(world):
    plain = link(world)["token"]
    r = ics(world, plain)
    assert r.status_code == 404 and "term dates" in r.json()["detail"]
    # lecture times removed -> nothing to put in a calendar
    data = world["owen"].get(f"/institutions/{world['iid']}").json()
    data["calendar"]["times"] = []
    assert world["owen"].put(f"/institutions/{world['iid']}", json=data).status_code == 200
    token = link(world, term_start="2026-11-02", term_end="2027-02-27")["token"]
    r = ics(world, token)
    assert r.status_code == 404 and "lecture times" in r.json()["detail"]
    meta = world["public"].get(f"/public/{token}").json()
    assert meta["calendar_feed"] is False


def test_the_page_says_when_a_calendar_is_available(world):
    assert world["public"].get(f"/public/{link(world)['token']}").json()["calendar_feed"] is False
    assert world["public"].get(f"/public/{link(world, term_start='2026-11-02', term_end='2027-02-27')['token']}").json()["calendar_feed"] is True


def test_a_wrong_or_withdrawn_link_gives_no_calendar(world):
    assert ics(world, "x" * 43).status_code == 404
    made = link(world, term_start="2026-11-02", term_end="2027-02-27")
    world["owen"].delete(f"/institutions/{world['iid']}/publications/{made['id']}")
    assert ics(world, made["token"]).status_code == 404


# ---------- term dates ----------

def test_term_dates_are_checked_and_can_be_changed_and_removed(world):
    o, iid = world["owen"], world["iid"]
    url = f"/institutions/{iid}/publications"
    assert o.post(url, json={"job_id": world["jid"], "term_start": "2026-11-02"}).status_code == 422  # both or neither
    assert o.post(url, json={"job_id": world["jid"], "term_start": "2026-11-02", "term_end": "2026-10-01"}).status_code == 422
    assert o.post(url, json={"job_id": world["jid"], "term_start": "2026-01-01", "term_end": "2027-12-31"}).status_code == 422  # too long
    made = link(world)
    assert made["term_start"] is None and made["times_set"] is True
    r = o.patch(f"{url}/{made['id']}", json={"term_start": "2026-11-02", "term_end": "2027-02-27"})
    assert r.status_code == 200 and (r.json()["term_start"], r.json()["term_end"]) == ("2026-11-02", "2027-02-27")
    assert o.patch(f"{url}/{made['id']}", json={"term_end": "2027-03-27"}).status_code == 422  # only half
    assert o.patch(f"{url}/{made['id']}", json={"label": "renamed"}).json()["term_start"] == "2026-11-02"  # untouched by other edits
    r = o.patch(f"{url}/{made['id']}", json={"term_start": None, "term_end": None})
    assert r.json()["term_start"] is None
    assert ics(world, made["token"]).status_code == 404


def test_weekday_names():
    names = ["Mon", "Tuesday", "wed.", "THU", "Fri", "Saturday"]
    assert [weekday_of(names, i) for i in range(6)] == [0, 1, 2, 3, 4, 5]
    assert weekday_of(["Day 1", "Day 2", "Sun"], 1) == 1 and weekday_of(["Day 1", "Day 2", "Sun"], 2) == 6


# ---------- what changed ----------

def kept(world, placements):
    r = world["owen"].post(f"/institutions/{world['iid']}/timetable/imported", json={"placements": placements})
    assert r.status_code == 201, r.text
    return r.json()["job_id"]


def test_students_are_told_what_changed_after_a_link_is_pointed_at_a_newer_timetable(world):
    o, iid = world["owen"], world["iid"]
    full = o.get(f"/jobs/{world['jid']}/timetable").json()["placements"]
    older = kept(world, full[:-1])  # without the last session
    newer = kept(world, [{**full[0], "day": (full[0]["day"] + 1) % len(world["data"]["calendar"]["day_names"])}, *full[1:]])  # first moved, last back
    made = o.post(f"/institutions/{iid}/publications", json={"job_id": older, "kinds": ["batch"]}).json()
    pub = world["public"]
    assert pub.get(f"/public/{made['token']}").json()["update"] is None  # nothing to report yet

    r = o.patch(f"/institutions/{iid}/publications/{made['id']}", json={"job_id": newer})
    assert r.status_code == 200
    assert o.patch(f"/institutions/{iid}/publications/{made['id']}", json={"note": "  Lab moves to Friday  "}).json()["note"] == "Lab moves to Friday"
    update = pub.get(f"/public/{made['token']}").json()["update"]
    assert (update["moved"], update["added"], update["removed"]) == (1, 1, 0)
    assert update["note"] == "Lab moves to Friday" and update["at"]

    offerings = {x["id"]: x for x in world["data"]["offerings"]}
    moved_class = offerings[full[0]["offering_id"]]["batch_ids"][0]
    view = pub.get(f"/public/{made['token']}/views/batch/{moved_class}").json()
    flagged = [s for s in view["sessions"] if s["changed"]]
    assert any(s["offering_id"] == full[0]["offering_id"] and s["session_index"] == full[0]["session_index"] for s in flagged)
    assert view["removed"] == []

    # the other direction: pointing back at the older one shows the dropped session as removed
    o.patch(f"/institutions/{iid}/publications/{made['id']}", json={"job_id": older})
    last_class = offerings[full[-1]["offering_id"]]["batch_ids"][0]
    back = pub.get(f"/public/{made['token']}/views/batch/{last_class}").json()
    assert back["removed"] and {"course_name", "day_name", "time"} <= set(back["removed"][0])
    assert pub.get(f"/public/{made['token']}").json()["update"]["note"] == ""  # a note belongs to its update


def test_pointing_at_the_same_timetable_is_not_an_update(world):
    o, iid = world["owen"], world["iid"]
    made = link(world)
    o.patch(f"/institutions/{iid}/publications/{made['id']}", json={"job_id": world["jid"], "label": "x"})
    assert world["public"].get(f"/public/{made['token']}").json()["update"] is None


def test_the_notice_goes_away_after_two_weeks(world):
    o, iid = world["owen"], world["iid"]
    full = o.get(f"/jobs/{world['jid']}/timetable").json()["placements"]
    a, b = kept(world, full[:-1]), kept(world, full)
    made = o.post(f"/institutions/{iid}/publications", json={"job_id": a, "kinds": ["batch"]}).json()
    o.patch(f"/institutions/{iid}/publications/{made['id']}", json={"job_id": b})
    assert world["public"].get(f"/public/{made['token']}").json()["update"] is not None
    old = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat(timespec="microseconds")
    db = sqlite3.connect(world["path"])
    db.execute("UPDATE publications SET updated_at=?", (old,))
    db.commit()
    db.close()
    assert world["public"].get(f"/public/{made['token']}").json()["update"] is None
    view = world["public"].get(f"/public/{made['token']}/views/batch/{world['data']['batches'][0]['id']}").json()
    assert all("changed" not in s for s in view["sessions"]) and view["removed"] == []


def test_an_old_database_gains_the_publication_columns(tmp_path):
    path = str(tmp_path / "old.db")
    with TestClient(create_app(path, auth=True)) as c:
        c.post("/auth/setup", json={"username": "root", "password": PW})
    db = sqlite3.connect(path)
    for col in ("term_start", "term_end", "prev_job_id", "updated_at", "note"):
        db.execute(f"ALTER TABLE publications DROP COLUMN {col}")
    db.commit()
    db.close()
    with TestClient(create_app(path, auth=True)) as c:
        c.post("/auth/login", json={"username": "root", "password": PW})
        iid = c.post("/institutions", json=sample_institution().model_dump(mode="json")).json()["id"]
        jid = c.post(f"/institutions/{iid}/timetable/imported", json={"placements": []}).json()["job_id"]
        made = c.post(f"/institutions/{iid}/publications", json={"job_id": jid, "term_start": "2026-11-02", "term_end": "2027-02-27"})
        assert made.status_code == 201 and made.json()["term_end"] == "2027-02-27"


def test_event_ids_survive_a_link_being_pointed_at_a_newer_timetable(world):
    """A subscribed calendar must update its events, not add a second copy of each."""
    o, iid = world["owen"], world["iid"]
    made = link(world, term_start="2026-11-02", term_end="2027-02-27")
    before = {e["UID"] for e in events(ics(world, made["token"]).text)}
    full = o.get(f"/jobs/{world['jid']}/timetable").json()["placements"]
    newer = kept(world, full)
    assert o.patch(f"/institutions/{iid}/publications/{made['id']}", json={"job_id": newer}).status_code == 200
    time.sleep(0.01)
    assert {e["UID"] for e in events(ics(world, made["token"]).text)} == before


# ---------- breaks: holidays and exam weeks ----------

def occurrences(ev):
    """The days one event happens on, read the way a calendar app would: weekly from DTSTART to UNTIL, minus EXDATE."""
    first = datetime.strptime(ev["DTSTART"][:8], "%Y%m%d").date()
    until = datetime.strptime(re.search(r"UNTIL=(\d{8})", ev["RRULE"]).group(1), "%Y%m%d").date()
    skipped = {datetime.strptime(x[:8], "%Y%m%d").date() for x in ev.get("EXDATE", "").split(",") if x}
    days, d = [], first
    while d <= until:
        if d not in skipped:
            days.append(d)
        d += timedelta(weeks=1)
    return days


TERM = {"term_start": "2026-11-02", "term_end": "2027-02-27"}


def test_a_break_week_is_left_out_of_every_event(world):
    plain = events(ics(world, link(world, **TERM)["token"]).text)
    token = link(world, **TERM, no_class=[{"start": "2026-11-16", "end": "2026-11-22", "label": "Diwali"}])["token"]
    got = events(ics(world, token).text)
    assert len(got) == len(plain) > 0
    for a, b in zip(sorted(plain, key=lambda e: e["UID"]), sorted(got, key=lambda e: e["UID"])):
        assert a["UID"] == b["UID"]
        assert len(occurrences(b)) == len(occurrences(a)) - 1
        assert not any(date(2026, 11, 16) <= d <= date(2026, 11, 22) for d in occurrences(b))
        assert "EXDATE" in b and b["EXDATE"].count(",") == 0 and b["EXDATE"].endswith(a["DTSTART"][8:])


def test_a_single_day_break_only_removes_that_weekday(world):
    plain = events(ics(world, link(world, **TERM)["token"]).text)
    token = link(world, **TERM, no_class=[{"start": "2026-11-18", "end": "2026-11-18"}])["token"]  # a Wednesday
    got = {e["UID"]: e for e in events(ics(world, token).text)}
    hit = 0
    for a in plain:
        b = got[a["UID"]]
        lost = len(occurrences(a)) - len(occurrences(b))
        assert lost == (1 if date(2026, 11, 18) in occurrences(a) else 0)
        hit += lost
    assert hit > 0  # the sample has Wednesday lectures


def test_a_break_at_the_start_moves_the_first_day_instead_of_listing_it_as_skipped(world):
    plain = {e["UID"]: e for e in events(ics(world, link(world, **TERM)["token"]).text)}
    token = link(world, **TERM, no_class=[{"start": "2026-11-02", "end": "2026-11-08"}])["token"]  # the whole first week
    for e in events(ics(world, token).text):
        assert "EXDATE" not in e
        assert occurrences(e)[0] == occurrences(plain[e["UID"]])[1]


def test_a_break_covering_the_term_leaves_an_empty_but_valid_calendar(world):
    token = link(world, **TERM, no_class=[{"start": "2026-10-01", "end": "2027-03-31"}])["token"]
    r = ics(world, token)
    assert r.status_code == 200 and events(r.text) == [] and r.text.startswith("BEGIN:VCALENDAR")


def test_breaks_outside_the_term_change_nothing(world):
    plain = ics(world, link(world, **TERM)["token"]).text
    token = link(world, **TERM, no_class=[{"start": "2025-01-01", "end": "2025-01-31"}, {"start": "2027-06-01", "end": "2027-06-30"}])["token"]
    strip = lambda t: re.sub(r"DTSTAMP:\S+", "", t)
    assert strip(ics(world, token).text) == strip(plain)


def test_breaks_are_stored_in_date_order_and_can_be_changed_or_cleared(world):
    pub = link(world, **TERM, no_class=[{"start": "2027-01-26", "end": "2027-01-26", "label": "  Republic Day "},
                                        {"start": "2026-11-16", "end": "2026-11-22", "label": "Diwali"}])
    assert [n["start"] for n in pub["no_class"]] == ["2026-11-16", "2027-01-26"] and pub["no_class"][1]["label"] == "Republic Day"
    url = f"/institutions/{world['iid']}/publications/{pub['id']}"
    assert world["owen"].patch(url, json={"label": "renamed"}).json()["no_class"] == pub["no_class"]  # left alone when not sent
    assert world["owen"].patch(url, json={"no_class": [{"start": "2026-12-25", "end": "2026-12-25", "label": "Christmas"}]}).json()["no_class"] == [
        {"start": "2026-12-25", "end": "2026-12-25", "label": "Christmas"}]
    assert world["owen"].patch(url, json={"no_class": []}).json()["no_class"] == []
    assert [p["no_class"] for p in world["owen"].get(f"/institutions/{world['iid']}/publications").json()] == [[]]


def test_the_page_lists_the_breaks_only_when_there_is_a_term(world):
    brk = [{"start": "2026-11-16", "end": "2026-11-22", "label": "Diwali"}]
    token = link(world, **TERM, no_class=brk)["token"]
    assert world["public"].get(f"/public/{token}").json()["no_class"] == [{**brk[0]}]
    bare = link(world, no_class=brk)["token"]  # no term: nothing to leave out of
    assert world["public"].get(f"/public/{bare}").json()["no_class"] == []


@pytest.mark.parametrize("bad", [
    [{"start": "2026-11-20", "end": "2026-11-19"}],
    [{"start": "2026-01-01", "end": "2027-03-01"}],  # longer than a term can be
    [{"start": "2026-11-16", "end": "2026-11-16", "label": "x" * 61}],
    [{"start": "2026-11-16", "end": "2026-11-16"}] * 61,
    [{"start": "tomorrow", "end": "2026-11-16"}],
])
def test_bad_breaks_are_refused(world, bad):
    r = world["owen"].post(f"/institutions/{world['iid']}/publications", json={"job_id": world["jid"], "no_class": bad})
    assert r.status_code == 422
    pub = link(world, **TERM)
    assert world["owen"].patch(f"/institutions/{world['iid']}/publications/{pub['id']}", json={"no_class": bad}).status_code == 422


def test_changing_the_breaks_is_in_the_activity_log(world):
    pub = link(world, **TERM)
    world["owen"].patch(f"/institutions/{world['iid']}/publications/{pub['id']}", json={"no_class": [{"start": "2026-11-16", "end": "2026-11-16"}]})
    rows = world["owen"].get(f"/institutions/{world['iid']}/audit").json()
    assert any(r["action"] == "publication.changed" and "no_class" in r["summary"] for r in rows)


def test_a_database_from_before_breaks_opens_and_gets_the_column(tmp_path):
    from automated_academics.store import Store
    path = str(tmp_path / "old.db")
    Store(path)
    with sqlite3.connect(path) as c:
        c.execute("ALTER TABLE publications DROP COLUMN no_class")
    s = Store(path)
    with sqlite3.connect(path) as c:
        assert "no_class" in {r[1] for r in c.execute("PRAGMA table_info(publications)")}
    assert s.list_publications("none") == []


# ---------- the early/late floor in the checks ----------

def test_checks_say_when_early_and_late_lectures_cannot_reach_zero():
    from automated_academics.checks import diagnose
    inst = sample_institution()
    inst.weights.odd_hours = 5
    leaves = set(inst.leaf_batches())
    hours = {b: 0 for b in leaves}
    for o in inst.offerings:
        for b in inst.occupied_batches(o.batch_ids) & leaves:
            hours[b] += sum(o.sessions)
    saw_exact = False
    for early, late in [(e, l) for e in range(4) for l in range(4)]:  # how many lectures at each end of the day count as odd
        inst.calendar.early_count, inst.calendar.late_count = early, late
        middle = inst.calendar.days * (inst.calendar.lectures_per_day - len(inst.calendar.odd_lectures()))
        forced = sum(max(0, n - middle) for n in hours.values())
        over = sum(1 for n in hours.values() if n > middle)
        saw_exact |= any(n == middle for n in hours.values())
        notes = [d for d in diagnose(inst) if "early/late" in d.message]
        if forced:
            assert len(notes) == 1 and notes[0].level == "info" and f"at least {forced} lectures" in notes[0].message
            assert f"{over} class(es) have more" in notes[0].message
        else:
            assert not notes
    assert saw_exact, "the sample should have a class loaded exactly to the limit for some setting"
    inst.weights.odd_hours = 0  # goal off: no comment
    assert not [d for d in diagnose(inst) if "early/late" in d.message]
    inst.weights.odd_hours = 5
    inst.calendar.early_count = inst.calendar.late_count = 0  # nothing counts as early or late
    assert not [d for d in diagnose(inst) if "early/late" in d.message]
