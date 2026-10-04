"""FastAPI service.

Run:  uvicorn automated_academics.api:create_app --factory --reload
Docs: http://127.0.0.1:8000/docs

There is no authentication yet: run it on localhost or behind your own access
control. Do not expose it to the public internet as is.
"""

from __future__ import annotations

import io
import logging
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import __version__
from .excel_io import ImportErrors, export_workbook, import_workbook
from .models import Institution, Timetable
from .solver import InfeasibleError, solve
from .store import Store
from .synthetic import sample_institution
from .validate import find_conflicts

log = logging.getLogger("automated_academics")

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class SolveRequest(BaseModel):
    time_limit_s: float = Field(default=30, ge=1, le=300)


class ConflictReport(BaseModel):
    ok: bool
    conflicts: list[str]


def create_app(db_path: str | None = None, workers: int = 1) -> FastAPI:
    store = Store(db_path or os.environ.get("AA_DB", "automated_academics.db"))
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="solver")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        n = store.fail_unfinished("server restarted before the job finished")
        if n:
            log.warning("marked %d unfinished job(s) as failed", n)
        yield
        pool.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="Automated Academics", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],  # React dev server
        allow_methods=["*"], allow_headers=["*"],
    )

    # ---------------- helpers ----------------
    def institution_or_404(iid: str) -> Institution:
        inst = store.get_institution(iid)
        if inst is None:
            raise HTTPException(404, "institution not found")
        return inst

    def job_or_404(jid: str) -> dict:
        job = store.get_job(jid)
        if job is None:
            raise HTTPException(404, "job not found")
        return job

    def run_job(jid: str, inst: Institution, limit: float) -> None:
        store.set_running(jid)
        try:
            store.finish_job(jid, solve(inst, time_limit_s=limit))
        except InfeasibleError as e:
            store.fail_job(jid, str(e))
        except Exception:  # noqa: BLE001 - never leave a job stuck in "running"
            log.exception("job %s crashed", jid)
            store.fail_job(jid, "internal error while solving")

    # ---------------- meta ----------------
    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__}

    # ---------------- institutions ----------------
    @app.get("/institutions/template")
    def template():
        buf = io.BytesIO()
        export_workbook(sample_institution(), buf)
        buf.seek(0)
        return StreamingResponse(
            buf, media_type=XLSX,
            headers={"Content-Disposition": 'attachment; filename="institution-template.xlsx"'},
        )

    @app.get("/institutions")
    def list_institutions():
        return store.list_institutions()

    @app.post("/institutions", status_code=201)
    def create_institution(inst: Institution):
        return {"id": store.add_institution(inst), "name": inst.name}

    @app.post("/institutions/upload", status_code=201)
    def upload_institution(file: UploadFile = File(...)):
        data = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"file larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        try:
            inst = import_workbook(io.BytesIO(data))
        except ImportErrors as e:
            raise HTTPException(422, detail={
                "message": f"{len(e.issues)} problem(s) found in the workbook",
                "issues": [{"sheet": i.sheet, "row": i.row, "message": i.message} for i in e.issues],
            }) from None
        except (zipfile.BadZipFile, KeyError, OSError):
            raise HTTPException(400, "not a valid .xlsx workbook") from None
        return {"id": store.add_institution(inst), "name": inst.name}

    @app.get("/institutions/{iid}", response_model=Institution)
    def get_institution(iid: str):
        return institution_or_404(iid)

    @app.post("/institutions/{iid}/solve", status_code=202)
    def start_solve(iid: str, req: SolveRequest | None = None):
        inst = institution_or_404(iid)
        limit = (req or SolveRequest()).time_limit_s
        jid = store.create_job(iid, limit)
        pool.submit(run_job, jid, inst, limit)
        return {"job_id": jid, "status": "queued"}

    @app.post("/institutions/{iid}/validate", response_model=ConflictReport)
    def validate_timetable(iid: str, tt: Timetable):
        """Check a (possibly hand-edited) timetable; the basis for live clash detection."""
        conflicts = find_conflicts(institution_or_404(iid), tt)
        return ConflictReport(ok=not conflicts, conflicts=conflicts)

    # ---------------- jobs and timetables ----------------
    @app.get("/jobs/{jid}")
    def get_job(jid: str):
        return job_or_404(jid)

    @app.get("/jobs/{jid}/timetable", response_model=Timetable)
    def get_timetable(jid: str):
        job = job_or_404(jid)
        tt = store.get_timetable(jid)
        if tt is None:
            raise HTTPException(409, f"no timetable: job is {job['status']}"
                                + (f" ({job['error']})" if job["error"] else ""))
        return tt

    @app.get("/jobs/{jid}/views/{kind}/{ident}")
    def view(jid: str, kind: Literal["batch", "faculty", "room"], ident: str):
        """One batch's, faculty member's or room's week, with names resolved for display."""
        job = job_or_404(jid)
        inst = institution_or_404(job["institution_id"])
        tt = store.get_timetable(jid)
        if tt is None:
            raise HTTPException(409, f"no timetable: job is {job['status']}")

        offerings = {o.id: o for o in inst.offerings}
        courses = {c.code: c for c in inst.courses}
        faculty = {f.id: f for f in inst.faculty}
        rooms = {r.id: r for r in inst.rooms}
        batches = {b.id: b for b in inst.batches}
        known = {"batch": batches, "faculty": faculty, "room": rooms}[kind]
        if ident not in known:
            raise HTTPException(404, f"{kind} not found")

        def relevant(p) -> bool:
            o = offerings[p.offering_id]
            if kind == "faculty":
                return o.faculty_id == ident
            if kind == "room":
                return p.room_id == ident
            # a section sees its own sessions, its sub-groups' labs, and a sub-group sees its parent's
            return ident in inst.occupied_batches(o.batch_ids) or any(
                batches[b].group_of == ident for b in o.batch_ids)

        cal = inst.calendar
        out = []
        for p in sorted(tt.placements, key=lambda p: (p.day, p.start)):
            if not relevant(p):
                continue
            o = offerings[p.offering_id]
            out.append({
                "offering_id": o.id, "session_index": p.session_index,
                "course_code": o.course_code, "course_name": courses[o.course_code].name,
                "faculty_id": o.faculty_id, "faculty_name": faculty[o.faculty_id].name,
                "batch_ids": o.batch_ids, "room_id": p.room_id, "room_name": rooms[p.room_id].name,
                "day": p.day, "day_name": cal.day_names[p.day],
                "start": p.start, "length": p.length,  # zero-based periods
            })
        return {"kind": kind, "id": ident, "sessions": out}

    return app
