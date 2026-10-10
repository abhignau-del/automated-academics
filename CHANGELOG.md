# Changelog

## Unreleased

- **Import an existing timetable.** + New → *Import an existing timetable…* reads timetable grids (a block per class, days
  across, lectures down, "Subject (Teacher)" in the cells) into a new institution: classes and their rooms from the headings,
  lecture times and breaks from the side column, subjects and teachers from the cells (several layouts, plus a faculty table
  beside the grid), parallel electives as class groups, double lectures kept, spellings of one teacher merged (and listed),
  repeated weeks and per-teacher views skipped. It scores the current timetable and keeps it as the first timetable.
  Preview first, with every assumption listed; the sheets to read can be unticked.
## v0.6.0 (2026-10-10)

Lecture times. Existing data, workbooks and databases keep working: data without times looks exactly as before.

- **Lecture times.** Optional clock times for the lectures, set in Settings by whoever runs the timetable: fill them in from
  a start time, a length, the gaps and a longer break, then adjust any single one; a day can run to its own clock (a shorter
  Saturday). They appear on the timetable grid, in a session's details, on teachers' availability grids, in PDFs and in
  Excel, which also reads and writes them (`lecture_times`, `lecture_times_<Day>`; "8 to 8.50", "9:30 am" and "8.55" all
  work). Changing the number of lectures keeps the times in step. Nothing changes for data without times.
## v0.5.0 (2026-10-07)

Joint classes, university-scale solving and a shared (server) mode. Existing data, workbooks and databases keep
working; sign-in is off unless you switch it on, and databases gain their new tables in place.

- **Shared mode (first slice).** Run it as a server with `AA_AUTH=1` (or `docker compose up -d`): first-run administrator,
  sign-in with salted-scrypt passwords and HttpOnly cookie sessions, login throttling, an administrator's **People**
  list, and per-institution access as owner / editor / viewer with a **Share** dialog. Viewers get a view-only screen.
  Saves carry the version that was read (`ETag` / `If-Match`) and a stale save is refused with 409 and an offer to
  load the other person's version. Databases gain `users`, `sessions`, `members` and a `version` column in place, and
  switch to WAL mode. With sign-in off, nothing changes. Adds a `Dockerfile`, `docker-compose.yml` and a CI job that
  builds and smoke-tests the image (not run locally: Docker was unavailable).
- **University scale: department-by-department solving.** Institutions above 400 sessions are now solved one
  department at a time (joint classes first), each around what is already placed, with a small pool of rooms
  per department, then improved one department at a time. A fictional 10-department university (1,615
  sessions) that found no timetable in 10 minutes now takes about 90 s, 25 departments (4,036 sessions)
  about 3 minutes and 60 departments (9,690 sessions) about 15 minutes, all with no clashes. Smaller institutions still solve all at once. The solver can now schedule
  part of an institution around fixed placements, and `backend/benchmarks/scale.py` measures it.
- **Joint classes as a feature.** The Offerings tab suggests offerings of the same course and teacher for different
  classes that may be taught together, and combines or splits them in one click (also for ticked rows). Combining
  checks the course, teacher and classes match, keeps the busiest weekly hours, and warns when no room is big enough.

## v0.4.0 (2026-10-06)

Spreadsheet-style editing, importing a workload list, locking sessions, plain-language failure reasons and a
Windows app that needs no setup. Existing data, workbooks and databases keep working (a workbook without a
Pins sheet imports with no pins).

- **Import a workload list** (New ▾ → Import a workload list…): the flat "subject, teacher, programme, hours
  per week" sheet many colleges already keep becomes a full institution. Headings are matched by name, classes
  are built from programme + semester, and classes taught together (same subject and teacher across
  programmes) can be combined up to a size you choose. Everything the importer had to assume (rooms, seats,
  student counts, lab or not) is listed in a report before anything is saved.
- **Lock sessions in place.** Select a session in the timetable and choose Lock in place (or Lock all shown);
  it keeps its day, lecture and room when you regenerate. Locked sessions are listed in the Data tab's
  **Locked** tab and are stored in the workbook (an optional Pins sheet; older workbooks still import).
  Locking does not make the existing timetable out of date, since it only steers the next Generate.
