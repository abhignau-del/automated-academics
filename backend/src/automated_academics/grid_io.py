"""Import an *existing grid timetable* into an Institution.

Most colleges already have their timetable as grids in a spreadsheet: one block per class, days across,
lectures down, a "Subject (Teacher)" in every cell. This reads that directly, so a college can start from
what it already has instead of re-typing it:

  * a block is found by its row of day names (Mon..Sat, Monday..); the class name is the nearest heading
    above it, and a room in brackets there ("(E Block, Room 714)") is taken as that class's room;
  * the times written down the side ("8 TO 8.50") become the lecture times, and a longer gap becomes a break;
  * a cell is split into subject and teacher in the usual ways ("Maths (Prof. Rao)", two lines,
    "Maths - Prof. Rao", "Maths Prof. Rao"), and a "Subject | Faculty" table beside the grid fills in
    cells that name no teacher;
  * cells listing several things in one slot ("1) A  2) B") are parallel electives: each option becomes a
    sub-group of the class;
  * different spellings of one teacher or subject are merged, and every merge is reported so it can be checked;
  * lectures in a row on one day for the same subject and teacher become one double lecture (switchable).

Nothing is guessed silently: what the sheet does not say (student numbers, room sizes, which subjects are labs)
is filled with stated assumptions, and the existing timetable is scored (clashes, idle gaps) and returned so
it can be kept as the first timetable.
"""

from __future__ import annotations

import difflib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

from openpyxl import load_workbook

from .excel_io import _times
from .models import (
    Batch, Calendar, Course, Faculty, Institution, LectureTime, Level, Offering, Placement, Room, Timetable,
)
from .quality import measure
from .validate import find_conflicts
from .workload_io import _code_for, _initials

_DAYS = {
    "mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4, "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}
_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_TITLE_WORDS = re.compile(r"\b(prof|professor|dr|mr|mrs|ms|miss|sir|madam|maam|ma'am|mam|shri|smt)\b\.?", re.I)
_BOILERPLATE = re.compile(r"^(from|date|w\.?e\.?f|week|academic|session|year|valid|effective|time\s*table|timetable|time)\b", re.I)
_EMPTY = re.compile(r"^[\s\-–—_.*]*$|^(free|nil|off|na|n/a|holiday)$", re.I)
_NOT_A_PERSON = re.compile(r"\b(lab|room|block|hall|theory|practical|tutorial|elective|group|batch|section|div|class|hr|hrs)\b", re.I)
_PERSON_VIEW = re.compile(r"^(prof|professor|dr|mr|mrs|ms|shri|smt)\b", re.I)
_SHEET_NOT_CLASSES = re.compile(r"individual|faculty|teacher|staff|room\s*wise|summary", re.I)


@dataclass
class GridOptions:
    sheets: list[str] | None = None  # which sheets to read (default: every sheet that has timetable blocks)
    default_students: int = 60  # the grid doesn't say how big a class is
    seats: int = 70
    spare_rooms: int | None = None  # extra rooms for parallel electives (default: as many as needed)
    merge_consecutive: bool = True  # lectures in a row for one subject and teacher become a double lecture


@dataclass
class GridReport:
    sheets: list[dict[str, Any]] = field(default_factory=list)  # {name, blocks, used, note}
    classes: list[dict[str, Any]] = field(default_factory=list)  # {name, sessions, slots, room}
    assumptions: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)  # spellings that were treated as one
    parallel: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # things worth a second look
    existing: dict[str, Any] = field(default_factory=dict)  # the existing timetable, scored

    def as_dict(self) -> dict[str, Any]:
        return {"sheets": self.sheets, "classes": self.classes, "assumptions": self.assumptions, "merged": self.merged,
                "parallel": self.parallel, "problems": self.problems, "existing": self.existing}


class GridError(ValueError):
    """No timetable grids could be read from the workbook."""


@dataclass
class _Block:
    sheet: str
    title: str
    room: str | None
    days: list[int]  # the day index of each used column, left to right
    time_labels: list[str]  # one per lecture row
    cells: dict[tuple[int, int], str]  # (day index, lecture) -> raw cell text
    breaks: list[int]  # explicit break rows: a break after these lectures (zero-based)


# ------------------------------------------------------------------ reading blocks

