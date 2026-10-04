"""SQLite persistence (standard library only).

Institutions and solve jobs are stored as JSON documents. This is deliberately
simple; swap for PostgreSQL later if multi-user hosting needs it.
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import Institution, Timetable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS institutions (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, institution_id TEXT NOT NULL REFERENCES institutions(id),
    status TEXT NOT NULL, time_limit_s REAL NOT NULL, created_at TEXT NOT NULL,
    finished_at TEXT, error TEXT, timetable TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        with self._conn() as c:
            c.executescript(_SCHEMA)

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
    def add_institution(self, inst: Institution) -> str:
        iid = _new_id()
        with self._conn() as c:
            c.execute("INSERT INTO institutions VALUES (?,?,?,?)",
                      (iid, inst.name, _now(), inst.model_dump_json()))
        return iid

    def get_institution(self, iid: str) -> Institution | None:
        with self._conn() as c:
            row = c.execute("SELECT data FROM institutions WHERE id=?", (iid,)).fetchone()
        return Institution.model_validate_json(row["data"]) if row else None

    def list_institutions(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT id, name, created_at FROM institutions "
                             "ORDER BY created_at DESC, rowid DESC").fetchall()
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
