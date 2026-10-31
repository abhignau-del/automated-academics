# Changelog

## Unreleased

- **Fewer repeated notes.** When several joint lectures bring together classes that live in different rooms, the checks now say so once
  (naming the first three) instead of once per lecture.

## v0.16.0 (2026-11-01)

Housekeeping: activity-log clean-up, backup and restore of all the data, and when each published link was last opened. Existing data, workbooks and databases keep working.

- **Housekeeping.** (1) *Activity log clean-up:* an administrator can **Delete older entries** (30 days or more are kept) or set
  `AA_AUDIT_KEEP_DAYS`; the newest entry always stays and the log still verifies, noting that older entries were removed on purpose.
  (2) *Backup and restore:* **Back up…** (single-user) or **Back up and restore…** (administrator, shared mode) downloads one database
  file without sessions or reset links; restoring checks the file first, refuses while a timetable is being generated, keeps a copy of
  the current data in a `backups` folder (the last five), upgrades older backups and signs everyone out. (3) *Last opened:* each
  published link shows when it was last opened. Databases gain `audit_anchor` and `publications.last_opened_at` in place.

## v0.15.0 (2026-10-31)

A goal for spreading a course's sessions over the week. Existing data, workbooks and databases keep working; the goal is off until weighted.

- **Course spacing.** A new goal, *Course spacing* (`day_spacing`, off until weighted; `weight_day_spacing` in the workbook), penalises each
  pair of consecutive days on which the same course meets, so a course's sessions are spread over the week (Mon + Thu rather than Mon + Tue).
  It is measured independently and shown in the quality panel as *Back-to-back days*. The checks say when a course has more sessions than
  the week has days that can be kept apart, with the number of sessions affected. Existing data gives the same timetables as before.

## v0.14.0 (2026-10-27)

Named home rooms for classes. Existing data, workbooks and databases keep working; a class has no home room until you choose one.

- **Named home rooms.** A class can be given a *Home room* (Classes table, or `home_room` in the workbook, by id or room name). Its
  classroom lectures then go in that room only. Labs, halls and a class's practical sub-groups are unaffected, and a joint lecture of
  classes with different home rooms may use any suitable classroom (the checks note it). The checks report a home room that is too small
  or overbooked and a pin outside the home room; moving a lecture out of it by hand is flagged as a conflict. Renaming a room keeps its
  classes there; deleting it sends them back to "any room". Existing data is unchanged.

## v0.13.0 (2026-10-26)

Holidays and exam weeks in calendar files, and a clearer early/late-lectures check. Existing data, workbooks and databases keep working; databases gain `no_class` on `publications` in place.

- **Holidays and exam weeks in calendar files.** Under a link with term dates, *Add break* lists days without lectures (a first and last
  day, optionally a name). Calendar files leave those days out of every event's weekly repeat, and the published page lists them. Breaks
  outside the term change nothing; a break covering the whole term gives an empty calendar. Databases gain `no_class` on `publications`
  in place.
