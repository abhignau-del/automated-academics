"""Measure how the solver scales on a fictional, department-coupled university.

    python benchmarks/scale.py 2 5 10 25 --limit 600          # departments per run, 8 classes each
    python benchmarks/scale.py 10 --goals --limit 300         # also improve against the soft goals
    python benchmarks/scale.py 5 --whole                      # the all-at-once solver, for comparison

Each size runs in its own process so its peak memory is its own. Without --goals only the hard rules are
solved (the first valid timetable), which is the part that decides whether a size is feasible at all.
Prints one line per size; nothing is saved.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time


def peak_memory_mb() -> float:
    try:
        import resource  # Linux / macOS
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak / (1024 if sys.platform.startswith("linux") else 1024 * 1024)
    except ImportError:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        c = Counters()
        c.cb = ctypes.sizeof(c)
        k = ctypes.windll.kernel32
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        k.K32GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(c), c.cb)
        return c.PeakWorkingSetSize / 1024 / 1024


def run_one(departments: int, classes: int, limit: float, goals: bool, whole: bool) -> dict:
    from automated_academics.decompose import solve_decomposed
    from automated_academics.models import Weights
    from automated_academics.solver import InfeasibleError, solve
    from automated_academics.synthetic import university_institution
    from automated_academics.validate import find_conflicts

    inst = university_institution(departments, classes)
    if not goals:
        inst = inst.model_copy(update={"weights": Weights(repeat_course_day=0, batch_gaps=0, faculty_gaps=0,
                                                          peak_day_load=0, avoid_slot=0)})
    out = {"departments": departments, "sessions": sum(len(o.sessions) for o in inst.offerings),
           "teachers": len(inst.faculty), "rooms": len(inst.rooms)}
    t0 = time.monotonic()
    try:
        tt = (solve(inst, time_limit_s=limit, min_first_solution_s=limit) if whole
              else solve_decomposed(inst, time_limit_s=limit))
        out.update(status=tt.status, seconds=round(time.monotonic() - t0, 1), clashes=len(find_conflicts(inst, tt)),
                   penalty=tt.penalty if goals else None)
    except InfeasibleError as e:
        out.update(status=type(e).__name__, seconds=round(time.monotonic() - t0, 1), clashes=None, penalty=None)
    out["peak_mb"] = round(peak_memory_mb())
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("departments", nargs="*", type=int, default=[2, 5, 10])
    ap.add_argument("--classes", type=int, default=8, help="classes per department")
    ap.add_argument("--limit", type=float, default=300, help="seconds allowed per size")
    ap.add_argument("--goals", action="store_true", help="also optimise the soft goals (default: first valid timetable only)")
    ap.add_argument("--whole", action="store_true", help="solve everything at once instead of department by department")
    ap.add_argument("--one", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.one:
        print(json.dumps(run_one(a.departments[0], a.classes, a.limit, a.goals, a.whole)))
        return
    print(f"{'depts':>5} {'sessions':>8} {'teachers':>8} {'rooms':>5}  {'result':<10} {'seconds':>7} {'clashes':>7} {'peak MB':>8}")
    for d in a.departments:
        cmd = [sys.executable, __file__, str(d), "--one", "--classes", str(a.classes), "--limit", str(a.limit)] + (["--goals"] if a.goals else []) + (["--whole"] if a.whole else [])
        p = subprocess.run(cmd, capture_output=True, text=True)
        try:
            r = json.loads(p.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            print(f"{d:>5}  failed: {p.stderr.strip().splitlines()[-1] if p.stderr.strip() else 'no output'}")
            continue
        print(f"{r['departments']:>5} {r['sessions']:>8} {r['teachers']:>8} {r['rooms']:>5}  {r['status']:<10} {r['seconds']:>7} "
              f"{r['clashes'] if r['clashes'] is not None else '-':>7} {r['peak_mb']:>8}" + (f"  penalty {r['penalty']}" if r.get("penalty") is not None else ""))


if __name__ == "__main__":
    main()
