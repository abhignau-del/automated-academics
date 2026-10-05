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
import re
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
from .export import build_pdf, build_xlsx
from .models import Institution, Timetable
from .solver import InfeasibleError, solve
from .store import Store
from .synthetic import sample_institution
from .quality import measure, score
from .validate import find_conflict_details
from .views import session_views

log = logging.getLogger("automated_academics")

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class SolveRequest(BaseModel):
    time_limit_s: float = Field(default=30, ge=1, le=900)


class ConflictDetail(BaseModel):
    message: str
    offering_ids: list[str]


class ConflictReport(BaseModel):
    ok: bool
    conflicts: list[str]
    details: list[ConflictDetail]
    # soft-goal metrics of this timetable (see quality.measure) and their weighted total
    quality: dict[str, int]
    penalty: int


def _attachment(filename: str) -> dict[str, str]:
    """Content-Disposition header with a filename safe to embed (ids come from user data)."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename)
    return {"Content-Disposition": f'attachment; filename="{safe}"'}


def _report(inst: Institution, tt: Timetable) -> ConflictReport:
    details = find_conflict_details(inst, tt)
    metrics = measure(inst, tt)
    return ConflictReport(
        ok=not details,
        conflicts=[d.message for d in details],
        details=[ConflictDetail(message=d.message, offering_ids=list(d.offering_ids)) for d in details],
        quality=metrics,
        penalty=score(inst.weights, metrics),
    )


def create_app(db_path: str | None = None, workers: int = 1, pdf_font: str | None = None) -> FastAPI:
    store = Store(db_path or os.environ.get("AA_DB", "automated_academics.db"))
    pdf_font = pdf_font or os.environ.get("AA_PDF_FONT") or None
    if pdf_font and not os.path.isfile(pdf_font):
        raise FileNotFoundError(f"AA_PDF_FONT / pdf_font not found: {pdf_font}")
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
        return _report(institution_or_404(iid), tt)

    @app.get("/institutions/{iid}/latest-job")
    def latest_job(iid: str):
        institution_or_404(iid)
        job = store.latest_done_job(iid)
        if job is None:
            raise HTTPException(404, "no finished timetable for this institution yet")
        return job

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

    @app.put("/jobs/{jid}/timetable", response_model=ConflictReport)
    def save_timetable(jid: str, tt: Timetable):
        """Save a hand-edited timetable. Clashes are reported but do not block saving."""
        job = job_or_404(jid)
        if store.get_timetable(jid) is None:
            raise HTTPException(409, f"job is {job['status']}; only finished jobs can be edited")
        inst = institution_or_404(job["institution_id"])
        store.save_timetable(jid, tt.model_copy(update={"status": "MANUAL"}))
        return _report(inst, tt)

    def _exportable(jid: str) -> tuple[Institution, Timetable]:
        job = job_or_404(jid)
        tt = store.get_timetable(jid)
        if tt is None:
            raise HTTPException(409, f"no timetable: job is {job['status']}")
        return institution_or_404(job["institution_id"]), tt

    @app.get("/jobs/{jid}/export.xlsx")
    def export_xlsx(jid: str):
        """Excel workbook: a flat session list plus one week grid per class, faculty member and room."""
        inst, tt = _exportable(jid)
        buf = io.BytesIO()
        build_xlsx(inst, tt, buf)
        buf.seek(0)
        return StreamingResponse(buf, media_type=XLSX, headers=_attachment("timetable.xlsx"))

    @app.get("/jobs/{jid}/export.pdf")
    def export_pdf(jid: str, kind: Literal["batch", "faculty", "room"] | None = None, id: str | None = None):
        """PDF, one landscape page per entity. Without `kind` everything is exported;
        with `kind` and `id`, just that class, faculty member or room."""
        if id is not None and kind is None:
            raise HTTPException(422, "`id` requires `kind`")
        inst, tt = _exportable(jid)
        buf = io.BytesIO()
        try:
            build_pdf(inst, tt, buf, kind, id, font_path=pdf_font)
        except LookupError:
            raise HTTPException(404, f"{kind} not found") from None
        buf.seek(0)
        name = "timetable" + (f"-{kind}" if kind else "") + (f"-{id}" if id else "") + ".pdf"
        return StreamingResponse(buf, media_type="application/pdf", headers=_attachment(name))

    @app.get("/jobs/{jid}/views/{kind}/{ident}")
    def view(jid: str, kind: Literal["batch", "faculty", "room"], ident: str):
        """One batch's, faculty member's or room's week, with names resolved for display."""
        job = job_or_404(jid)
        inst = institution_or_404(job["institution_id"])
        tt = store.get_timetable(jid)
        if tt is None:
            raise HTTPException(409, f"no timetable: job is {job['status']}")

        try:
            sessions = session_views(inst, tt, kind, ident)
        except LookupError:
            raise HTTPException(404, f"{kind} not found") from None
        return {"kind": kind, "id": ident, "sessions": sessions}  # lectures are zero-based

    return app
