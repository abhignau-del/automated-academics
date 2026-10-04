"""Domain model.

An *Offering* is one course delivered by one faculty member to one or more
batches. Listing several batches models CBCS/NEP shared electives (MDC, VAC,
AEC, open electives) that students from different programs attend together.
Each offering is split into weekly *sessions*; a lab is typically one session
of 2-3 consecutive periods.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class RoomKind(str, Enum):
    CLASSROOM = "classroom"
    LAB = "lab"
    HALL = "hall"


class Level(str, Enum):
    UG = "UG"
    PG = "PG"


class Slot(BaseModel):
    day: int = Field(ge=0)
    period: int = Field(ge=0)


class Calendar(BaseModel):
    """Weekly grid. Days and periods are zero-based indices."""

    day_names: list[str] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    periods_per_day: int = Field(default=7, ge=1)
    # A block may not span the boundary after any period listed here
    # (e.g. [3] = lunch between the 4th and 5th period).
    break_after: list[int] = [3]

    @property
    def days(self) -> int:
        return len(self.day_names)

    def block_fits(self, start: int, length: int) -> bool:
        end = start + length - 1
        if start < 0 or end >= self.periods_per_day:
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
    max_periods_per_day: int = Field(default=6, ge=1)


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
    # Length (in periods) of each weekly session, e.g. [1, 1, 1] or [2].
    sessions: list[int] = Field(min_length=1)

    @model_validator(mode="after")
    def _positive_sessions(self) -> "Offering":
        if any(s < 1 for s in self.sessions):
            raise ValueError("session lengths must be >= 1")
        return self


class Institution(BaseModel):
    name: str
    calendar: Calendar = Calendar()
    rooms: list[Room]
    faculty: list[Faculty]
    batches: list[Batch]
    courses: list[Course]
    offerings: list[Offering]

    @model_validator(mode="after")
    def _references_exist(self) -> "Institution":
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
