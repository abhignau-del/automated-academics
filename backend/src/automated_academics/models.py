"""Domain model.

An *Offering* is one course delivered by one faculty member to one or more
batches. Listing several batches models CBCS/NEP shared electives (MDC, VAC,
AEC, open electives) that students from different programs attend together.
Each offering is split into weekly *sessions*; a lab is typically one session
of 2-3 consecutive lectures.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


class RoomKind(str, Enum):
    CLASSROOM = "classroom"
    LAB = "lab"
    HALL = "hall"


class Level(str, Enum):
    UG = "UG"
    PG = "PG"


class Slot(BaseModel):
    day: int = Field(ge=0)
    lecture: int = Field(ge=0)


_CLOCK = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _minutes(clock: str) -> int:
    h, m = clock.split(":")
    return int(h) * 60 + int(m)


class LectureTime(BaseModel):
    """When one lecture of the day runs, as 24-hour "HH:MM" clock times (shown as e.g. 9:30-10:30)."""

    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def _clock(cls, v: str) -> str:
        m = _CLOCK.match(v.strip())
        if not m:
            raise ValueError(f"{v!r} is not a time like 9:30 or 14:05")
        return f"{int(m.group(1)):02d}:{m.group(2)}"

    @model_validator(mode="after")
    def _ordered(self) -> "LectureTime":
        if _minutes(self.end) <= _minutes(self.start):
            raise ValueError(f"a lecture must end after it starts ({self.start} to {self.end})")
        return self

    @staticmethod
    def pretty(clock: str) -> str:
        h, m = clock.split(":")
        return f"{int(h)}:{m}"


class Calendar(BaseModel):
    """Weekly grid. Days and lectures are zero-based indices."""

    day_names: list[str] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    lectures_per_day: int = Field(default=7, ge=1)
    # A block may not span the boundary after any lecture listed here
    # (e.g. [3] = lunch between the 4th and 5th lecture).
    break_after: list[int] = [3]
    # Optional clock times of the lectures, one per lecture (empty = just "L1", "L2", ...), plus
    # per-day replacements for days that run to a different clock (e.g. a short Saturday).
    times: list[LectureTime] = []
    day_times: dict[int, list[LectureTime]] = {}

    @model_validator(mode="after")
    def _times_make_sense(self) -> "Calendar":
        def check(rows: list[LectureTime], what: str) -> None:
            if len(rows) != self.lectures_per_day:
                raise ValueError(f"{what} needs a time for each of the {self.lectures_per_day} lectures, "
                                 f"but has {len(rows)}")
            for i in range(1, len(rows)):
                if _minutes(rows[i].start) < _minutes(rows[i - 1].end):
                    raise ValueError(f"{what}: lecture {i + 1} starts before lecture {i} ends")

        if self.times:
            check(self.times, "the lecture times")
        for d, rows in self.day_times.items():
            if not 0 <= d < len(self.day_names):
                raise ValueError(f"lecture times given for day {d + 1}, which is not one of the {len(self.day_names)} days")
            check(rows, f"the lecture times for {self.day_names[d]}")
        return self

    def times_on(self, day: int) -> list[LectureTime]:
        return self.day_times.get(day) or self.times

    def time_label(self, day: int, start: int, length: int = 1) -> str | None:
        """"9:30-10:30" for the block starting at lecture `start` on `day`, or None if no times are set."""
        rows = self.times_on(day)
        if not rows or start < 0 or start + length > len(rows):
            return None
        return f"{LectureTime.pretty(rows[start].start)}\u2013{LectureTime.pretty(rows[start + length - 1].end)}"

    @property
    def days(self) -> int:
        return len(self.day_names)

    def block_fits(self, start: int, length: int) -> bool:
        end = start + length - 1
        if start < 0 or end >= self.lectures_per_day:
            return False
        return not any(start <= b < end for b in self.break_after)


class Room(BaseModel):
    id: str
    name: str
    capacity: int = Field(gt=0)
    kind: RoomKind = RoomKind.CLASSROOM


class Faculty(BaseModel):
    id: str
    name: str
    department: str
    unavailable: list[Slot] = []
    # Soft preference: the solver tries not to schedule this person here, but may if it must.
    avoid: list[Slot] = []
    max_lectures_per_day: int = Field(default=6, ge=1)


class Weights(BaseModel):
    """Relative importance of the soft goals. Higher means the solver works harder to satisfy it.

    Set a weight to 0 to ignore that goal. Names match the metrics in `quality.measure`.
    """

    repeat_course_day: int = Field(default=5, ge=0)  # per extra session of one course on the same day
    batch_gaps: int = Field(default=10, ge=0)  # per idle lecture inside a class's day
    faculty_gaps: int = Field(default=3, ge=0)  # per idle lecture inside a faculty member's day
    peak_day_load: int = Field(default=4, ge=0)  # per lecture on a class's busiest day (spreads the week)
    avoid_slot: int = Field(default=6, ge=0)  # per lecture a faculty member asked to avoid


class Batch(BaseModel):
    """A fixed cohort of students (program + semester + section)."""

    id: str
    program: str
    level: Level
    semester: int = Field(ge=1)
    section: str = "A"
    department: str
    strength: int = Field(gt=0)
    # Set for a practical/tutorial sub-group (e.g. lab batch A of a section).
    # The parent's sessions occupy every sub-group; sub-group sessions occupy
    # only themselves (the parent is free to run other sub-groups in parallel).
    group_of: str | None = None


class Course(BaseModel):
    code: str
    name: str
    department: str
    credits: int = Field(ge=0)
    # CBCS/NEP category, e.g. "DSC", "DSE", "MDC", "AEC", "SEC", "VAC", "LAB"
    category: str = "DSC"
    room_kind: RoomKind = RoomKind.CLASSROOM


class Offering(BaseModel):
    id: str
    course_code: str
    faculty_id: str
    batch_ids: list[str] = Field(min_length=1)
    # Length (in lectures) of each weekly session, e.g. [1, 1, 1] or [2].
    sessions: list[int] = Field(min_length=1)

    @model_validator(mode="after")
    def _positive_sessions(self) -> "Offering":
        if any(s < 1 for s in self.sessions):
            raise ValueError("session lengths must be >= 1")
        return self


class Pin(BaseModel):
    """A session fixed in place: the solver must put it here and schedules everything else around it.

    Lectures and days are zero-based, like everywhere else in the data.
    """

    offering_id: str
    session_index: int = Field(ge=0)
    day: int = Field(ge=0)
    start: int = Field(ge=0)
    room_id: str | None = None  # None leaves the room to the solver


class Institution(BaseModel):
    name: str
    calendar: Calendar = Calendar()
    rooms: list[Room]
    faculty: list[Faculty]
    batches: list[Batch]
    courses: list[Course]
    offerings: list[Offering]
    weights: Weights = Weights()
    pins: list[Pin] = []

    @model_validator(mode="after")
    def _references_exist(self) -> "Institution":
        offering_sessions = {o.id: len(o.sessions) for o in self.offerings}
        room_ids = {r.id for r in self.rooms}
        for p in self.pins:
            if p.offering_id not in offering_sessions:
                raise ValueError(f"pin: unknown offering {p.offering_id}")
            if p.session_index >= offering_sessions[p.offering_id]:
                raise ValueError(f"pin: {p.offering_id} has no session {p.session_index + 1}")
            if p.room_id is not None and p.room_id not in room_ids:
                raise ValueError(f"pin: unknown room {p.room_id}")
        faculty = {f.id for f in self.faculty}
        batches = {b.id for b in self.batches}
        courses = {c.code for c in self.courses}
        for o in self.offerings:
            if o.faculty_id not in faculty:
                raise ValueError(f"{o.id}: unknown faculty {o.faculty_id}")
            if o.course_code not in courses:
                raise ValueError(f"{o.id}: unknown course {o.course_code}")
            missing = set(o.batch_ids) - batches
            if missing:
                raise ValueError(f"{o.id}: unknown batches {sorted(missing)}")
        for b in self.batches:
            if b.group_of is not None and b.group_of not in batches:
                raise ValueError(f"{b.id}: unknown parent batch {b.group_of}")
        return self

    def occupied_batches(self, batch_ids: list[str]) -> set[str]:
        """Batch ids blocked when `batch_ids` attend: themselves plus their sub-groups."""
        out = set(batch_ids)
        out.update(b.id for b in self.batches if b.group_of in out)
        return out

    def leaf_batches(self) -> list[str]:
        """Batches whose schedule is what a student actually experiences: those with no sub-groups.

        A section split into lab groups is represented by its groups (each group's day includes
        the parent's lectures), so the parent itself is skipped.
        """
        parents = {b.group_of for b in self.batches if b.group_of}
        return [b.id for b in self.batches if b.id not in parents]


class Placement(BaseModel):
    offering_id: str
    session_index: int
    day: int
    start: int
    length: int
    room_id: str


class Timetable(BaseModel):
    placements: list[Placement]
    status: str
    penalty: int = 0
    # Soft-goal metrics (see quality.measure); filled in by the solver.
    breakdown: dict[str, int] = {}
