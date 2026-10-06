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


def scaled_institution(copies: int) -> Institution:
    """`copies` independent copies of the sample institution (ids suffixed ~0, ~1, ...).

    Used to measure how the solver scales: 1 copy is about 68 weekly sessions, 4 copies is about
    a large department's worth. Copies share nothing, so the optimum is `copies` times the sample's.
    """
    base = sample_institution()
    if copies == 1:
        return base
    pools: dict[str, list] = {k: [] for k in ("rooms", "faculty", "batches", "courses", "offerings")}
    for n in range(copies):
        data = base.model_dump(mode="json")
        ids = {r["id"] for k in ("rooms", "faculty", "batches", "offerings") for r in data[k]}
        ids |= {c["code"] for c in data["courses"]}

        def suffix(x, tag=f"~{n}"):
            if isinstance(x, str):
                return x + tag if x in ids else x
            if isinstance(x, list):
                return [suffix(y) for y in x]
            if isinstance(x, dict):
                return {a: suffix(b) for a, b in x.items()}
            return x

        data = suffix(data)
        for k in pools:
            pools[k] += data[k]
    return Institution(name=f"{base.name} x{copies}", calendar=base.calendar, weights=base.weights, **pools)


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
                    # a soft preference: nobody wants the last two lectures on Saturday
                    avoid=[Slot(day=5, lecture=5), Slot(day=5, lecture=6)],
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


def university_institution(departments: int = 10, classes_per_dept: int = 8, seed: int = 1) -> Institution:
    """A fictional university-sized institution that, unlike `scaled_institution`, couples its departments.

    Rooms, labs and halls are one shared pool; about 8% of courses are taught by a teacher from the next
    department; and a few open electives bring classes from different departments together in a hall.
    Each department has `classes_per_dept` classes (semesters 1/3/5/7 x sections, 60 students each, labs in
    two groups). Roughly 22 weekly sessions per class, so 10 x 8 classes is about 1,800 sessions.
    Deterministic for a given `seed`.
    """
    import math
    import random

    rng = random.Random(seed)
    classes_total = departments * classes_per_dept
    rooms = [Room(id=f"C{i}", name=f"Classroom {i}", capacity=70) for i in range(1, math.ceil(classes_total * 0.6) + 1)]
    rooms += [Room(id=f"L{i}", name=f"Lab {i}", capacity=35, kind=RoomKind.LAB)
              for i in range(1, math.ceil(classes_total * 0.15) + 1)]
    rooms += [Room(id=f"H{i}", name=f"Hall {i}", capacity=300, kind=RoomKind.HALL)
              for i in range(1, max(2, math.ceil(classes_total / 40)) + 1)]

    batches: list[Batch] = []
    courses: list[Course] = []
    plan: list[tuple[str, str, list[str], list[int], str]] = []  # (offering id, course, batch ids, sessions, dept)
    top_classes: list[Batch] = []
    for d in range(departments):
        dept = f"D{d + 1:02d}"
        for sem in (1, 3, 5, 7)[: max(1, math.ceil(classes_per_dept / 2))]:
            for sec in "AB":
                if len([b for b in batches if b.department == dept and b.group_of is None]) >= classes_per_dept:
                    break
                bid = f"{dept}-S{sem}{sec}"
                b = Batch(id=bid, program=f"UG {dept}", level=Level.UG, semester=sem, section=sec, department=dept, strength=60)
                batches.append(b)
                top_classes.append(b)
                for g in "12":
                    batches.append(Batch(id=f"{bid}-G{g}", program=b.program, level=Level.UG, semester=sem, section=sec,
                                         department=dept, strength=30, group_of=bid))
                for k in range(1, 7):
                    code = f"{dept}-S{sem}-T{k}"
                    if not any(c.code == code for c in courses):
                        courses.append(Course(code=code, name=f"{dept} Sem {sem} Theory {k}", department=dept, credits=3))
                    plan.append((f"O-{bid}-T{k}", code, [bid], [1, 1, 1], dept))
                lab = f"{dept}-S{sem}-LAB"
                if not any(c.code == lab for c in courses):
                    courses.append(Course(code=lab, name=f"{dept} Sem {sem} Lab", department=dept, credits=2,
                                          category="LAB", room_kind=RoomKind.LAB))
                for g in "12":
                    plan.append((f"O-{bid}-LAB-G{g}", lab, [f"{bid}-G{g}"], [2], dept))

    # open electives: classes from different departments, same semester, together in a hall
    electives = max(1, classes_total // 16)
    pool = [b for b in top_classes if b.semester == 1]
    rng.shuffle(pool)
    for e in range(electives):
        picks: list[Batch] = []
        for b in list(pool):
            if all(p.department != b.department for p in picks):
                picks.append(b)
                pool.remove(b)
            if len(picks) == 4:
                break
        if len(picks) < 2:
            break
        code = f"OE{e + 1}"
        courses.append(Course(code=code, name=f"Open Elective {e + 1}", department=picks[0].department, credits=3,
                              category="MDC", room_kind=RoomKind.HALL))
        plan.append((f"O-{code}", code, [p.id for p in picks], [1, 1, 1], picks[0].department))

    # teachers: enough per department for about 13 lecture-hours each, handing courses out in turn
    hours: dict[str, int] = {}
    for _, _, _, sessions, dept in plan:
        hours[dept] = hours.get(dept, 0) + sum(sessions)
    faculty: list[Faculty] = []
    by_dept: dict[str, list[str]] = {}
    for dept, h in hours.items():
        for n in range(1, math.ceil(h / 13) + 1):
            fid = f"{dept}F{n}"
            unavailable = [Slot(day=rng.randrange(6), lecture=rng.randrange(7)) for _ in range(6)] if rng.random() < 0.1 else []
            avoid = [Slot(day=5, lecture=5), Slot(day=5, lecture=6)] if rng.random() < 0.3 else []
            faculty.append(Faculty(id=fid, name=f"{dept} Faculty {n}", department=dept, unavailable=unavailable, avoid=avoid,
                                   max_lectures_per_day=5))
            by_dept.setdefault(dept, []).append(fid)
    depts = list(by_dept)
    cursor = {d: 0 for d in depts}
    offerings: list[Offering] = []
    for oid, code, bids, sessions, dept in plan:
        home = dept
        if rng.random() < 0.08:
            home = depts[(depts.index(dept) + 1) % len(depts)]  # taught by a colleague from the next department
        fid = by_dept[home][cursor[home] % len(by_dept[home])]
        cursor[home] += 1
        offerings.append(Offering(id=oid, course_code=code, faculty_id=fid, batch_ids=bids, sessions=sessions))

    return Institution(name=f"University of {departments} departments (fictional)", rooms=rooms, faculty=faculty,
                       batches=batches, courses=courses, offerings=offerings)
