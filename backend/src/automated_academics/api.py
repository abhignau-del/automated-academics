"""FastAPI service.

Run:  uvicorn automated_academics.api:create_app --factory --reload
Docs: http://127.0.0.1:8000/docs

There is no authentication yet: run it on localhost or behind your own access
control. Do not expose it to the public internet as is.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import __version__
from .checks import check_institution, diagnose
from .excel_io import ImportErrors, export_workbook, import_workbook
from .export import build_pdf, build_xlsx
from .models import Institution, Placement, Timetable
from .explain import explain_message
from . import auth as authlib
from .auth import LOCAL_USER, User
from .decompose import solve_auto
from .solver import InfeasibleError, Unsatisfiable
from .store import Conflict, Store
from .synthetic import sample_institution
from .quality import measure, score
from .validate import find_conflict_details
from .views import entities, session_views
from .grid_io import GridError, GridOptions, import_grid
from .mail import Mailer, SmtpMailer, normalise_email, reset_message
from .scope import institution_violations, timetable_violations
from .changes import describe_changes, describe_timetable_edit
from .workload_io import WorkloadError, WorkloadOptions, import_workload

log = logging.getLogger("automated_academics")

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class WorkloadOptionsIn(BaseModel):
    """What the import dialog sends; bounds keep a typo from building a nonsense institution."""

    days: list[str] = Field(default=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"], min_length=1, max_length=7)
    lectures_per_day: int = Field(default=4, ge=1, le=12)
    break_after: list[int] = Field(default=[2], max_length=6)
    classrooms: int | None = Field(default=None, ge=1, le=500)
    seats: int | None = Field(default=None, ge=1, le=2000)
    labs: int = Field(default=0, ge=0, le=100)
    lab_seats: int | None = Field(default=None, ge=1, le=2000)
    joint_max_students: int = Field(default=0, ge=0, le=5000)
    default_students: int = Field(default=60, ge=1, le=2000)

    def to_options(self) -> WorkloadOptions:
        return WorkloadOptions(**self.model_dump())


class GridOptionsIn(BaseModel):
    """What the "import an existing timetable" dialog sends."""

    sheets: list[str] | None = Field(default=None, max_length=100)
    default_students: int = Field(default=60, ge=1, le=2000)
    seats: int = Field(default=70, ge=1, le=2000)
    spare_rooms: int | None = Field(default=None, ge=0, le=50)
    merge_consecutive: bool = True

    def to_options(self) -> GridOptions:
        return GridOptions(**self.model_dump())


class ImportedTimetable(BaseModel):
    placements: list[Placement] = Field(max_length=20000)


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


_RANK = {"viewer": 1, "editor": 2, "owner": 3}
_PUBLIC = {"/health", "/auth/status", "/auth/login", "/auth/setup", "/auth/forgot", "/auth/reset"}
_PUBLIC_PREFIX = "/public/"  # published, link-only timetables: no account needed
# what every answer about a published timetable says: don't keep it, don't index it, don't pass the address on
_PUBLIC_HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"}
_ALL_KINDS = ("batch", "faculty", "room")
SESSION_COOKIE = "aa_session"


class Credentials(BaseModel):
    username: str
    password: str


class NewUser(BaseModel):
    username: str
    password: str
    display_name: str = ""
    email: str = ""
    role: Literal["admin", "member"] = "member"


class ForgotIn(BaseModel):
    who: str = Field(max_length=254)  # a username or an e-mail address


class ResetIn(BaseModel):
    token: str = Field(max_length=200)
    new: str


class UserChanges(BaseModel):
    display_name: str | None = None
    email: str | None = None
    password: str | None = None
    role: Literal["admin", "member"] | None = None
    disabled: bool | None = None


class PasswordChange(BaseModel):
    current: str
    new: str


class PublishIn(BaseModel):
    job_id: str | None = None  # default: the institution's latest finished timetable
    label: str = Field(default="", max_length=80)
    kinds: list[Literal["batch", "faculty", "room"]] = Field(default=["batch"], min_length=1, max_length=3)
    expires_days: int | None = Field(default=None, ge=1, le=3650)  # None: the link never expires


class PublishPatch(BaseModel):
    job_id: str | None = None
    label: str | None = Field(default=None, max_length=80)
    kinds: list[Literal["batch", "faculty", "room"]] | None = Field(default=None, min_length=1, max_length=3)
    expires_days: int | None = Field(default=None, ge=1, le=3650)  # send null to make it never expire


class MemberRole(BaseModel):
    role: Literal["owner", "editor", "viewer"]
    # An editor can be limited to some departments; omit (or null) for the whole institution.
    departments: list[str] | None = Field(default=None, max_length=200)


_CSV_ROWS = 50_000  # most rows one CSV download holds


RESET_MINUTES = 60


def create_app(db_path: str | None = None, workers: int = 1, pdf_font: str | None = None,
               static_dir: str | None = None, auth: bool | None = None, mailer: Mailer | None = None,
               public_url: str | None = None) -> FastAPI:
    """`auth` switches on accounts (default: the AA_AUTH environment variable). Off, everyone is one local
    administrator, which is how the single-user desktop app works."""
    store = Store(db_path or os.environ.get("AA_DB", "automated_academics.db"))
    auth_on = auth if auth is not None else os.environ.get("AA_AUTH", "") not in ("", "0")
    secure_cookie = os.environ.get("AA_COOKIE_SECURE", "") not in ("", "0")
    throttle = authlib.LoginThrottle()
    bad_links = authlib.LoginThrottle(limit=30, lock_s=60.0)  # slows down anyone guessing published-link addresses
    forgot_throttle = authlib.LoginThrottle(limit=5, lock_s=600.0)  # a few reset e-mails per address, then a pause
    bad_resets = authlib.LoginThrottle(limit=10, lock_s=300.0)  # guessing reset links
    mailer = mailer if mailer is not None else SmtpMailer.from_env()
    public_url = (public_url or os.environ.get("AA_PUBLIC_URL", "")).strip().rstrip("/")
    reset_on = auth_on and mailer is not None and bool(public_url)  # no mail server or no known address: no "forgot password"
    mail_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mail")  # sending is slow and must not show who has an account

    def session_user(request: Request) -> User | None:
        token = request.cookies.get(SESSION_COOKIE)
        header = request.headers.get("authorization", "")
        if not token and header.lower().startswith("bearer "):
            token = header[7:].strip()
        if not token:
            return None
        row = store.user_for_session(authlib.token_hash(token))
        user = authlib.user_from_row(row) if row else None
        return None if user is None or user.disabled else user

    def login_required(request: Request) -> None:
        """Runs before every route: sets request.state.user, or answers 401 (the public few excepted)."""
        if not auth_on:
            request.state.user = LOCAL_USER
            return
        user = session_user(request)
        request.state.user = user
        if user is None and request.url.path not in _PUBLIC and not request.url.path.startswith(_PUBLIC_PREFIX):
            raise HTTPException(401, "please sign in")

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
        mail_pool.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="Automated Academics", version=__version__, lifespan=lifespan,
                  dependencies=[Depends(login_required)])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],  # React dev server
        allow_methods=["*"], allow_headers=["*"], allow_credentials=True, expose_headers=["ETag"],
    )

    # ---------------- helpers ----------------
    def me(request: Request) -> User:
        return request.state.user

    def audit(request: Request | None, action: str, what: str, iid: str | None = None, detail: dict[str, Any] | None = None,
              actor: User | str | None = None, institution: str | None = None) -> None:
        """Write one line to the activity log (shared mode only). Never raises: a failing log must not stop the work.

        `what` is the sentence after the person's name. Never pass a password, a session token or a link token."""
        if not auth_on:
            return
        try:
            user = actor if isinstance(actor, User) else (getattr(request.state, "user", None) if request is not None and actor is None else None)
            if isinstance(actor, str):  # someone who is not (yet) signed in, e.g. a failed sign-in: only the typed name
                aid, aname, who = None, actor[:80], actor[:80]
            elif user is not None:
                aid, aname, who = user.id, user.username, user.display_name or user.username
            else:
                aid, aname, who = None, "system", "The solver"
            ip = request.client.host if request is not None and request.client else None
            name = institution if institution is not None else (store.institution_name(iid) if iid else None)
            store.add_audit(aid, aname, action, f"{who} {what}", iid, name, detail, ip)
        except Exception:  # noqa: BLE001 - fail open
            log.exception("could not write the audit log")

    def allow(request: Request, iid: str, level: str) -> None:
        """Let the signed-in user through if they may `level` ("viewer" / "editor" / "owner") this institution."""
        user = me(request)
        if user.is_admin:
            return
        have = store.member_role(iid, user.id)
        if have is None:
            raise HTTPException(404, "institution not found")  # don't reveal that it exists
        if _RANK[have] < _RANK[level]:
            raise HTTPException(403, f"you have {have} access to this institution; {level} access is needed")

    def scope_of(request: Request, iid: str) -> set[str] | None:
        """The departments the signed-in person is limited to on this institution; None = the whole of it."""
        user = me(request)
        if user.is_admin or not auth_on:
            return None
        if store.member_role(iid, user.id) != "editor":
            return None
        deps = store.member_departments(iid, user.id)
        return set(deps) if deps else None

    def whole_institution_only(request: Request, iid: str, what: str) -> None:
        if scope_of(request, iid) is not None:
            audit(request, "access.refused", f"was refused: tried to {what}", iid)
            raise HTTPException(403, detail={
                "message": f"You are an editor for some departments only, so you can't {what}. "
                           "Ask an editor of the whole institution.", "scoped": True})

    def institution_or_404(iid: str, request: Request | None = None, level: str = "viewer") -> Institution:
        inst = store.get_institution(iid)
        if inst is None:
            raise HTTPException(404, "institution not found")
        if request is not None:
            allow(request, iid, level)
        return inst

    def job_or_404(jid: str, request: Request | None = None, level: str = "viewer") -> dict:
        job = store.get_job(jid)
        if job is None:
            raise HTTPException(404, "job not found")
        if request is not None:
            allow(request, job["institution_id"], level)
        return job

    def etag(response: Response, iid: str) -> None:
        version = store.institution_version(iid)
        if version is not None:
            response.headers["ETag"] = f'"{version}"'

    def expected_version(request: Request) -> int | None:
        raw = request.headers.get("if-match", "").strip().strip('"')
        try:
            return int(raw) if raw else None
        except ValueError:
            raise HTTPException(400, "bad If-Match header") from None

    def with_stale(job: dict) -> dict:
        """Add `stale`: the institution's data was edited after this timetable was generated."""
        updated = store.institution_updated_at(job["institution_id"])
        return {**job, "stale": updated is not None and job["created_at"] < updated}

    def run_job(jid: str, inst: Institution, limit: float) -> None:
        store.set_running(jid)
        iid = store.get_job(jid)["institution_id"]
        try:
            tt = solve_auto(inst, time_limit_s=limit)
            store.finish_job(jid, tt)
            audit(None, "timetable.finished", f"finished a timetable ({tt.status.lower()})", iid, {"job": jid, "status": tt.status},
                  institution=inst.name)
        except Unsatisfiable as e:  # proven impossible: say which of the user's constraints clash
            store.fail_job(jid, explain_message(inst, str(e)))
            audit(None, "timetable.failed", "could not make a timetable: the rules clash", iid, {"job": jid}, institution=inst.name)
        except InfeasibleError as e:
            store.fail_job(jid, str(e))
            audit(None, "timetable.failed", "could not make a timetable", iid, {"job": jid}, institution=inst.name)
        except Exception:  # noqa: BLE001 - never leave a job stuck in "running"
            log.exception("job %s crashed", jid)
            store.fail_job(jid, "internal error while solving")
            audit(None, "timetable.failed", "failed while making a timetable", iid, {"job": jid}, institution=inst.name)

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
    def list_institutions(request: Request):
        user = me(request)
        return store.list_institutions(None if user.is_admin else user.id)

    @app.get("/institutions/starter", response_model=Institution)
    def starter(kind: Literal["blank", "sample"] = "blank"):
        """Data to start a new institution from: an empty one, or the fictional sample."""
        if kind == "sample":
            return sample_institution()
        return Institution(name="New institution", rooms=[], faculty=[], batches=[], courses=[], offerings=[])

    @app.post("/institutions/import-workload")
    def import_workload_list(file: UploadFile = File(...), options: str = Form("{}"), sheet: str | None = Form(None)):
        """Read a flat workload list (subject, teacher, programme, hours...) into a draft institution.

        Nothing is saved: the draft and a report of every assumption and oddity come back for the user
        to review, then the draft is created with POST /institutions.
        """
        try:
            opt = WorkloadOptionsIn.model_validate(json.loads(options or "{}"))
        except (ValueError, TypeError) as e:
            raise HTTPException(422, f"bad options: {e}") from None
        data = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"file larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        try:
            inst, report = import_workload(io.BytesIO(data), opt.to_options(), sheet=sheet or None)
        except WorkloadError as e:
            raise HTTPException(422, str(e)) from None
        except (zipfile.BadZipFile, KeyError, OSError):
            raise HTTPException(400, "not a valid .xlsx workbook") from None
        _, issues = check_institution(inst.model_dump(mode="json"))
        return {"institution": inst.model_dump(mode="json"), "report": report.as_dict(),
                "issues": [i.as_dict() for i in (issues or diagnose(inst))]}

    @app.post("/institutions/import-grid")
    def import_grid_timetable(file: UploadFile = File(...), options: str = Form("{}")):
        """Read an existing grid timetable (one block per class, days across, lectures down) into a draft.

        Nothing is saved: the draft, a report of everything assumed or merged, the existing timetable's quality
        and its placements come back for review. Create it with POST /institutions, then keep the existing
        timetable with POST /institutions/{id}/timetable/imported.
        """
        try:
            opt = GridOptionsIn.model_validate(json.loads(options or "{}"))
        except (ValueError, TypeError) as e:
            raise HTTPException(422, f"bad options: {e}") from None
        data = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"file larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        try:
            inst, report, placements = import_grid(io.BytesIO(data), opt.to_options())
        except GridError as e:
            raise HTTPException(422, str(e)) from None
        except (zipfile.BadZipFile, KeyError, OSError):
            raise HTTPException(400, "not a valid .xlsx workbook") from None
        _, issues = check_institution(inst.model_dump(mode="json"))
        return {"institution": inst.model_dump(mode="json"), "report": report.as_dict(),
                "issues": [i.as_dict() for i in (issues or diagnose(inst))],
                "placements": [pl.model_dump(mode="json") for pl in placements]}

    @app.post("/institutions/check")
    def check_draft(data: dict[str, Any] = Body(...)):
        """Check institution data without saving it, for live feedback while it is being edited.

        `valid` says whether it can be saved; `issues` are located by section and row.
        Impossible-to-schedule data is reported as errors but does not make it invalid.
        """
        inst, issues = check_institution(data)
        if inst is not None:
            issues = diagnose(inst)
        return {"valid": inst is not None, "issues": [i.as_dict() for i in issues]}

    def owner_of(request: Request) -> str | None:
        """Whoever creates an institution owns it (with sign-in off there are no owners)."""
        return me(request).id if auth_on else None

    def _valid_or_422(data: dict[str, Any]) -> tuple[Institution, list[dict]]:
        inst, issues = check_institution(data)
        if inst is None:
            raise HTTPException(422, detail={
                "message": f"{len(issues)} problem(s) in the data",
                "issues": [i.as_dict() for i in issues],
            })
        return inst, [i.as_dict() for i in diagnose(inst)]

    @app.post("/institutions", status_code=201)
    def create_institution(request: Request, data: dict[str, Any] = Body(...)):
        inst, notes = _valid_or_422(data)
        iid = store.add_institution(inst, owner_id=owner_of(request))
        audit(request, "institution.created", f"created the institution \u201c{inst.name}\u201d", iid)
        return {"id": iid, "name": inst.name, "issues": notes}

    @app.put("/institutions/{iid}")
    def update_institution(iid: str, request: Request, response: Response, data: dict[str, Any] = Body(...)):
        """Replace an institution's data. Existing timetables are kept but become `stale`.

        Send the version you read (the ETag of GET) as `If-Match` and the save is refused with 409 if somebody
        else saved in between, instead of silently overwriting their work."""
        before = institution_or_404(iid, request, "editor")
        inst, notes = _valid_or_422(data)
        scope = scope_of(request, iid)
        if scope is not None:
            bad = institution_violations(before, inst, scope)
            if bad:
                audit(request, "access.refused", "was refused: tried to change data outside their departments", iid,
                      {"violations": bad[:20]})
                raise HTTPException(403, detail={
                    "message": "You can change only the data of " + ", ".join(sorted(scope)) + ". Not allowed: " + "; ".join(bad[:6])
                               + (f"; and {len(bad) - 6} more" if len(bad) > 6 else "") + ".",
                    "scoped": True, "violations": bad})
        try:
            store.update_institution(iid, inst, expected_version(request))
        except Conflict:
            raise HTTPException(409, detail={
                "message": "Someone else saved changes to this institution after you opened it. "
                           "Reload to see their changes, then make yours again.",
                "conflict": True}) from None
        what, detail = describe_changes(before, inst)
        if what:
            audit(request, "institution.updated", what, iid, detail)
        etag(response, iid)
        return {"id": iid, "name": inst.name, "issues": notes, "version": store.institution_version(iid)}

    @app.get("/institutions/{iid}/workbook.xlsx")
    def institution_workbook(iid: str, request: Request):
        """The saved data as an Excel workbook in the same format the upload accepts: a backup, or a
        way to share it. (Not the timetable; see /jobs/{id}/export.xlsx for that.)"""
        inst = institution_or_404(iid, request)
        buf = io.BytesIO()
        export_workbook(inst, buf)
        buf.seek(0)
        return StreamingResponse(buf, media_type=XLSX, headers=_attachment(f"{inst.name}-data.xlsx"))

    @app.delete("/institutions/{iid}", status_code=204)
    def delete_institution(iid: str, request: Request):
        """Permanently delete an institution and all of its timetables."""
        institution_or_404(iid, request, "owner")
        name = store.institution_name(iid)
        if not store.delete_institution(iid):
            raise HTTPException(404, "institution not found")
        audit(request, "institution.deleted", f"deleted the institution \u201c{name}\u201d and all its timetables", iid, institution=name)

    @app.post("/institutions/upload", status_code=201)
    def upload_institution(request: Request, file: UploadFile = File(...)):
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
        iid = store.add_institution(inst, owner_id=owner_of(request))
        audit(request, "institution.created", f"created the institution \u201c{inst.name}\u201d from a workbook", iid)
        return {"id": iid, "name": inst.name, "issues": [i.as_dict() for i in diagnose(inst)]}

    @app.get("/institutions/{iid}", response_model=Institution)
    def get_institution(iid: str, request: Request, response: Response):
        inst = institution_or_404(iid, request)
        etag(response, iid)
        return inst

    @app.post("/institutions/{iid}/solve", status_code=202)
    def start_solve(iid: str, request: Request, req: SolveRequest | None = None):
        inst = institution_or_404(iid, request, "editor")
        whole_institution_only(request, iid, "generate a timetable (it replaces everyone's)")
        limit = (req or SolveRequest()).time_limit_s
        jid = store.create_job(iid, limit)
        audit(request, "timetable.generated", "asked for a new timetable", iid, {"job": jid, "time_limit_s": limit})
        pool.submit(run_job, jid, inst, limit)
        return {"job_id": jid, "status": "queued"}

    @app.post("/institutions/{iid}/timetable/imported", status_code=201)
    def keep_imported_timetable(iid: str, body: ImportedTimetable, request: Request):
        """Store an existing timetable (as read by import-grid) as this institution's first finished timetable,
        so it can be looked at, compared with and replaced by a generated one. Clashes are reported, not refused."""
        inst = institution_or_404(iid, request, "editor")
        whole_institution_only(request, iid, "replace the institution's timetable")
        offerings = {o.id: o for o in inst.offerings}
        rooms = {r.id for r in inst.rooms}
        for pl in body.placements:
            o = offerings.get(pl.offering_id)
            if o is None or pl.session_index >= len(o.sessions) or pl.room_id not in rooms or pl.day >= inst.calendar.days:
                raise HTTPException(422, f"placement of {pl.offering_id} does not belong to this institution")
        tt = Timetable(placements=body.placements, status="IMPORTED")
        jid = store.create_job(iid, 0)
        store.finish_job(jid, tt)
        audit(request, "timetable.imported_kept", f"kept an existing timetable of {len(tt.placements)} sessions", iid, {"job": jid})
        return {"job_id": jid, "report": _report(inst, tt)}

    @app.post("/institutions/{iid}/validate", response_model=ConflictReport)
    def validate_timetable(iid: str, tt: Timetable, request: Request):
        """Check a (possibly hand-edited) timetable; the basis for live clash detection."""
        return _report(institution_or_404(iid, request), tt)

    @app.get("/institutions/{iid}/latest-job")
    def latest_job(iid: str, request: Request):
        institution_or_404(iid, request)
        job = store.latest_done_job(iid)
        if job is None:
            raise HTTPException(404, "no finished timetable for this institution yet")
        return with_stale(job)

    # ---------------- jobs and timetables ----------------
    @app.get("/jobs/{jid}")
    def get_job(jid: str, request: Request):
        return with_stale(job_or_404(jid, request))

    @app.get("/jobs/{jid}/timetable", response_model=Timetable)
    def get_timetable(jid: str, request: Request):
        job = job_or_404(jid, request)
        tt = store.get_timetable(jid)
        if tt is None:
            raise HTTPException(409, f"no timetable: job is {job['status']}"
                                + (f" ({job['error']})" if job["error"] else ""))
        return tt

    @app.put("/jobs/{jid}/timetable", response_model=ConflictReport)
    def save_timetable(jid: str, tt: Timetable, request: Request):
        """Save a hand-edited timetable. Clashes are reported but do not block saving."""
        job = job_or_404(jid, request, "editor")
        if store.get_timetable(jid) is None:
            raise HTTPException(409, f"job is {job['status']}; only finished jobs can be edited")
        inst = institution_or_404(job["institution_id"])
        scope = scope_of(request, job["institution_id"])
        if scope is not None:
            bad = timetable_violations(inst, store.get_timetable(jid), tt, scope)
            if bad:
                audit(request, "access.refused", "was refused: tried to move sessions outside their departments",
                      job["institution_id"], {"violations": bad[:20]})
                raise HTTPException(403, detail={
                    "message": "You can move only sessions of " + ", ".join(sorted(scope)) + ". Not allowed: " + "; ".join(bad[:6]) + ".",
                    "scoped": True, "violations": bad})
        what, detail = describe_timetable_edit(store.get_timetable(jid), tt)
        store.save_timetable(jid, tt.model_copy(update={"status": "MANUAL"}))
        if what:
            audit(request, "timetable.edited", what, job["institution_id"], {"job": jid, **detail})
        return _report(inst, tt)

    def _exportable(jid: str, request: Request) -> tuple[Institution, Timetable]:
        job = job_or_404(jid, request)
        tt = store.get_timetable(jid)
        if tt is None:
            raise HTTPException(409, f"no timetable: job is {job['status']}")
        return institution_or_404(job["institution_id"]), tt

    @app.get("/jobs/{jid}/export.xlsx")
    def export_xlsx(jid: str, request: Request):
        """Excel workbook: a flat session list plus one week grid per class, faculty member and room."""
        inst, tt = _exportable(jid, request)
        buf = io.BytesIO()
        build_xlsx(inst, tt, buf)
        buf.seek(0)
        return StreamingResponse(buf, media_type=XLSX, headers=_attachment("timetable.xlsx"))

    @app.get("/jobs/{jid}/export.pdf")
    def export_pdf(jid: str, request: Request, kind: Literal["batch", "faculty", "room"] | None = None, id: str | None = None):
        """PDF, one landscape page per entity. Without `kind` everything is exported;
        with `kind` and `id`, just that class, faculty member or room."""
        if id is not None and kind is None:
            raise HTTPException(422, "`id` requires `kind`")
        inst, tt = _exportable(jid, request)
        buf = io.BytesIO()
        try:
            build_pdf(inst, tt, buf, kind, id, font_path=pdf_font)
        except LookupError:
            raise HTTPException(404, f"{kind} not found") from None
        buf.seek(0)
        name = "timetable" + (f"-{kind}" if kind else "") + (f"-{id}" if id else "") + ".pdf"
        return StreamingResponse(buf, media_type="application/pdf", headers=_attachment(name))

    @app.get("/jobs/{jid}/views/{kind}/{ident}")
    def view(jid: str, request: Request, kind: Literal["batch", "faculty", "room"], ident: str):
        """One batch's, faculty member's or room's week, with names resolved for display."""
        job = job_or_404(jid, request)
        inst = institution_or_404(job["institution_id"])
        tt = store.get_timetable(jid)
        if tt is None:
            raise HTTPException(409, f"no timetable: job is {job['status']}")

        try:
            sessions = session_views(inst, tt, kind, ident)
        except LookupError:
            raise HTTPException(404, f"{kind} not found") from None
        return {"kind": kind, "id": ident, "sessions": sessions}  # lectures are zero-based

    # ---------------- sign-in ----------------
    def start_session(response: Response, user_id: str) -> None:
        token = authlib.new_token()
        store.create_session(user_id, authlib.token_hash(token), authlib.SESSION_DAYS)
        response.set_cookie(SESSION_COOKIE, token, max_age=int(authlib.SESSION_DAYS * 86400), httponly=True,
                            samesite="lax", secure=secure_cookie, path="/")

    @app.get("/auth/status")
    def auth_status(request: Request):
        """What the screen needs before it can show anything: is sign-in on, is first-run setup needed, who am I."""
        user = request.state.user if auth_on else None
        return {"auth": auth_on, "setup_needed": auth_on and store.count_users() == 0,
                "user": user.public() if user else None, "reset_enabled": reset_on}

    @app.post("/auth/setup", status_code=201)
    def auth_setup(body: NewUser, request: Request, response: Response):
        """Create the first administrator. Only works while there are no users at all."""
        if not auth_on:
            raise HTTPException(409, "sign-in is switched off")
        if store.count_users() > 0:
            raise HTTPException(409, "already set up")
        user = _create_user(body.model_copy(update={"role": "admin"}))
        start_session(response, user["id"])
        audit(request, "auth.setup", "set up the first administrator account", actor=authlib.user_from_row(user))
        return authlib.user_from_row(user).public()

    def _free_email(raw: str, mine: str | None = None) -> str:
        try:
            email = normalise_email(raw)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        other = store.get_user_by_email(email) if email else None
        if other is not None and other["id"] != mine:
            raise HTTPException(409, "another account already uses that e-mail address")
        return email

    def _create_user(body: NewUser) -> dict:
        try:
            name = authlib.normalise_username(body.username)
            authlib.check_password_strength(body.password)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        if store.get_user_by_name(name):
            raise HTTPException(409, "that username is taken")
        email = _free_email(body.email)
        uid = store.add_user(name, body.display_name.strip() or name, authlib.hash_password(body.password), body.role, email)
        return store.get_user(uid)

    def send_reset(row: dict) -> None:
        token = authlib.new_token()
        store.create_reset(row["id"], authlib.token_hash(token), RESET_MINUTES * 60)
        subject, text = reset_message(row["display_name"], f"{public_url}/#/reset/{token}", RESET_MINUTES)
        try:
            mailer.send(row["email"], subject, text)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 - the person is told nothing either way; the administrator reads the log
            log.exception("could not send a password-reset e-mail")

    @app.post("/auth/forgot", status_code=202)
    def forgot_password(body: ForgotIn, request: Request):
        """Ask for an e-mail with a link to choose a new password. The answer is the same whoever (or nobody) the name matches."""
        if not reset_on:
            raise HTTPException(409, "password reset by e-mail is not switched on; ask your administrator to set a new password")
        ip = request.client.host if request.client else ""
        wait = forgot_throttle.wait_s(ip)
        if wait > 0:
            raise HTTPException(429, f"too many requests; try again in {int(wait) + 1} seconds")
        forgot_throttle.failed(ip)  # counts every request, not only failures
        who = body.who.strip().lower()
        row = store.get_user_by_name(who) or (store.get_user_by_email(who) if "@" in who else None)
        sendable = row is not None and not row["disabled"] and bool(row["email"]) and not store.recent_reset(row["id"], 60)
        audit(request, "auth.reset_requested", "asked for a password-reset e-mail", actor=who[:80] or "(blank)",
              detail={"known_user": row is not None, "sent": sendable})
        if sendable:
            mail_pool.submit(send_reset, row)
        return {"ok": True, "message": "If that account has an e-mail address, a link to choose a new password is on its way."}

    @app.post("/auth/reset")
    def reset_password(body: ResetIn, request: Request):
        """Choose a new password with the e-mailed link. The link works once and for an hour; it ends every sign-in of that person."""
        if not reset_on:
            raise HTTPException(409, "password reset by e-mail is not switched on")
        ip = request.client.host if request.client else ""
        wait = bad_resets.wait_s(ip)
        if wait > 0:
            raise HTTPException(429, f"too many wrong links; try again in {int(wait) + 1} seconds")
        try:
            authlib.check_password_strength(body.new)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        uid = store.take_reset(authlib.token_hash(body.token))
        row = store.get_user(uid) if uid else None
        if row is None or row["disabled"]:
            bad_resets.failed(ip)
            raise HTTPException(400, "this link is not valid any more; ask for a new one")
        store.update_user(uid, password_hash=authlib.hash_password(body.new))
        store.delete_user_sessions(uid)
        audit(request, "auth.password_reset", "chose a new password from an e-mailed link", actor=authlib.user_from_row(row))
        return {"ok": True}

    @app.post("/auth/login")
    def login(body: Credentials, request: Request, response: Response):
        if not auth_on:
            raise HTTPException(409, "sign-in is switched off")
        name = body.username.strip().lower()
        key = f"{name}|{request.client.host if request.client else ''}"
        wait = throttle.wait_s(key)
        if wait > 0:
            raise HTTPException(429, f"too many attempts; try again in {int(wait) + 1} seconds")
        row = store.get_user_by_name(name)
        good = row is not None and authlib.verify_password(body.password, row["password_hash"]) and not row["disabled"]
        if row is None:
            authlib.burn_time()
        if not good:
            throttle.failed(key)
            audit(request, "auth.login_failed", "failed to sign in", actor=name or "(blank)",
                  detail={"known_user": row is not None})
            raise HTTPException(401, "wrong username or password")
        throttle.succeeded(key)
        start_session(response, row["id"])
        audit(request, "auth.login", "signed in", actor=authlib.user_from_row(row))
        return authlib.user_from_row(row).public()

    @app.post("/auth/logout", status_code=204)
    def logout(request: Request, response: Response):
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            if getattr(request.state, "user", None) is not None:
                audit(request, "auth.logout", "signed out")
            store.delete_session(authlib.token_hash(token))
        response.delete_cookie(SESSION_COOKIE, path="/")

    @app.post("/auth/password", status_code=204)
    def change_password(body: PasswordChange, request: Request):
        user = me(request)
        row = store.get_user(user.id)
        if row is None or not authlib.verify_password(body.current, row["password_hash"]):
            raise HTTPException(403, "the current password is wrong")
        try:
            authlib.check_password_strength(body.new)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        store.update_user(user.id, password_hash=authlib.hash_password(body.new))
        audit(request, "auth.password_changed", "changed their own password")
        store.delete_user_sessions(user.id)  # other devices must sign in again; the caller is signed out too
        request.state.user = None

    def admin_only(request: Request) -> User:
        user = me(request)
        if not auth_on or not user.is_admin:
            raise HTTPException(403, "only an administrator can do that")
        return user

    def last_admin(uid: str) -> bool:
        admins = [u for u in store.list_users() if u["role"] == "admin" and not u["disabled"]]
        return len(admins) == 1 and admins[0]["id"] == uid

    @app.get("/users")
    def list_users(request: Request):
        admin_only(request)
        return [{**authlib.user_from_row(u).public(), "disabled": bool(u["disabled"]), "email": u["email"]} for u in store.list_users()]

    @app.post("/users", status_code=201)
    def add_user(body: NewUser, request: Request):
        admin_only(request)
        created = authlib.user_from_row(_create_user(body))
        audit(request, "user.created", f"created the account {created.username} ({created.role})", detail={"user": created.username, "role": created.role})
        return created.public()

    @app.patch("/users/{uid}")
    def change_user(uid: str, body: UserChanges, request: Request):
        admin_only(request)
        row = store.get_user(uid)
        if row is None:
            raise HTTPException(404, "user not found")
        fields: dict[str, Any] = {}
        if body.display_name is not None:
            fields["display_name"] = body.display_name.strip() or row["username"]
        if body.email is not None:
            fields["email"] = _free_email(body.email, uid)
        if body.password is not None:
            try:
                authlib.check_password_strength(body.password)
            except ValueError as e:
                raise HTTPException(422, str(e)) from None
            fields["password_hash"] = authlib.hash_password(body.password)
        if body.role is not None and body.role != row["role"]:
            if row["role"] == "admin" and last_admin(uid):
                raise HTTPException(409, "there must be at least one administrator")
            fields["role"] = body.role
        if body.disabled is not None:
            if body.disabled and last_admin(uid):
                raise HTTPException(409, "there must be at least one administrator")
            fields["disabled"] = int(body.disabled)
        store.update_user(uid, **fields)
        if body.disabled or "password_hash" in fields:
            store.delete_user_sessions(uid)
        said = []
        if "display_name" in fields:
            said.append("renamed them")
        if "password_hash" in fields:
            said.append("reset their password")
        if "email" in fields:
            said.append("changed their e-mail address")
        if "role" in fields:
            said.append(f"made them {'an administrator' if fields['role'] == 'admin' else 'an ordinary member'}")
        if "disabled" in fields:
            said.append("switched the account off" if fields["disabled"] else "switched the account back on")
        if said:
            audit(request, "user.changed", f"changed the account {row['username']}: " + ", ".join(said),
                  detail={"user": row["username"], "fields": sorted(k.replace("password_hash", "password") for k in fields)})
        return authlib.user_from_row(store.get_user(uid)).public()

    @app.delete("/users/{uid}", status_code=204)
    def remove_user(uid: str, request: Request):
        admin = admin_only(request)
        if store.get_user(uid) is None:
            raise HTTPException(404, "user not found")
        if uid == admin.id or last_admin(uid):
            raise HTTPException(409, "you can't delete the last administrator, or yourself")
        gone = store.get_user(uid)["username"]
        store.delete_user(uid)
        audit(request, "user.deleted", f"deleted the account {gone}", detail={"user": gone})

    # ---------------- publishing a timetable by link ----------------
    def link_view(p: dict, request: Request) -> dict:
        return {"id": p["id"], "job_id": p["job_id"], "label": p["label"], "kinds": p["kinds"], "token": p["token"],
                "path": f"/#/p/{p['token']}", "created_at": p["created_at"], "expires_at": p["expires_at"],
                "expired": _expired(p)}

    def _expired(p: dict) -> bool:
        return bool(p["expires_at"]) and p["expires_at"] < datetime.now(timezone.utc).isoformat(timespec="microseconds")

    def finished_job_of(iid: str, job_id: str) -> dict:
        job = store.get_job(job_id)
        if job is None or job["institution_id"] != iid:
            raise HTTPException(422, "that timetable doesn't belong to this institution")
        if store.get_timetable(job_id) is None:
            raise HTTPException(409, "that timetable isn't finished yet")
        return job

    def publisher(request: Request, iid: str) -> None:
        if not auth_on:
            raise HTTPException(409, "publishing needs shared mode (sign-in), because the link has to be reachable by other people")
        institution_or_404(iid, request, "editor")
        whole_institution_only(request, iid, "publish the timetable (it is the whole institution's)")

    @app.get("/institutions/{iid}/publications")
    def list_links(iid: str, request: Request):
        institution_or_404(iid, request, "editor")
        return [link_view(p, request) for p in store.list_publications(iid)]

    @app.post("/institutions/{iid}/publications", status_code=201)
    def publish(iid: str, body: PublishIn, request: Request):
        """Make a finished timetable readable by anyone with a link. They need no account; the link can be revoked."""
        publisher(request, iid)
        job_id = body.job_id
        if job_id is None:
            latest = store.latest_done_job(iid)
            if latest is None:
                raise HTTPException(409, "generate a timetable first")
            job_id = latest["id"]
        finished_job_of(iid, job_id)
        kinds = [k for k in _ALL_KINDS if k in body.kinds]
        row = store.add_publication(iid, job_id, authlib.new_token(), body.label.strip(), kinds, me(request).id, body.expires_days)
        audit(request, "publication.created", f"published a timetable link \u201c{row['label'] or 'untitled'}\u201d", iid,
              {"label": row["label"], "kinds": kinds, "expires_days": body.expires_days, "job": job_id})
        return link_view(row, request)

    @app.patch("/institutions/{iid}/publications/{pid}")
    def change_link(iid: str, pid: str, body: PublishPatch, request: Request):
        publisher(request, iid)
        if store.get_publication(pid, iid) is None:
            raise HTTPException(404, "no such link")
        fields: dict[str, Any] = {}
        if body.job_id is not None:
            finished_job_of(iid, body.job_id)
            fields["job_id"] = body.job_id
        if body.label is not None:
            fields["label"] = body.label.strip()
        if body.kinds is not None:
            fields["kinds"] = [k for k in _ALL_KINDS if k in body.kinds]
        if "expires_days" in body.model_fields_set:
            fields["expires_at"] = (datetime.now(timezone.utc) + timedelta(days=body.expires_days)).isoformat(timespec="microseconds") \
                if body.expires_days else None
        store.update_publication(pid, iid, **fields)
        row = store.get_publication(pid, iid)
        audit(request, "publication.changed", f"changed the timetable link \u201c{row['label'] or 'untitled'}\u201d ({', '.join(sorted(fields))})", iid,
              {"label": row["label"], "fields": sorted(fields)})
        return link_view(row, request)

    @app.delete("/institutions/{iid}/publications/{pid}", status_code=204)
    def revoke(iid: str, pid: str, request: Request):
        publisher(request, iid)
        label = (store.get_publication(pid, iid) or {}).get("label")
        if not store.delete_publication(pid, iid):
            raise HTTPException(404, "no such link")
        audit(request, "publication.withdrawn", f"withdrew the timetable link \u201c{label or 'untitled'}\u201d", iid, {"label": label})

    # what anyone holding a link can see: only the timetable it was made for, only the views that were chosen
    def open_link(token: str, request: Request, response: Response) -> tuple[dict, Institution, Timetable]:
        ip = request.client.host if request.client else ""
        response.headers.update(_PUBLIC_HEADERS)
        wait = bad_links.wait_s(ip)
        if wait > 0:
            raise HTTPException(429, f"too many wrong addresses; try again in {int(wait) + 1} seconds")
        row = store.get_publication_by_token(token)
        if row is None:
            bad_links.failed(ip)
            raise HTTPException(404, "this link is not valid (it may have been withdrawn)")
        if _expired(row):
            raise HTTPException(410, "this link has expired")
        inst, tt = store.get_institution(row["institution_id"]), store.get_timetable(row["job_id"])
        if inst is None or tt is None:
            raise HTTPException(404, "this timetable is no longer available")
        return row, inst, tt

    @app.get("/public/{token}")
    def public_meta(token: str, request: Request, response: Response):
        row, inst, _tt = open_link(token, request, response)
        return {
            "institution": inst.name, "label": row["label"], "kinds": row["kinds"], "published_at": row["created_at"],
            "expires_at": row["expires_at"], "calendar": inst.calendar.model_dump(mode="json"),
            "entities": {k: [{"id": i, "label": lab, **({"clock": inst.clock_of_batch(i)} if k == "batch" else {})}
                             for i, lab in entities(inst, k)] for k in row["kinds"]},
            "everything": sorted(row["kinds"]) == sorted(_ALL_KINDS),
        }

    @app.get("/public/{token}/views/{kind}/{ident}")
    def public_view(token: str, kind: Literal["batch", "faculty", "room"], ident: str, request: Request, response: Response):
        row, inst, tt = open_link(token, request, response)
        if kind not in row["kinds"]:
            raise HTTPException(404, f"{kind} timetables are not part of this link")
        try:
            sessions = session_views(inst, tt, kind, ident)
        except LookupError:
            raise HTTPException(404, f"{kind} not found") from None
        return {"kind": kind, "id": ident, "sessions": sessions}

    @app.get("/public/{token}/export.pdf")
    def public_pdf(token: str, request: Request, kind: Literal["batch", "faculty", "room"] | None = None, id: str | None = None):
        response = Response()
        row, inst, tt = open_link(token, request, response)
        if id is not None and kind is None:
            raise HTTPException(422, "`id` requires `kind`")
        if (kind is None and sorted(row["kinds"]) != sorted(_ALL_KINDS)) or (kind is not None and kind not in row["kinds"]):
            raise HTTPException(404, "that is not part of this link")
        buf = io.BytesIO()
        try:
            build_pdf(inst, tt, buf, kind, id, font_path=pdf_font)
        except LookupError:
            raise HTTPException(404, f"{kind} not found") from None
        buf.seek(0)
        name = "timetable" + (f"-{kind}" if kind else "") + (f"-{id}" if id else "") + ".pdf"
        return StreamingResponse(buf, media_type="application/pdf", headers={**_attachment(name), **_PUBLIC_HEADERS})

    @app.get("/public/{token}/export.xlsx")
    def public_xlsx(token: str, request: Request):
        response = Response()
        row, inst, tt = open_link(token, request, response)
        if sorted(row["kinds"]) != sorted(_ALL_KINDS):
            raise HTTPException(404, "the workbook includes every view, which this link does not")
        buf = io.BytesIO()
        build_xlsx(inst, tt, buf)
        buf.seek(0)
        return StreamingResponse(buf, media_type=XLSX, headers={**_attachment("timetable.xlsx"), **_PUBLIC_HEADERS})

    # ---------------- sharing an institution ----------------
    @app.get("/institutions/{iid}/members")
    def members(iid: str, request: Request):
        institution_or_404(iid, request, "owner")
        return store.list_members(iid)

    @app.put("/institutions/{iid}/members/{username}")
    def share(iid: str, username: str, body: MemberRole, request: Request):
        """Give someone access (or change it). Only an owner may; owners can do everything including deleting."""
        institution_or_404(iid, request, "owner")
        target = store.get_user_by_name(username.strip().lower())
        if target is None or target["disabled"]:
            raise HTTPException(404, "no such user")
        owners = [m for m in store.list_members(iid) if m["role"] == "owner"]
        if body.role != "owner" and len(owners) == 1 and owners[0]["id"] == target["id"]:
            raise HTTPException(409, "an institution needs at least one owner")
        departments = [d.strip() for d in (body.departments or []) if d.strip()] or None
        if departments and body.role != "editor":
            raise HTTPException(422, "only an editor can be limited to some departments")
        store.set_member(iid, target["id"], body.role, departments)
        audit(request, "member.set", f"gave {target['username']} {body.role} access"
              + (" limited to " + ", ".join(sorted(departments)) if departments else ""), iid,
              {"user": target["username"], "role": body.role, "departments": sorted(departments) if departments else None})
        return store.list_members(iid)

    @app.delete("/institutions/{iid}/members/{username}")
    def unshare(iid: str, username: str, request: Request):
        institution_or_404(iid, request, "owner")
        target = store.get_user_by_name(username.strip().lower())
        if target is None:
            raise HTTPException(404, "no such user")
        owners = [m for m in store.list_members(iid) if m["role"] == "owner"]
        if len(owners) == 1 and owners[0]["id"] == target["id"]:
            raise HTTPException(409, "an institution needs at least one owner")
        store.remove_member(iid, target["id"])
        audit(request, "member.removed", f"removed {target['username']}'s access", iid, {"user": target["username"]})
        return store.list_members(iid)

    # ---------------- the activity log ----------------
    def logging_on() -> None:
        if not auth_on:
            raise HTTPException(409, "the activity log needs shared mode (sign-in); nothing is recorded without it")

    def clean_limit(limit: int) -> int:
        return max(1, min(limit, 200))

    def csv_cell(v: Any) -> str:
        text = "" if v is None else str(v)
        return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text  # a spreadsheet must not run a cell as a formula

    def audit_csv(rows: list[dict]) -> StreamingResponse:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["When (UTC)", "Who", "What", "Institution", "Action", "From"])
        for r in rows:
            w.writerow([csv_cell(r["at"]), csv_cell(r["actor"]), csv_cell(r["summary"]), csv_cell(r["institution"]),
                        csv_cell(r["action"]), csv_cell(r["ip"])])
        return StreamingResponse(io.BytesIO(("\ufeff" + buf.getvalue()).encode("utf-8")), media_type="text/csv",
                                 headers=_attachment("activity-log.csv"))

    @app.get("/audit")
    def audit_all(request: Request, user: str | None = None, action: str | None = None, institution: str | None = None,
                  since: str | None = None, until: str | None = None, q: str | None = None, before: int | None = None,
                  limit: int = 50):
        logging_on()
        admin_only(request)
        return store.list_audit(institution, user, action, since, until, q, before, clean_limit(limit))

    @app.get("/audit.csv")
    def audit_all_csv(request: Request, user: str | None = None, action: str | None = None, institution: str | None = None,
                      since: str | None = None, until: str | None = None, q: str | None = None):
        logging_on()
        admin_only(request)
        return audit_csv(store.list_audit(institution, user, action, since, until, q, None, _CSV_ROWS))

    @app.get("/audit/verify")
    def audit_verify(request: Request):
        """Recompute the chain of hashes; says where the first altered or missing entry is, if there is one."""
        logging_on()
        admin_only(request)
        return store.verify_audit()

    @app.get("/institutions/{iid}/audit")
    def institution_audit(iid: str, request: Request, user: str | None = None, action: str | None = None,
                          since: str | None = None, until: str | None = None, q: str | None = None,
                          before: int | None = None, limit: int = 50):
        logging_on()
        institution_or_404(iid, request, "owner")
        return store.list_audit(iid, user, action, since, until, q, before, clean_limit(limit))

    @app.get("/institutions/{iid}/audit.csv")
    def institution_audit_csv(iid: str, request: Request, user: str | None = None, action: str | None = None,
                              since: str | None = None, until: str | None = None, q: str | None = None):
        logging_on()
        institution_or_404(iid, request, "owner")
        return audit_csv(store.list_audit(iid, user, action, since, until, q, None, _CSV_ROWS))

    # The packaged app serves the built screen itself, from the same address as the engine.
    # Mounted last so every API route above takes priority.
    static_dir = static_dir or os.environ.get("AA_STATIC") or None
    if static_dir and os.path.isfile(os.path.join(static_dir, "index.html")):
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="screen")

    return app
