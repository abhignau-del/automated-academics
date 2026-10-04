"""Command line helpers.

  python -m automated_academics.cli template out.xlsx   # fictional sample workbook to edit
  python -m automated_academics.cli check data.xlsx     # validate a workbook
"""

from __future__ import annotations

import argparse
import sys

from .excel_io import ImportErrors, export_workbook, import_workbook
from .synthetic import sample_institution


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="automated-academics")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("template", help="write a sample workbook to use as a template")
    t.add_argument("path")
    c = sub.add_parser("check", help="validate a workbook and summarise it")
    c.add_argument("path")
    args = ap.parse_args(argv)

    if args.cmd == "template":
        export_workbook(sample_institution(), args.path)
        print(f"wrote {args.path}")
        return 0

    try:
        inst = import_workbook(args.path)
    except ImportErrors as e:
        print(f"{len(e.issues)} problem(s) found:")
        for issue in e.issues:
            print(f"  - {issue}")
        return 1
    sessions = sum(len(o.sessions) for o in inst.offerings)
    print(f"OK: {inst.name} - {len(inst.rooms)} rooms, {len(inst.faculty)} faculty, "
          f"{len(inst.batches)} batches, {len(inst.courses)} courses, "
          f"{len(inst.offerings)} offerings ({sessions} weekly sessions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