- **Why it failed.** When no timetable exists, the message now names the smallest set of your own rules that
  cannot hold together (for example "Teacher A and Teacher B are both free only at lecture 2, for the same
  class"), or says the data itself is impossible and lists why. New checks catch pins that cannot fit, and
  rooms whose total demand exceeds their slots.
- **Windows app that needs no setup.** `scripts\build-app.ps1` builds a folder (and zip) that runs by double-click:
  the engine and the screen come from one address, data lives in `%APPDATA%\Automated Academics`, and a second
  launch just reopens the running one. A release workflow builds and attaches it when a version tag is pushed
  (`scripts/smoke_app.py` checks the built app: start, screen, generate, export, data folder).
- Tests: 21 workload-import tests, 14 pin and explanation tests, an API test, and a browser journey covering
  lock, regenerate, unlock and the failure message.

### Spreadsheet-style editing

- **Paste from Excel / Google Sheets**, into the cells (fills down and across, adds rows as needed) or via
  **Paste from spreadsheet…**, which recognises a heading row (columns matched by name in any order, existing
  ids updated, new ones added) and previews how many rows are new and updated. Choices match by id or name;
  anything unreadable is reported and left alone.
- **Bulk edit:** tick rows (or all rows shown), then set a column on all of them, duplicate, or delete them
  (with a summary of what else the delete takes with it). New **filter** box on every table.
- **Undo / redo** for all data edits, including pastes and bulk edits; typing in a cell is one step.
- **Keyboard:** Enter and the up/down arrows move between rows; Escape cancels typing.
- **Scale:** a measured fix. With about 1,000 offerings, typing took 1.6 s per keystroke and the Offerings tab
  hung the browser. Rows are now memoised, each table gets only the choices it needs, long choice lists use one
  shared type-ahead list instead of a dropdown per row, and 200 rows are drawn at a time. Typing is now 25-40 ms.
- **The launcher serves the built app** (building it on first start, or when the source changed) instead of the
  development server, which is about twice as fast to type into.
- **Fixed:** pressing Escape in an id cell (and in the new type-ahead fields) committed the half-typed text
  instead of cancelling it. An id cell would have renamed the row.
- Tests: 59 new unit tests (paste parsing and column mapping, bulk operations, undo history) and, run by hand,
  three browser journeys covering the whole editor (36 + 33 + 20 checks) against the production build.

## v0.3.0 (2026-10-05)

In-app data entry, and a double-click launcher for Windows. Existing data, workbooks and databases keep
working (databases are migrated automatically when opened).

**Double-click launcher (Windows).** `Start Automated Academics.bat` starts the engine and the screen,
waits until both are ready and opens the browser; `Stop Automated Academics.bat` stops them. No terminal
needed after the one-time setup. Stop only ends this app's own processes and leaves anything else using
the same ports alone. See *Starting the app with a double-click* in the README.

**In-app data entry.** You can now enter an institution's data in the browser instead of preparing an
Excel workbook.

- **Data tab** with a table per section (settings and quality weights, rooms, faculty, classes, courses,
  offerings), start-from-blank or copy-of-the-sample, and a clickable weekly availability grid per
  teacher (free, would rather avoid, unavailable).
- **Live validation** (`POST /institutions/check`): problems are located by section, row and field and
  highlighted on the exact cell. Structural errors block saving; **impossible-to-schedule data** (a session
  that no room can hold, a teacher with more lectures than free slots, a block that fits nowhere, a class
  with more lectures than the week) is explained immediately and blocks Generate, not Save.
- Renaming a teacher, course or class updates everything that refers to it; deleting a row says what
  else it will remove; duplicate or blank ids are refused.
- `PUT /institutions/{id}`, `DELETE /institutions/{id}`, `GET /institutions/starter`,
  `GET /institutions/{id}/workbook.xlsx` (download the data as an Excel workbook that uploads again).
  `POST /institutions` and uploads now also return diagnostics, and creation errors are located.
- **Out-of-date timetables:** editing data marks an existing timetable `stale` (only when the data really
  changed); the UI says so and holds back exports until it is regenerated.
- Existing SQLite databases are migrated in place (new `updated_at` column); no action needed.
- Tests: 25 new backend tests (checks, diagnostics, endpoints, migration) and 21 new frontend tests for the
  editing logic. A scripted real-browser run of the whole journey (blank institution to generated timetable,
  36 checks) passed; it is not part of CI.

## v0.2.0 (2026-10-05)

Solver quality and scale. Existing v0.1.0 workbooks and data keep working: the new `avoid` column
and weights are optional and default sensibly.

**Timetable quality.** The solver now minimises five weighted soft goals instead of one: idle gaps
in each class's day, idle gaps in each teacher's day, the busiest-day load per class (an even week),
repeated courses on one day, and lectures in slots a teacher asked to avoid. On the sample
institution, class idle gaps went 67 to 0, teacher idle gaps 39 to 0, busiest-day load 29 to 20,
and the weighted penalty 903 to 80.

- New optional `Faculty.avoid` slots (soft; `unavailable` stays hard) and `Institution.weights`.
  Excel: optional `avoid` column on `Faculty` and `weight_*` rows on `Institution`. Workbooks without
  them still import.
- `quality.measure`: an independent measurement of these goals (written separately from the
  solver, like the clash validator). A test checks the solver's own objective equals it.
- The Quality panel in the UI and the `quality` / `penalty` fields of `POST .../validate` score a
  timetable live while you edit, with the change against the saved version.
- One double-booking is now reported once, even when it hits a section and its lab groups.

**Solver rewrite for scale.** The v0.1.0 formulation created a variable per (session, slot, room)
and did not scale: a 272-session instance found no timetable in a minute. It now uses a start slot
per session with separate room choice and no-overlap constraints, finds a valid timetable first and
improves it, and turns off two CP-SAT preprocessing steps that took 55 of 61 seconds. Result: a
valid timetable in ~5 s at 272 sessions and ~90 s at 1,088 (see the README for measured quality
against time). Any time limit now returns a valid timetable; before, short limits could return an error.

- `time_limit_s` may now be up to 900 s (was 300), and the UI offers 5, 10 and 15 minute options.
- `scaled_institution(n)` builds n copies of the sample institution for scale tests.
- Test suite: 67 backend tests in about 90 s (the first version of these changes took 5 minutes).

## v0.1.0 (2026-10-04)

First release.

- **Solver:** OR-Tools CP-SAT. Hard constraints: no faculty, room or batch clashes; room kind and
  capacity; lab blocks that never span a break; faculty unavailability and per-day load. Soft goal:
  avoid repeating a course twice on one day.
- **CBCS / NEP model:** shared electives across programs, practical sub-groups (parallel lab
  batches), multi-lecture lab blocks, UG and PG batches.
- **Excel import** with one sheet per entity, row-level error reporting, and a template workbook.
- **FastAPI service** with SQLite persistence and background solving.
- **React web UI:** upload, generate, class / faculty / room weekly views, drag-and-drop editing with
  live clash checking, undo, revert and save.
- **Export:** PDF (landscape A4, one page per class, faculty member and room) and Excel.
  `AA_PDF_FONT` selects a font for Hindi and other Indian scripts.
- Terminology: "lecture" (not "period") for a teaching slot.

See the README for known limitations.
