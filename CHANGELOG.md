# Changelog

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
