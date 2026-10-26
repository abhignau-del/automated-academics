"""SQLite persistence (standard library only).

Institutions and solve jobs are stored as JSON documents. This is deliberately
simple; swap for PostgreSQL later if multi-user hosting needs it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import Institution, Timetable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS institutions (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, institution_id TEXT NOT NULL REFERENCES institutions(id),
    status TEXT NOT NULL, time_limit_s REAL NOT NULL, created_at TEXT NOT NULL,
    finished_at TEXT, error TEXT, timetable TEXT
);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL, role TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS password_resets (
    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
    institution_id TEXT NOT NULL REFERENCES institutions(id), user_id TEXT NOT NULL REFERENCES users(id),
    role TEXT NOT NULL, PRIMARY KEY (institution_id, user_id)
);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor_id TEXT, actor TEXT NOT NULL, action TEXT NOT NULL,
    institution_id TEXT, institution TEXT, summary TEXT NOT NULL, detail TEXT NOT NULL, ip TEXT,
    prev_hash TEXT NOT NULL, hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_by_institution ON audit (institution_id, seq);
CREATE TABLE IF NOT EXISTS publications (
    id TEXT PRIMARY KEY, institution_id TEXT NOT NULL REFERENCES institutions(id), job_id TEXT NOT NULL,
    token TEXT NOT NULL UNIQUE, label TEXT NOT NULL, kinds TEXT NOT NULL,
    created_by TEXT, created_at TEXT NOT NULL, expires_at TEXT
);
"""


class Conflict(Exception):
    """The data was changed by someone else since the caller read it."""


_GENESIS = "0" * 64


