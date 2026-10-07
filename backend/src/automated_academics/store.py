"""SQLite persistence (standard library only).

Institutions and solve jobs are stored as JSON documents. This is deliberately
simple; swap for PostgreSQL later if multi-user hosting needs it.
"""

from __future__ import annotations

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
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
    institution_id TEXT NOT NULL REFERENCES institutions(id), user_id TEXT NOT NULL REFERENCES users(id),
    role TEXT NOT NULL, PRIMARY KEY (institution_id, user_id)
);
"""


class Conflict(Exception):
    """The data was changed by someone else since the caller read it."""


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
            # counts every change (including ones that leave updated_at alone), so two editors can't overwrite each other
            if "version" not in cols:
                c.execute("ALTER TABLE institutions ADD COLUMN version INTEGER NOT NULL DEFAULT 0")
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
                rows = c.execute("SELECT i.id, i.name, i.created_at, i.updated_at, m.role FROM institutions i "
                                 "JOIN members m ON m.institution_id = i.id WHERE m.user_id=? "
                                 "ORDER BY i.created_at DESC, i.rowid DESC", (user_id,)).fetchall()
        return [dict(r) for r in rows]

    # ---- users, sign-in sessions and who may see which institution ----
    def count_users(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]

    def add_user(self, username: str, display_name: str, password_hash: str, role: str) -> str:
        uid = _new_id()
        with self._conn() as c:
            c.execute("INSERT INTO users (id, username, display_name, password_hash, role, created_at) VALUES (?,?,?,?,?,?)",
                      (uid, username, display_name, password_hash, role, _now()))
        return uid

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
        allowed = {"display_name", "password_hash", "role", "disabled"}
        assert set(fields) <= allowed, fields
        if not fields:
            return
        sets = ", ".join(k + "=?" for k in fields)
        with self._conn() as c:
            c.execute("UPDATE users SET " + sets + " WHERE id=?", (*fields.values(), uid))

    def delete_user(self, uid: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
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

    def member_role(self, iid: str, uid: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT role FROM members WHERE institution_id=? AND user_id=?", (iid, uid)).fetchone()
        return row["role"] if row else None

    def set_member(self, iid: str, uid: str, role: str) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO members (institution_id, user_id, role) VALUES (?,?,?) "
                      "ON CONFLICT(institution_id, user_id) DO UPDATE SET role=excluded.role", (iid, uid, role))

    def remove_member(self, iid: str, uid: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM members WHERE institution_id=? AND user_id=?", (iid, uid))

    def list_members(self, iid: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT u.id, u.username, u.display_name, m.role FROM members m JOIN users u ON u.id = m.user_id "
                             "WHERE m.institution_id=? ORDER BY m.role, u.username", (iid,)).fetchall()
        return [dict(r) for r in rows]

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
