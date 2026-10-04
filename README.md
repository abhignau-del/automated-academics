# Automated Academics

Open-source timetable generator for colleges and universities running UG and PG programs.
It models the realities of Indian higher education (CBCS / NEP 2020) and uses a
constraint solver (Google OR-Tools CP-SAT) to produce clash-free timetables.

> **Status: early development (v0.1).** The solver core and data model work and are tested.
> The API, Excel import, web UI and exports are on the roadmap below.

## What it handles today

- Weekly grid with configurable days, periods and breaks (blocks never span lunch)
- Faculty, rooms (classroom / lab / hall), batches and courses across departments
- **Shared electives**: one offering attended by batches from several programs (MDC, VAC, AEC, open electives)
- **Practical sub-groups**: a lab batch blocks its parent section, and two sub-groups can run labs in parallel
- Multi-period lab blocks, room kind and capacity matching
- Faculty unavailability and per-day load limits
- Soft goal: avoid repeating the same course twice on one day
- An independent clash validator (the basis for live checking of manual edits)

## Quick start

Requires Python 3.11+.

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

```python
from automated_academics.synthetic import sample_institution
from automated_academics.solver import solve
from automated_academics.validate import find_conflicts

inst = sample_institution()          # fictional institution
tt = solve(inst, time_limit_s=30)
print(tt.status, len(tt.placements), find_conflicts(inst, tt))
```

## Loading your own data from Excel

Start from [docs/institution-template.xlsx](docs/institution-template.xlsx) (fictional sample data)
and replace the rows with your own. One sheet per entity: `Institution`, `Rooms`, `Faculty`,
`Batches`, `Courses`, `Offerings`. Periods are numbered from 1 and days are written by name
(e.g. unavailable = `Mon:1, Tue:3`). A shared elective lists several batches in `batch_ids`;
a lab sub-group sets `group_of` to its parent batch.

```bash
python -m automated_academics.cli check my-data.xlsx
```

The checker reports every problem with its sheet and row, so you can fix the file in one pass.

```python
from automated_academics.excel_io import import_workbook
inst = import_workbook("my-data.xlsx")
```

## Running the API

```bash
cd backend
uvicorn automated_academics.api:create_app --factory --reload
```

Interactive docs at <http://127.0.0.1:8000/docs>. Data is kept in a local SQLite file
(`automated_academics.db`, override with the `AA_DB` environment variable).

| Endpoint | Purpose |
|---|---|
| `GET /institutions/template` | Download the Excel template |
| `POST /institutions/upload` | Upload a workbook. Returns `201` with an id, or `422` listing every sheet/row problem |
| `POST /institutions` | Create from JSON; `GET /institutions`, `GET /institutions/{id}` |
| `POST /institutions/{id}/solve` | Start a background solve (`time_limit_s` 1-300); returns a `job_id` |
| `GET /jobs/{id}` | Poll status: `queued`, `running`, `done` or `failed` (with `error`) |
| `GET /jobs/{id}/timetable` | The generated timetable |
| `GET /jobs/{id}/views/{batch\|faculty\|room}/{id}` | One class's, teacher's or room's week with names resolved |
| `POST /institutions/{id}/validate` | Check a hand-edited timetable for clashes |

`PUT /jobs/{id}/timetable` saves a hand-edited timetable (clashes are reported but do not block
saving), and `GET /institutions/{id}/latest-job` finds the newest finished timetable.

Periods in API responses are zero-based. There is **no authentication yet**: run it on localhost
or behind your own access control, not directly on the public internet.

## Running the web UI

Requires Node.js 20+. In two terminals:

```bash
cd backend  && uvicorn automated_academics.api:create_app --factory --reload   # API on :8000
cd frontend && npm install && npm run dev                                      # UI on :5173
```

Open <http://localhost:5173>, upload your workbook (or the template), select the institution and
press **Generate timetable**. Then:

- switch between **Class**, **Faculty** and **Room** weeks
- **drag a session** to another slot; the server re-checks clashes after every change and sessions
  involved turn red, with the reasons listed under *Problems*
- change a session's room from the side panel, **Undo**, **Revert**, and **Save changes**

Set `VITE_API_URL` if the API is not on `http://127.0.0.1:8000`. Frontend tests: `npm test`.

## Roadmap

- [x] Domain model, CP-SAT solver, validator, synthetic data
- [x] Excel import with row-level validation errors, plus a template workbook
- [x] FastAPI service with SQLite persistence and background solving
- [x] React UI: Excel upload, generate, class / faculty / room views
- [x] Drag-and-drop editing with live clash detection, undo and save
- [ ] In-app data entry (today data comes from the Excel workbook)
- [ ] PDF and Excel export
- [ ] More soft constraints: gaps, faculty preferences, balanced days

## Data and privacy

This repository contains only fictional data. Never commit real student or staff data.
`private-data/` is git-ignored for local validation.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Issues and pull requests are welcome.

## Licence

[MIT](LICENSE)
