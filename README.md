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

## Roadmap

- [x] Domain model, CP-SAT solver, validator, synthetic data
- [ ] Excel import templates (programs, courses, faculty, rooms, batches)
- [ ] FastAPI service and persistence
- [ ] React UI: data entry, class / faculty / room views
- [ ] Drag-and-drop editing with live clash detection
- [ ] PDF and Excel export
- [ ] More soft constraints: gaps, faculty preferences, balanced days

## Data and privacy

This repository contains only fictional data. Never commit real student or staff data.
`private-data/` is git-ignored for local validation.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Issues and pull requests are welcome.

## Licence

[MIT](LICENSE)