- **Checks explain the early/late-lectures goal.** When the goal is on but the classes are loaded so fully that some lectures must fall in
  an early or late slot anyway, the checks now say so, with the least number that cannot be avoided. (Found running a real management
  institute's data: every class had 21 to 24 of its 24 weekly lectures, so at least 97 of 124 early/late lectures were unavoidable.)

## v0.12.0 (2026-10-21)

Password reset by e-mail, room and time-of-day goals, and calendar files for published links. Existing data, workbooks and databases keep working; databases gain `users.email`, a `password_resets` table, new `publications` columns and the goals' settings in place. The new goals are off until weighted, and password reset is off until a mail server is configured.

- **Calendar files and "what changed" on published links.** A link can carry the term's first and last day; with lecture times set, the
  page then offers *Add to my calendar* (subscribe), a downloadable `.ics` file and a copyable address for the class, teacher or
  room on screen: weekly repeating events at the class's own clock times, with stable event ids so a subscribed calendar updates
  instead of duplicating. Pointing a link at a newer timetable (with an optional note) now shows students, for two weeks, that it
  was updated, marks the new or moved lectures of the class they are viewing, lists any that are gone and tells an unchanged class
  so. Databases gain `term_start`, `term_end`, `prev_job_id`, `updated_at` and `note` on `publications` in place.
- **Two new goals: stay in one room, avoid early and late lectures.** `room_changes` penalises each classroom a class uses for its theory
  lectures beyond the first (labs and halls don't count); `odd_hours` penalises class lectures in the first or last slots of the day
  (the calendar says how many of each, one by default). Both are off until weighted (Data → Settings), so existing institutions
  give the same timetables; both appear in the quality panel, the workbook (`weight_room_changes`, `weight_odd_hours`,
  `early_lectures`, `late_lectures`) and are measured independently of the solver. On the sample, weights of 10 took extra
  classrooms from 24 to 0 and early/late lectures from 8 to 0.
- **Forgot your password? (optional, shared mode).** With a mail server configured (`AA_SMTP_HOST`, `AA_SMTP_FROM`, `AA_PUBLIC_URL`, see the README),
  the sign-in screen offers a reset by e-mail: a one-use link that lasts an hour, replaced by any newer request, ending every sign-in of
  that person. The answer never reveals whether an account exists, requests and wrong links are throttled, only a hash of the link is
  stored, and the activity log records the request and the reset without any secret. Accounts get an **E-mail** column in People…
  (unique, lower-cased). Databases gain `users.email` and a `password_resets` table in place. Without the settings nothing changes.

## v0.11.0 (2026-10-21)

An activity log. Existing data, workbooks and databases keep working; databases gain an `audit` table in place, and the log needs shared mode (sign-in).

- **Activity log (shared mode).** Records who did what: sign-ins and failed attempts (name and address, never the password), sign-out, password
  changes, account creation/changes/deletion, institution created/changed/deleted (with a plain sentence such as "changed 2 teachers
  (CSF1, CSF2) and 1 class"), timetables asked for, finished or failed (by "The solver"), hand edits and kept imports, links
  published/changed/withdrawn (the name, never the address), sharing and removals, and refused attempts by department-limited editors.
  An institution's owner opens **History…**; an administrator opens **Activity log…** from the account menu. Filters (kind, person, since,
  a word), paging, and a CSV download that neutralises formula-looking cells. Entries are append-only and hash-chained; **Verify
  integrity** (administrators) reports the first altered or missing entry. Passwords, session tokens and link addresses are never
  stored; people opening a published link are not logged; a failing log write never blocks the action. Nothing is recorded with sign-in
  off. New `audit` table, added in place. API: `GET /audit`, `/audit.csv`, `/audit/verify`, `/institutions/{id}/audit(.csv)`.

## v0.10.0 (2026-10-20)

Read-only publish links. Existing data, workbooks and databases keep working; databases gain a `publications` table in place, and publishing needs shared mode (sign-in).

- **Read-only publish links (shared mode).** An editor of the whole institution can **Publish…** a finished timetable as a link anyone
  can open with no account. Choose the views to offer (classes, teachers, rooms), an optional expiry and a name; copy the link,
  point it at a newer timetable, or withdraw it at any time. The public page is read-only, shows the class's own clock, works on
  a phone (week grid or day list), downloads PDFs (and an Excel workbook when every view is offered) and opens straight to a
  bookmarked class. Links are 256-bit random, never listed, not cached or indexed, send no referrer, and guessing addresses is
  slowed down; the rest of the app still needs a sign-in. A new `publications` table is added to databases in place.

## v0.9.0 (2026-10-20)

Clearer clash messages, sideways grids in the timetable import, and browser tests in CI. Existing data, workbooks and databases keep working.

- **Browser tests in CI.** `e2e/` now holds eight journeys that drive the real app in a real browser (generate and export, locking
  and failure reasons, joint classes, lecture times, class clocks, grid import ordinary and sideways, shared mode, department
  scoping; about 120 checks), each on its own copy of the app. A new `e2e` CI job runs them on every push and uploads a screenshot
  when one fails. Locally: `node run.mjs` in `e2e/`.
- **Sideways grids in the timetable import.** Grids drawn with the days down the first column and the lectures (times or period
  numbers, with a LUNCH/BREAK column if there is one) across the top are read the same way as ordinary ones: same classes, times,
  breaks and sessions. The heading may sit in the top-left corner or above the grid, and days may be spelled out ("Monday").
- **Clearer clash messages.** Clashes and other timetable problems now read in plain words: "Prof. Rao is teaching two things at once
  on Mon, lecture 1 (9:00–9:50): Calculus for CS-UG1 and Algebra for MG-UG1", "Room One is booked twice…", "Dr. Iyer is
  unavailable on Tue, lecture 2…", "Room Two seats 20 but 30 students attend". Names instead of ids, lectures counted from 1, the
  class's own times when the institution has them. A section and its lab groups hit by one mistake are still one message. Two
  solver errors that said "session 0" now name the course and class.

## v0.8.0 (2026-10-14)

Per-class lecture times and department-limited editors. Existing data, workbooks and databases keep working; databases gain a
`departments` column in place, and data without clocks or limits behaves exactly as before.

- **Editors limited to some departments.** In shared mode an editor can be limited to chosen departments (tick them when sharing).
  They maintain those departments' teachers, classes, courses, offerings and locks and move their own offerings' sessions; other
  departments' rows are shown locked. Rooms, settings, generating a timetable and keeping an imported one stay with editors of the
  whole institution, and a joint class between departments needs all of its departments. Enforced on the server by comparing the
  saved data with the data being saved (the refusal lists what is not allowed); databases gain a `departments` column in place.
- **Different lecture times per class.** Named **clocks** (Settings → *Classes that keep other hours*) for classes that run to
  other hours, chosen per class in the Classes tab's new Clock column. Grids, PDFs and Excel sheets are labelled with the class's
  own clock; sessions from another clock show their own time on teachers' and rooms' grids; a special day still wins for
  everyone. The timetable-grid import turns each distinct set of written times into a named clock instead of forcing the most
  common one on everybody. Excel gains `clock_<name>` settings and a `clock` column on Batches. A warning appears when classes on
  different clocks share a teacher and their lectures would overlap in real time. Fixed: the PDF's time column was too narrow
  and wrapped times such as "10:00–10:40".
## v0.7.0 (2026-10-10)

Import an existing timetable. Existing data, workbooks and databases keep working.

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