def _audit_hash(prev: str, row: dict) -> str:
    """Each entry's hash covers the one before it, so editing or removing an entry breaks every hash after it."""
    body = json.dumps([row["at"], row["actor_id"], row["actor"], row["action"], row["institution_id"], row["institution"],
                       row["summary"], row["detail"], row["ip"]], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256((prev + body).encode("utf-8")).hexdigest()


def _without_pins(data: str) -> dict:
    d = json.loads(data)
    d.pop("pins", None)
    return d


def _now() -> str:
    # microseconds, so an edit made right after a solve starts still sorts after it
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        with self._conn() as c:
            c.executescript(_SCHEMA)
            # databases created before in-app editing have no updated_at column
            cols = {r["name"] for r in c.execute("PRAGMA table_info(institutions)")}
            if "updated_at" not in cols:
                c.execute("ALTER TABLE institutions ADD COLUMN updated_at TEXT")
            c.execute("UPDATE institutions SET updated_at = created_at WHERE updated_at IS NULL")
            # term dates (for calendar files) and the last switch to a newer timetable (for the students' "what changed")
            pub_cols = {r["name"] for r in c.execute("PRAGMA table_info(publications)")}
            for col in ("term_start", "term_end", "prev_job_id", "updated_at"):
                if col not in pub_cols:
                    c.execute(f"ALTER TABLE publications ADD COLUMN {col} TEXT")
            if "note" not in pub_cols:
                c.execute("ALTER TABLE publications ADD COLUMN note TEXT NOT NULL DEFAULT ''")
            # holidays and exam weeks (JSON list of {start, end, label}) that a calendar file leaves out
            if "no_class" not in pub_cols:
                c.execute("ALTER TABLE publications ADD COLUMN no_class TEXT NOT NULL DEFAULT '[]'")
            # counts every change (including ones that leave updated_at alone), so two editors can't overwrite each other
            if "version" not in cols:
                c.execute("ALTER TABLE institutions ADD COLUMN version INTEGER NOT NULL DEFAULT 0")
            # an editor limited to some departments (JSON list); NULL means the whole institution
            member_cols = {r["name"] for r in c.execute("PRAGMA table_info(members)")}
            if "departments" not in member_cols:
                c.execute("ALTER TABLE members ADD COLUMN departments TEXT")
            # an e-mail address, for "forgot password" (empty = none)
            user_cols = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
            if "email" not in user_cols:
                c.execute("ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")  # readers don't block the writer, which matters with several users

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            with c:  # commit or roll back
                yield c
        finally:
            c.close()

    # ---- institutions ----
    def add_institution(self, inst: Institution, owner_id: str | None = None) -> str:
        iid, now = _new_id(), _now()
        with self._conn() as c:
            c.execute("INSERT INTO institutions (id, name, created_at, updated_at, data) VALUES (?,?,?,?,?)",
                      (iid, inst.name, now, now, inst.model_dump_json()))
            if owner_id:
                c.execute("INSERT INTO members (institution_id, user_id, role) VALUES (?,?,'owner')", (iid, owner_id))
        return iid

    def get_institution(self, iid: str) -> Institution | None:
        with self._conn() as c:
            row = c.execute("SELECT data FROM institutions WHERE id=?", (iid,)).fetchone()
        return Institution.model_validate_json(row["data"]) if row else None

    def institution_version(self, iid: str) -> int | None:
        with self._conn() as c:
            row = c.execute("SELECT version FROM institutions WHERE id=?", (iid,)).fetchone()
        return row["version"] if row else None

    def update_institution(self, iid: str, inst: Institution, expected_version: int | None = None) -> bool:
        """Replace an institution's data. Returns False if it does not exist.

        With `expected_version`, raises `Conflict` if someone saved in the meantime.

        `updated_at` only moves when the data actually changed, so saving an unchanged form does
        not make existing timetables look out of date.
        """
        data = inst.model_dump_json()
        with self._conn() as c:
            row = c.execute("SELECT data, version FROM institutions WHERE id=?", (iid,)).fetchone()
            if row is None:
                return False
            if expected_version is not None and row["version"] != expected_version:
                raise Conflict()
            if row["data"] != data:
                # Pins only steer the next Generate; they don't make an existing timetable wrong.
                only_pins = _without_pins(row["data"]) == _without_pins(data)
                c.execute("UPDATE institutions SET name=?, data=?, updated_at=COALESCE(?, updated_at), version=version+1 "
                          "WHERE id=?", (inst.name, data, None if only_pins else _now(), iid))
        return True

    def delete_institution(self, iid: str) -> bool:
        """Delete an institution and all its timetables. Returns False if it does not exist."""
        with self._conn() as c:
            c.execute("DELETE FROM jobs WHERE institution_id=?", (iid,))
            c.execute("DELETE FROM members WHERE institution_id=?", (iid,))
            c.execute("DELETE FROM publications WHERE institution_id=?", (iid,))
            return c.execute("DELETE FROM institutions WHERE id=?", (iid,)).rowcount > 0

    def institution_updated_at(self, iid: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT updated_at FROM institutions WHERE id=?", (iid,)).fetchone()
        return row["updated_at"] if row else None

    def list_institutions(self, user_id: str | None = None) -> list[dict[str, Any]]:
        """All institutions, or only those `user_id` is a member of (each with their `role`)."""
        with self._conn() as c:
            if user_id is None:
                rows = c.execute("SELECT id, name, created_at, updated_at FROM institutions "
                                 "ORDER BY created_at DESC, rowid DESC").fetchall()
            else:
                rows = c.execute("SELECT i.id, i.name, i.created_at, i.updated_at, m.role, m.departments FROM institutions i "
                                 "JOIN members m ON m.institution_id = i.id WHERE m.user_id=? "
                                 "ORDER BY i.created_at DESC, i.rowid DESC", (user_id,)).fetchall()
        out = [dict(r) for r in rows]
        for r in out:
            if "departments" in r:
                r["departments"] = json.loads(r["departments"]) if r["departments"] else None
        return out

    # ---- users, sign-in sessions and who may see which institution ----
    def count_users(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]

    def add_user(self, username: str, display_name: str, password_hash: str, role: str, email: str = "") -> str:
        uid = _new_id()
        with self._conn() as c:
            c.execute("INSERT INTO users (id, username, display_name, password_hash, role, created_at, email) VALUES (?,?,?,?,?,?,?)",
                      (uid, username, display_name, password_hash, role, _now(), email))
        return uid

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM users WHERE email=? AND email<>''", (email,)).fetchone()
        return dict(row) if row else None

    # ---- choosing a new password from an e-mailed link: one-use, short-lived, only the token's hash is kept ----
    def create_reset(self, user_id: str, token_hash: str, ttl_s: float) -> None:
        now = datetime.now(timezone.utc)
        with self._conn() as c:
            c.execute("DELETE FROM password_resets WHERE user_id=? OR expires_at < ?", (user_id, now.isoformat(timespec="microseconds")))
            c.execute("INSERT INTO password_resets (token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                      (token_hash, user_id, now.isoformat(timespec="microseconds"), (now + timedelta(seconds=ttl_s)).isoformat(timespec="microseconds")))

    def recent_reset(self, user_id: str, within_s: float) -> bool:
        since = (datetime.now(timezone.utc) - timedelta(seconds=within_s)).isoformat(timespec="microseconds")
        with self._conn() as c:
            return c.execute("SELECT 1 FROM password_resets WHERE user_id=? AND created_at > ?", (user_id, since)).fetchone() is not None

    def take_reset(self, token_hash: str) -> str | None:
        """The user this token is for, and the token is used up; None if unknown, used or expired."""
        with self._conn() as c:
            row = c.execute("SELECT user_id, expires_at FROM password_resets WHERE token_hash=?", (token_hash,)).fetchone()
            if row is None:
                return None
            c.execute("DELETE FROM password_resets WHERE token_hash=?", (token_hash,))
        return row["user_id"] if row["expires_at"] > datetime.now(timezone.utc).isoformat(timespec="microseconds") else None

    def get_user(self, uid: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return dict(row) if row else None

    def get_user_by_name(self, username: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM users ORDER BY created_at, rowid")]

    def update_user(self, uid: str, **fields: Any) -> None:
        allowed = {"display_name", "password_hash", "role", "disabled", "email"}
        assert set(fields) <= allowed, fields
        if not fields:
            return
        sets = ", ".join(k + "=?" for k in fields)
        with self._conn() as c:
            c.execute("UPDATE users SET " + sets + " WHERE id=?", (*fields.values(), uid))

    def delete_user(self, uid: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            c.execute("DELETE FROM password_resets WHERE user_id=?", (uid,))
            c.execute("DELETE FROM members WHERE user_id=?", (uid,))
            c.execute("DELETE FROM users WHERE id=?", (uid,))

    def create_session(self, user_id: str, token_hash: str, ttl_days: float) -> None:
        now = datetime.now(timezone.utc)
        with self._conn() as c:
            c.execute("DELETE FROM sessions WHERE expires_at < ?", (now.isoformat(timespec="microseconds"),))
            c.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                      (token_hash, user_id, now.isoformat(timespec="microseconds"),
                       (now + timedelta(days=ttl_days)).isoformat(timespec="microseconds")))

    def user_for_session(self, token_hash: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
                            "WHERE s.token_hash=? AND s.expires_at > ?",
                            (token_hash, datetime.now(timezone.utc).isoformat(timespec="microseconds"))).fetchone()
        return dict(row) if row else None

    def delete_session(self, token_hash: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))

    def delete_user_sessions(self, uid: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))

    # ---- the audit log: append-only, each entry chained to the one before ----
    def institution_name(self, iid: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT name FROM institutions WHERE id=?", (iid,)).fetchone()
        return row["name"] if row else None

    def add_audit(self, actor_id: str | None, actor: str, action: str, summary: str, institution_id: str | None = None,
                  institution: str | None = None, detail: dict[str, Any] | None = None, ip: str | None = None) -> int:
        row = {"at": _now(), "actor_id": actor_id, "actor": actor, "action": action, "institution_id": institution_id,
               "institution": institution, "summary": summary, "detail": json.dumps(detail or {}, sort_keys=True, ensure_ascii=False),
               "ip": ip}
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")  # one writer at a time, so the chain has no forks
            last = c.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
            prev = last["hash"] if last else _GENESIS
            cur = c.execute(
                "INSERT INTO audit (at, actor_id, actor, action, institution_id, institution, summary, detail, ip, prev_hash, hash) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (row["at"], actor_id, actor, action, institution_id, institution, summary, row["detail"], ip, prev, _audit_hash(prev, row)))
            return int(cur.lastrowid)

    def list_audit(self, institution_id: str | None = None, user: str | None = None, action: str | None = None,
                   since: str | None = None, until: str | None = None, q: str | None = None, before: int | None = None,
                   limit: int = 50) -> list[dict[str, Any]]:
        where, args = [], []
        if institution_id:
            where.append("institution_id = ?"); args.append(institution_id)
        if user:
            where.append("actor = ?"); args.append(user)
        if action:
            where.append("(action = ? OR action LIKE ?)"); args += [action, action.rstrip(".") + ".%"]
        if since:
            where.append("at >= ?"); args.append(since)
        if until:
            where.append("at <= ?"); args.append(until)
        if q:
            where.append("(summary LIKE ? ESCAPE '\\' OR actor LIKE ? ESCAPE '\\' OR institution LIKE ? ESCAPE '\\')")
            like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            args += [like, like, like]
        if before:
            where.append("seq < ?"); args.append(before)
        sql = ("SELECT seq, at, actor_id, actor, action, institution_id, institution, summary, detail, ip FROM audit"
               + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY seq DESC LIMIT ?")
        with self._conn() as c:
            rows = c.execute(sql, (*args, limit)).fetchall()
        out = [dict(r) for r in rows]
        for r in out:
            r["detail"] = json.loads(r["detail"])
        return out

    def verify_audit(self) -> dict[str, Any]:
        """Recompute the whole chain. `ok` is False, with the first bad entry, if anything was edited or removed from the middle."""
        prev, checked = _GENESIS, 0
        with self._conn() as c:
            for r in c.execute("SELECT * FROM audit ORDER BY seq"):
                row = dict(r)
                if row["prev_hash"] != prev or row["hash"] != _audit_hash(prev, row):
                    return {"ok": False, "checked": checked, "first_bad": row["seq"]}
                prev, checked = row["hash"], checked + 1
        return {"ok": True, "checked": checked, "first_bad": None}

    # ---- published (read-only, link-only) timetables ----
    @staticmethod
    def _publication(row: Any) -> dict[str, Any]:
        d = dict(row)
        d["kinds"] = json.loads(d["kinds"])
        d["no_class"] = json.loads(d["no_class"])
        return d

    def add_publication(self, iid: str, job_id: str, token: str, label: str, kinds: list[str], created_by: str | None,
                        expires_days: float | None, term_start: str | None = None, term_end: str | None = None,
                        no_class: list[dict[str, str]] | None = None) -> dict[str, Any]:
        pid, now = _new_id(), datetime.now(timezone.utc)
        expires = (now + timedelta(days=expires_days)).isoformat(timespec="microseconds") if expires_days else None
        with self._conn() as c:
            c.execute("INSERT INTO publications (id, institution_id, job_id, token, label, kinds, created_by, created_at, expires_at, "
                      "term_start, term_end, no_class) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (pid, iid, job_id, token, label, json.dumps(kinds), created_by, now.isoformat(timespec="microseconds"), expires,
                       term_start, term_end, json.dumps(no_class or [])))
            row = c.execute("SELECT * FROM publications WHERE id=?", (pid,)).fetchone()
        return self._publication(row)

    def get_publication_by_token(self, token: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM publications WHERE token=?", (token,)).fetchone()
        return self._publication(row) if row else None

    def list_publications(self, iid: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM publications WHERE institution_id=? ORDER BY created_at DESC, rowid DESC", (iid,)).fetchall()
        return [self._publication(r) for r in rows]

    def update_publication(self, pid: str, iid: str, **fields: Any) -> bool:
        allowed = {"job_id", "label", "kinds", "expires_at", "term_start", "term_end", "prev_job_id", "updated_at", "note", "no_class"}
        assert set(fields) <= allowed, fields
        if "kinds" in fields:
            fields["kinds"] = json.dumps(fields["kinds"])
        if "no_class" in fields:
            fields["no_class"] = json.dumps(fields["no_class"])
        if not fields:
            return self.get_publication(pid, iid) is not None
        sets = ", ".join(k + "=?" for k in fields)
        with self._conn() as c:
            return c.execute("UPDATE publications SET " + sets + " WHERE id=? AND institution_id=?",
                             (*fields.values(), pid, iid)).rowcount > 0

    def get_publication(self, pid: str, iid: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM publications WHERE id=? AND institution_id=?", (pid, iid)).fetchone()
        return self._publication(row) if row else None

    def delete_publication(self, pid: str, iid: str) -> bool:
        with self._conn() as c:
            return c.execute("DELETE FROM publications WHERE id=? AND institution_id=?", (pid, iid)).rowcount > 0

    def member_role(self, iid: str, uid: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT role FROM members WHERE institution_id=? AND user_id=?", (iid, uid)).fetchone()
        return row["role"] if row else None

    def member_departments(self, iid: str, uid: str) -> list[str] | None:
        """The departments an editor is limited to, or None for the whole institution."""
        with self._conn() as c:
            row = c.execute("SELECT departments FROM members WHERE institution_id=? AND user_id=?", (iid, uid)).fetchone()
        return json.loads(row["departments"]) if row and row["departments"] else None

    def set_member(self, iid: str, uid: str, role: str, departments: list[str] | None = None) -> None:
        stored = json.dumps(sorted(set(departments))) if departments else None
        with self._conn() as c:
            c.execute("INSERT INTO members (institution_id, user_id, role, departments) VALUES (?,?,?,?) "
                      "ON CONFLICT(institution_id, user_id) DO UPDATE SET role=excluded.role, departments=excluded.departments",
                      (iid, uid, role, stored))

    def remove_member(self, iid: str, uid: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM members WHERE institution_id=? AND user_id=?", (iid, uid))

    def list_members(self, iid: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT u.id, u.username, u.display_name, m.role, m.departments FROM members m "
                             "JOIN users u ON u.id = m.user_id WHERE m.institution_id=? ORDER BY m.role, u.username", (iid,)).fetchall()
        out = [dict(r) for r in rows]
        for r in out:
            r["departments"] = json.loads(r["departments"]) if r["departments"] else None
        return out

    # ---- jobs ----
    def create_job(self, institution_id: str, time_limit_s: float) -> str:
        jid = _new_id()
        with self._conn() as c:
            c.execute("INSERT INTO jobs (id, institution_id, status, time_limit_s, created_at) "
                      "VALUES (?,?,?,?,?)", (jid, institution_id, "queued", time_limit_s, _now()))
        return jid

    def set_running(self, jid: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE jobs SET status='running' WHERE id=?", (jid,))

    def finish_job(self, jid: str, tt: Timetable) -> None:
        with self._conn() as c:
            c.execute("UPDATE jobs SET status='done', finished_at=?, timetable=? WHERE id=?",
                      (_now(), tt.model_dump_json(), jid))

    def save_timetable(self, jid: str, tt: Timetable) -> None:
        with self._conn() as c:
            c.execute("UPDATE jobs SET timetable=? WHERE id=?", (tt.model_dump_json(), jid))

    def latest_done_job(self, institution_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT id, institution_id, status, time_limit_s, created_at, finished_at, error "
                "FROM jobs WHERE institution_id=? AND status='done' "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1", (institution_id,)).fetchone()
        return dict(row) if row else None

    def fail_job(self, jid: str, error: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE jobs SET status='failed', finished_at=?, error=? WHERE id=?",
                      (_now(), error, jid))

    def fail_unfinished(self, reason: str) -> int:
        with self._conn() as c:
            cur = c.execute("UPDATE jobs SET status='failed', finished_at=?, error=? "
                            "WHERE status IN ('queued','running')", (_now(), reason))
            return cur.rowcount

    def get_job(self, jid: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT id, institution_id, status, time_limit_s, created_at, "
                            "finished_at, error FROM jobs WHERE id=?", (jid,)).fetchone()
        return dict(row) if row else None

    def get_timetable(self, jid: str) -> Timetable | None:
        with self._conn() as c:
            row = c.execute("SELECT timetable FROM jobs WHERE id=?", (jid,)).fetchone()
        return Timetable.model_validate_json(row["timetable"]) if row and row["timetable"] else None
