# Changelog

## Unreleased (in-app data entry)

You can now enter an institution's data in the browser instead of preparing an Excel workbook.

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
