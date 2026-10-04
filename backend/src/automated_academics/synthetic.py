"""Deterministic fictional institution for tests, demos and documentation.

No real institutional data belongs in this repository.
"""

from __future__ import annotations

from .models import (
    Batch,
    Course,
    Faculty,
    Institution,
    Level,
    Offering,
    Room,
    RoomKind,
    Slot,
)

DEPARTMENTS = {"CS": "Computer Science", "MG": "Management"}


def sample_institution() -> Institution:
    rooms = [Room(id=f"C{i}", name=f"Classroom {i}", capacity=70) for i in range(1, 5)]
    rooms += [Room(id=f"L{i}", name=f"Lab {i}", capacity=40, kind=RoomKind.LAB) for i in range(1, 3)]
    rooms.append(Room(id="H1", name="Seminar Hall", capacity=150, kind=RoomKind.HALL))

    faculty = []
    for dept in DEPARTMENTS:
        for n in range(1, 5):
            faculty.append(
                Faculty(
                    id=f"{dept}F{n}",
                    name=f"{dept} Faculty {n}",
                    department=dept,
                    # one fixed day-off lecture per faculty to exercise availability
                    unavailable=[Slot(day=n % 6, lecture=0)],
                )
            )

    batches = [
        Batch(id="CS-UG1", program="BSc CS", level=Level.UG, semester=1, department="CS", strength=60),
        Batch(id="CS-UG3", program="BSc CS", level=Level.UG, semester=3, department="CS", strength=55),
        Batch(id="CS-PG1", program="MSc CS", level=Level.PG, semester=1, department="CS", strength=25),
        Batch(id="MG-UG1", program="BBA", level=Level.UG, semester=1, department="MG", strength=60),
        Batch(id="MG-PG1", program="MBA", level=Level.PG, semester=1, department="MG", strength=30),
        # practical groups: labs seat 40, so large UG batches split in two
        Batch(id="CS-UG1-A", program="BSc CS", level=Level.UG, semester=1, section="A",
              department="CS", strength=30, group_of="CS-UG1"),
        Batch(id="CS-UG1-B", program="BSc CS", level=Level.UG, semester=1, section="B",
              department="CS", strength=30, group_of="CS-UG1"),
        Batch(id="CS-UG3-A", program="BSc CS", level=Level.UG, semester=3, section="A",
              department="CS", strength=28, group_of="CS-UG3"),
        Batch(id="CS-UG3-B", program="BSc CS", level=Level.UG, semester=3, section="B",
              department="CS", strength=27, group_of="CS-UG3"),
    ]

    courses: list[Course] = []
    offerings: list[Offering] = []
    fac_cursor = {d: 0 for d in DEPARTMENTS}

    def next_faculty(dept: str) -> str:
        fac_cursor[dept] += 1
        return f"{dept}F{(fac_cursor[dept] - 1) % 4 + 1}"

    for b in (x for x in batches if x.group_of is None):
        for k in range(1, 5):
            code = f"{b.id}-T{k}"
            courses.append(Course(code=code, name=f"{b.program} Sem {b.semester} Theory {k}",
                                  department=b.department, credits=3))
            offerings.append(Offering(id=f"O-{code}", course_code=code,
                                      faculty_id=next_faculty(b.department),
                                      batch_ids=[b.id], sessions=[1, 1, 1]))
        if b.department == "CS":
            code = f"{b.id}-LAB"
            courses.append(Course(code=code, name=f"{b.program} Sem {b.semester} Lab",
                                  department="CS", credits=2, category="LAB",
                                  room_kind=RoomKind.LAB))
            groups = [x.id for x in batches if x.group_of == b.id] or [b.id]
            for g in groups:
                offerings.append(Offering(id=f"O-{code}-{g}", course_code=code,
                                          faculty_id=next_faculty("CS"),
                                          batch_ids=[g], sessions=[2]))

    # NEP multidisciplinary elective shared by UG Sem 1 of two departments
    courses.append(Course(code="MDC101", name="Design Thinking (MDC)", department="MG",
                          credits=3, category="MDC", room_kind=RoomKind.HALL))
    offerings.append(Offering(id="O-MDC101", course_code="MDC101", faculty_id="MGF1",
                              batch_ids=["CS-UG1", "MG-UG1"], sessions=[1, 1, 1]))

    return Institution(
        name="Sample University (fictional)",
        rooms=rooms, faculty=faculty, batches=batches,
        courses=courses, offerings=offerings,
    )