def _text(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(re.sub(r"[ \t ]+", " ", ln).strip() for ln in s.split("\n") if ln.strip())


def _flat(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\n", " ")).strip()


def _day_of(text: str) -> int | None:
    return _DAYS.get(re.sub(r"[^a-z]", "", text.lower()))


def _norm_name(n: str) -> str:
    n = _TITLE_WORDS.sub(" ", n.lower())
    return " ".join(re.sub(r"[^a-z ]", " ", n).split())


def _tok_eq(a: str, b: str) -> bool:
    if a == b:
        return True
    if len(a) == 1 or len(b) == 1:
        return a[0] == b[0]
    return len(a) >= 3 and len(b) >= 3 and (a.startswith(b) or b.startswith(a))


def _within(small: set[str], big: set[str]) -> bool:
    """Every word of `small` is (an abbreviation of) a different word of `big`."""
    pool = list(big)
    for t in small:
        hit = next((x for x in pool if _tok_eq(t, x)), None)
        if hit is None:
            return False
        pool.remove(hit)
    return bool(small)


def _class_and_room(title: str) -> tuple[str, str | None]:
    name = _flat(title)
    room = None
    m = re.search(r"\broom\s*(?:no\.?|number|#)?[\s_:.\-]*([A-Za-z0-9\-]+)", name, re.I)
    if m:
        room = m.group(1).strip("-_.")
    name = re.sub(r"\(.*", "", name).strip(" -–:,")
    return name or _flat(title), room


def _blocks(ws, sheet: str) -> list[_Block]:
    rows: dict[int, dict[int, str]] = {}
    for row in ws.iter_rows():
        for c in row:
            if c.value not in (None, ""):
                t = _text(c.value)
                if t:
                    rows.setdefault(c.row, {})[c.column] = t
    out: list[_Block] = []
    headers = []
    for r, cells in sorted(rows.items()):
        days = {col: _day_of(t) for col, t in cells.items() if _day_of(t) is not None and len(t) <= 12}
        if len(set(days.values())) >= 3:
            headers.append((r, days))
    header_rows = {r for r, _ in headers}
    floor_row = 0
    for r, daycols in headers:
        first, last = min(daycols), max(daycols)
        tcol = first - 1 if first > 1 else None
        cells: dict[tuple[int, int], str] = {}
        labels: list[str] = []
        breaks: list[int] = []
        rr = r + 1
        blanks = 0
        while rr <= max(rows, default=0) and rr not in header_rows:
            line = rows.get(rr, {})
            tcell = line.get(tcol, "") if tcol else ""
            filled = {c: t for c, t in line.items() if c in daycols and not _EMPTY.match(_flat(t))}
            if not line or (not tcell and not filled):
                blanks += 1
                if blanks >= 1:
                    break
            blanks = 0
            if tcol and tcell and not re.search(r"\d", tcell):  # "LUNCH", "BREAK", "RECESS"
                breaks.append(len(labels) - 1)
                rr += 1
                continue
            if tcol and not tcell:
                break
            slot = len(labels)
            labels.append(_flat(tcell) if tcell else f"L{slot + 1}")
            for col, t in filled.items():
                cells[(daycols[col], slot)] = t
            rr += 1
        if not labels:
            continue
        block_end = rr
        title = ""
        for tr in range(r - 1, max(r - 7, floor_row), -1):
            for col in sorted(rows.get(tr, {})):
                t = _flat(rows[tr][col])
                if col > last + 1 or _BOILERPLATE.match(t) or _day_of(t) is not None or len(t) > 140:
                    continue
                if re.fullmatch(r"[\d\W]+", t) or re.search(r"\d\s*(to|-|–)\s*\d", t, re.I):
                    continue
                title = rows[tr][col]
                break
            if title:
                break
        name, room = _class_and_room(title) if title else ("", None)
        floor_row = block_end - 1
        out.append(_Block(sheet=sheet, title=name, room=room, days=sorted(set(daycols.values())),
                          time_labels=labels, cells=cells, breaks=[b for b in breaks if b >= 0]))
    return out


def _side_tables(ws) -> list[tuple[str, str]]:
    """(subject, faculty) pairs from "Subject | Faculty" tables placed beside or below the grids."""
    cells: dict[tuple[int, int], str] = {}
    for row in ws.iter_rows():
        for c in row:
            if c.value not in (None, ""):
                cells[(c.row, c.column)] = _flat(_text(c.value))
    pairs: list[tuple[str, str]] = []
    for (r, c), t in sorted(cells.items()):
        if re.fullmatch(r"(faculty|teacher|instructor)(\s*name)?s?\s*:?", t, re.I):
            sub_col = next((cc for cc in range(c - 1, max(c - 4, 0), -1)
                            if re.search(r"subject|paper|course", cells.get((r, cc), ""), re.I)), None)
            if sub_col is None:
                continue
            rr = r + 1
            while (rr, sub_col) in cells or (rr, c) in cells:
                subj, teach = cells.get((rr, sub_col), ""), cells.get((rr, c), "")
                if subj and teach and not re.search(r"no one|not alloted|tbd|to be", teach, re.I):
                    pairs.append((subj, teach))
                rr += 1
    return pairs


# ------------------------------------------------------------------ cells

def _is_person(text: str, known: set[str]) -> bool:
    n = _norm_name(text)
    if not n:
        return False
    if _TITLE_WORDS.search(text) and not _NOT_A_PERSON.search(n):
        return True
    toks = set(n.split())
    if any(_within(toks, set(k.split())) for k in known if k):
        return True
    return len(n.split()) >= 2 and not _NOT_A_PERSON.search(n) and not re.search(r"\d", text)


def _split_options(text: str) -> list[str]:
    parts = re.split(r"(?:^|\s|\n)\d\s*\)\s*", text)
    parts = [p.strip() for p in parts if p.strip()]
    numbered = re.search(r"(?:^|\s|\n)1\s*\)", text) and re.search(r"\s2\s*\)", text)
    if numbered and len(parts) > 1:
        return parts
    return [re.sub(r"^\d\s*\)\s*", "", text)] if re.match(r"^\d\s*\)", text) else [text]


def _subject_teacher(opt: str, known: set[str]) -> tuple[str, str | None]:
    lines = [ln for ln in opt.split("\n") if ln.strip()]
    if len(lines) == 2 and _is_person(lines[1], known) and not re.search(r"\d", lines[1]):
        return _flat(lines[0]), _flat(lines[1])
    flat = _flat(opt)
    m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", flat)
    if m and m.group(1).strip() and _is_person(m.group(2), known):
        return m.group(1).strip(" -"), m.group(2).strip()
    m = re.match(r"^(.*\S)\s+(?:-|–|—|/|\|)\s+(\S.*)$", flat)
    if m and _is_person(m.group(2), known):
        return m.group(1).strip(), m.group(2).strip()
    m = re.match(r"^(.*?)\s+((?:prof|professor|dr|mr|mrs|ms|shri|smt)\b.*)$", flat, re.I)
    if m and m.group(1).strip():
        return m.group(1).strip(" -"), m.group(2).strip()
    toks = flat.split()
    for i in range(1, len(toks)):
        tail = _norm_name(" ".join(toks[i:]))
        if tail and any(_within(set(tail.split()), set(k.split())) for k in known if k):
            return " ".join(toks[:i]).strip(" -"), " ".join(toks[i:])
    return flat, None


def _cluster(names: Counter[str], threshold: float, ambiguous: list[str], person: bool = True) -> dict[str, str]:
    """Map every spelling to one representative. Tokens of one inside another, or very similar text, are
    the same person; a bare surname that fits two different people is left alone and reported."""
    reps: list[str] = []
    canon: dict[str, str] = {}
    for n in sorted(names, key=lambda x: (-len(_norm_name(x).split()), -names[x], x)):
        nn = _norm_name(n)
        toks = set(nn.split())
        matches = []
        for rep in reps:
            rt = set(_norm_name(rep).split())
            if (person and toks and (_within(toks, rt) or _within(rt, toks))) or \
                    difflib.SequenceMatcher(None, nn, _norm_name(rep)).ratio() >= threshold:
                matches.append(rep)
        if len(matches) > 1 and len(toks) == 1:
            ambiguous.append(f"{n!r} could be {' or '.join(repr(m) for m in matches)}; kept separate")
            matches = []
        if matches:
            canon[n] = matches[0]
        else:
            reps.append(n)
            canon[n] = n
    return canon


def _subject_key(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return " ".join(w for w in s.split() if w not in ("and", "for", "of", "the", "to", "in"))


def _level(name: str) -> Level:
    return Level.PG if re.match(r"\s*(m\.?\s?b\.?\s?a|m\.?\s?com|m\.?\s?sc|m\.?\s?a\b|m\.?\s?tech|pg|post)", name, re.I) else Level.UG


def _slug(s: str, taken: set[str], limit: int = 18) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")[:limit] or "C"
    out, n = base, 2
    while out in taken:
        out, n = f"{base}-{n}", n + 1
    taken.add(out)
    return out


# ------------------------------------------------------------------ the importer

def import_grid(source: str | Path | BinaryIO, options: GridOptions | None = None
                ) -> tuple[Institution, GridReport, list[Placement]]:
    opt = options or GridOptions()
    report = GridReport()
    wb = load_workbook(source, data_only=True)

    # 1. blocks on every sheet; per-teacher views and repeated classes are set aside
    chosen = [ws for ws in wb.worksheets if opt.sheets is None or ws.title in opt.sheets]
    blocks: list[_Block] = []
    side: list[tuple[str, str]] = []
    seen_titles: dict[str, str] = {}
    signatures: list[tuple[set[tuple[int, int, str]], str, str]] = []

    def signature(b: _Block) -> set[tuple[int, int, str]]:
        return {(d, sl, _subject_key(_subject_teacher(o, set())[0]))
                for (d, sl), t in b.cells.items() for o in _split_options(t)[:1]}

    for ws in chosen:
        found = _blocks(ws, ws.title)
        used = 0
        notes: list[str] = []
        for b in found:
            if (_SHEET_NOT_CLASSES.search(ws.title) and (not b.title or _PERSON_VIEW.match(b.title))) or \
                    (b.title and _PERSON_VIEW.match(b.title) and all(len(v) < 14 for v in b.cells.values())):
                notes.append(f"skipped {b.title or 'a block'}: it looks like one teacher's own timetable, which the class grids already say")
                continue
            key = re.sub(r"[^a-z0-9]", "", (b.title or "").lower())
            if key and key in seen_titles:
                notes.append(f"skipped {b.title}: it is already read from sheet {seen_titles[key]!r}")
                continue
            sig = signature(b)
            twin = next(((t, sh) for sg, t, sh in signatures
                         if len(sig) >= 3 and len(sig & sg) / max(len(sig), len(sg)) >= 0.6), None)
            if twin:
                notes.append(f"skipped {b.title or 'a block'}: it has the same subjects in the same places as "
                             f"{twin[0] or 'a class'!r} on sheet {twin[1]!r} (the same class drawn again)")
                continue
            if key:
                seen_titles[key] = ws.title
            signatures.append((sig, b.title, ws.title))
            blocks.append(b)
            used += 1
        report.sheets.append({"name": ws.title, "blocks": len(found), "used": used, "note": "; ".join(notes)})
        if used:
            side += _side_tables(ws)
    if not blocks:
        raise GridError("No timetable grids were found. A grid has a row of day names (Mon, Tue, ...) with the "
                        "lectures listed down the left.")

    # 2. the shape of the week: days, lectures, times, breaks
    day_count = Counter(d for b in blocks for d in b.days)
    days = sorted(day_count)
    lectures = max(len(b.time_labels) for b in blocks)
    cal_kwargs: dict[str, Any] = {"day_names": [_DAY_NAMES[d] for d in days], "lectures_per_day": lectures}
    day_pos = {d: i for i, d in enumerate(days)}

    label_sets = Counter(tuple(b.time_labels) for b in blocks if len(b.time_labels) == lectures and any(re.search(r"\d", x) for x in b.time_labels))
    times: list[LectureTime] = []
    if label_sets:
        best, count = label_sets.most_common(1)[0]
        try:
            times = _times("; ".join(re.sub(r"\s+", " ", x) for x in best), "lecture times")
            if len(times) != lectures:
                times = []
        except ValueError:
            times = []
        if len(label_sets) > 1:
            report.problems.append(
                f"{len(label_sets)} different sets of lecture times are written on the grids; the most common one "
                f"({count} classes) is used for everyone. Lecture times can't differ by class.")
    if times:
        report.assumptions.append(f"Lecture times: {', '.join(t.start + '-' + t.end for t in times)} (read from the first column).")
    else:
        report.assumptions.append("No lecture times could be read, so lectures are called L1, L2, ...")

    breaks = sorted({b for blk in blocks for b in blk.breaks if b < lectures - 1})
    if not breaks and times:
        mins = lambda c: int(c[:2]) * 60 + int(c[3:])  # noqa: E731
        gaps = [mins(times[i + 1].start) - mins(times[i].end) for i in range(len(times) - 1)]
        if gaps:
            med = sorted(gaps)[len(gaps) // 2]
            breaks = [i for i, g in enumerate(gaps) if g >= 15 and g >= 2 * max(med, 1)]
    if breaks:
        report.assumptions.append("A break after lecture " + ", ".join(str(b + 1) for b in breaks)
                                  + (" (a row marked as a break)." if any(blk.breaks for blk in blocks) else " (a longer gap in the times)."))
    else:
        report.assumptions.append("No break was found, so a double lecture may run across any two lectures.")
    cal = Calendar(**cal_kwargs, break_after=breaks, times=times) if times else Calendar(**cal_kwargs, break_after=breaks)
    if times:
        try:
            Calendar.model_validate(cal.model_dump())
        except ValueError:
            cal = Calendar(**cal_kwargs, break_after=breaks)
            report.problems.append("The lecture times didn't form a valid day (out of order or overlapping), so they were left out.")

    # 3. cells -> (subject, teacher), with names collected first so bare cells can be split
    known: set[str] = set()
    for b in blocks:
        for t in b.cells.values():
            for o in _split_options(t):
                subj, teach = _subject_teacher(o, set())
                m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", _flat(o))
                if m and _TITLE_WORDS.search(m.group(2)):
                    known.add(_norm_name(m.group(2)))
                elif teach:
                    known.add(_norm_name(teach))
    for _, teach in side:
        known.add(_norm_name(teach))
    known.discard("")

    parsed: dict[int, dict[tuple[int, int], list[tuple[str, str | None]]]] = {}
    raw_teachers: Counter[str] = Counter()
    raw_subjects: Counter[str] = Counter()
    for bi, b in enumerate(blocks):
        parsed[bi] = {}
        for pos, text in b.cells.items():
            opts = [_subject_teacher(o, known) for o in _split_options(text)]
            parsed[bi][pos] = opts
            for s, t in opts:
                raw_subjects[s] += 1
                if t:
                    raw_teachers[t] += 1
    for s, t in side:
        raw_teachers[t] += 0
    ambiguous: list[str] = []
    teacher_canon = _cluster(raw_teachers + Counter({t: 1 for _, t in side}), 0.86, ambiguous)
    subject_canon = _cluster(raw_subjects, 0.9, ambiguous, person=False)  # only near-identical spellings
    report.problems += ambiguous
    for label, canon, raw in (("teacher", teacher_canon, raw_teachers), ("subject", subject_canon, raw_subjects)):
        groups: dict[str, list[str]] = defaultdict(list)
        for n, c in canon.items():
            if n in raw:
                groups[c].append(n)
        for c, ns in sorted(groups.items()):
            if len(ns) > 1:
                report.merged.append(f"{label}: " + " | ".join(sorted(ns)))

    # subject -> its one teacher, from the side tables, for cells that name nobody
    by_subject: dict[str, set[str]] = defaultdict(set)
    for s, t in side:
        by_subject[_subject_key(subject_canon.get(s, s))].add(teacher_canon.get(t, t))
    unassigned: set[str] = set()

    # 4. classes, groups, courses, faculty, offerings
    taken_batch: set[str] = set()
    taken_course: set[str] = set()
    taken_fac: set[str] = set()
    batches: list[Batch] = []
    courses: dict[str, Course] = {}
    faculty: dict[str, Faculty] = {}
    offerings: list[Offering] = []
    placements: list[Placement] = []
    rooms: dict[str, Room] = {}
    class_room: dict[str, str] = {}
    spare_needed = 0
    fac_names: dict[str, Counter[str]] = defaultdict(Counter)
    for t, n in raw_teachers.items():
        fac_names[teacher_canon.get(t, t)][t] += n

    def fac_id(canon: str) -> str:
        if canon not in faculty:
            fid = _initials(canon, taken_fac)
            display = fac_names[canon].most_common(1)[0][0] if fac_names[canon] else canon
            faculty[canon] = Faculty(id=fid, name=re.sub(r"\s+", " ", display).strip(), department="General")
        return faculty[canon].id

    def course_code(subject: str) -> str:
        if subject not in courses:
            code = _code_for(subject, taken_course)
            courses[subject] = Course(code=code, name=subject[:80], department="General", credits=3)
        return courses[subject].code

    for bi, b in enumerate(blocks):
        name = b.title or f"Class {bi + 1} (sheet {b.sheet})"
        sem = re.search(r"\bsem(?:ester)?\.?\s*[-:]?\s*([ivx\d]+)\b", name, re.I)
        roman = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8}
        semester = 1
        if sem:
            tok = sem.group(1).lower()
            semester = int(tok) if tok.isdigit() else roman.get(tok, 1)
        bid = _slug(name, taken_batch)
        batches.append(Batch(id=bid, program=name, level=_level(name), semester=semester, section="A",
                             department="General", strength=opt.default_students))
        if b.room:
            rid = "R-" + re.sub(r"[^A-Za-z0-9]+", "", b.room)
            rooms.setdefault(rid, Room(id=rid, name=f"Room {b.room}", capacity=opt.seats))
            class_room[bid] = rid
        groups_made: dict[int, str] = {}
        where: dict[tuple[str, str, str | None], list[tuple[int, int]]] = defaultdict(list)  # (batch, subject, teacher) -> slots
        for (d, s), opts in sorted(parsed[bi].items(), key=lambda kv: (kv[0][0], kv[0][1])):
            if len(opts) > 1:
                spare_needed = max(spare_needed, len(opts) - 1)
            for oi, (subj, teach) in enumerate(opts):
                target = bid
                if len(opts) > 1:
                    if oi not in groups_made:
                        gid = _slug(f"{bid}-G{oi + 1}", taken_batch, 30)
                        groups_made[oi] = gid
                        batches.append(Batch(id=gid, program=name, level=_level(name), semester=semester, section=f"G{oi + 1}",
                                             department="General", strength=max(1, opt.default_students // 3), group_of=bid))
                    target = groups_made[oi]
                subject = subject_canon.get(subj, subj)
                person = teacher_canon.get(teach, teach) if teach else None
                if person is None:
                    cands = by_subject.get(_subject_key(subject), set())
                    if len(cands) == 1:
                        person = next(iter(cands))
                    else:
                        person = f"(teacher not given: {subject})"
                        unassigned.add(subject)
                where[(target, subject, person)].append((d, s))
        for oi, gid in groups_made.items():
            report.parallel.append(f"{name}: option {oi + 1} of a shared slot is its own group ({gid})")
        for (target, subject, person), slots in where.items():
            slots.sort()
            length_blocks: list[list[tuple[int, int]]] = []
            for d, s in slots:
                if (opt.merge_consecutive and length_blocks and length_blocks[-1][-1][0] == d
                        and length_blocks[-1][-1][1] == s - 1 and (s - 1) not in breaks):
                    length_blocks[-1].append((d, s))
                else:
                    length_blocks.append([(d, s)])
            code = course_code(subject)
            fid = fac_id(person)
            oid = f"O{len(offerings) + 1}"
            offerings.append(Offering(id=oid, course_code=code, faculty_id=fid, batch_ids=[target],
                                      sessions=[len(g) for g in length_blocks]))
            for k, g in enumerate(length_blocks):
                placements.append(Placement(offering_id=oid, session_index=k, day=day_pos[g[0][0]], start=g[0][1],
                                            length=len(g), room_id="?"))

    # 5. rooms: each class's own, spares for parallel electives, or one per class when the grid names none
    spare = opt.spare_rooms if opt.spare_rooms is not None else spare_needed
    if not rooms:
        for i, b in enumerate(x for x in batches if not x.group_of):
            rid = f"R{i + 1}"
            rooms[rid] = Room(id=rid, name=f"Room {i + 1}", capacity=opt.seats)
            class_room[b.id] = rid
        report.assumptions.append("The grids name no rooms, so each class is given a room of its own (Room 1, Room 2, ...).")
    else:
        report.assumptions.append(f"{len(rooms)} room(s) were read from the class headings, each assumed to seat {opt.seats}.")
    spares = []
    for i in range(spare):
        rid = f"R-spare{i + 1}"
        rooms[rid] = Room(id=rid, name=f"Spare room {i + 1} (assumed)", capacity=opt.seats)
        spares.append(rid)
    if spares:
        report.assumptions.append(f"{len(spares)} spare room(s) were added so parallel electives have somewhere to meet.")
    first_room = next(iter(rooms))
    group_room: dict[str, str] = {}
    parent = {b.id: b.group_of for b in batches}
    n_groups: dict[str, int] = defaultdict(int)
    by_off = {o.id: o for o in offerings}
    for p in placements:
        target = by_off[p.offering_id].batch_ids[0]
        if parent.get(target):
            if target not in group_room:
                idx = n_groups[parent[target]]
                n_groups[parent[target]] += 1
                # the class's own room is free while its groups meet apart; the other groups need a spare each
                home = class_room.get(parent[target], first_room)
                group_room[target] = home if idx == 0 else (spares[(idx - 1) % len(spares)] if spares else home)
            p.room_id = group_room[target]
        else:
            p.room_id = class_room.get(target, first_room)

    report.assumptions.append(f"Every class is assumed to have {opt.default_students} students (the grids don't say).")
    if unassigned:
        report.problems.append(f"{len(unassigned)} subject(s) have no teacher named on the grid or in a faculty table "
                               f"({', '.join(sorted(unassigned)[:5])}{'...' if len(unassigned) > 5 else ''}); "
                               "each was given a placeholder teacher so it clashes with nobody. Set the teachers in the Offerings tab.")
    lab_like = sorted(c.name for c in courses.values() if re.search(r"\b(lab|practical|workshop)\b", c.name, re.I))
    if lab_like:
        report.problems.append(f"{len(lab_like)} subject(s) look like labs ({', '.join(lab_like[:4])}...). They are set as ordinary classroom "
                               "subjects because the grids name no lab rooms; change their room kind in the Courses tab if they need one.")

    inst = Institution(name="Imported timetable", calendar=cal, rooms=list(rooms.values()), faculty=list(faculty.values()),
                       batches=batches, courses=list(courses.values()), offerings=offerings)

    # 6. the existing timetable, scored
    tt = Timetable(placements=placements, status="IMPORTED")
    conflicts = find_conflicts(inst, tt)
    quality = measure(inst, tt)
    sheet_of = {}
    for b_, blk in zip([x for x in batches if not x.group_of], blocks):
        sheet_of[b_.id] = blk.sheet
    for g_ in batches:
        if g_.group_of:
            sheet_of[g_.id] = sheet_of.get(g_.group_of, "")
    busy: dict[tuple[str, int, int], set[str]] = defaultdict(set)
    for pl_ in placements:
        o_ = next(x for x in offerings if x.id == pl_.offering_id)
        for t_ in range(pl_.start, pl_.start + pl_.length):
            busy[(o_.faculty_id, pl_.day, t_)].add(sheet_of.get(o_.batch_ids[0], ""))
    cross = sum(1 for sheets_ in busy.values() if len(sheets_) > 1)
    if cross:
        report.problems.append(
            f"{cross} teacher double-booking(s) are between classes from different sheets. If those sheets are "
            "different weeks or versions of the timetable, read only one of them (choose the sheets) and these go away.")
    report.existing = {"sessions": len(placements), "clashes": len(conflicts), "clash_examples": conflicts[:6], "quality": quality}
    for b in batches:
        if b.group_of:
            continue
        own = [o for o in offerings if inst.occupied_batches([b.id]) & set(o.batch_ids)]
        report.classes.append({
            "name": b.program, "id": b.id, "room": rooms[class_room[b.id]].name if b.id in class_room else None,
            "sessions": sum(sum(o.sessions) for o in own), "slots": cal.days * cal.lectures_per_day,
        })
    return inst, report, placements
