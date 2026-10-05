"""Check a built Windows app the way a person would use it: start it, open the screen, make and solve a timetable, export.

    python scripts/smoke_app.py dist-app/Automated-Academics-0.3.0-windows.zip

Unzips into a temporary folder, runs the program with its data in another temporary folder (never your real data),
and stops it afterwards. Exits 0 if everything worked.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

PORT = 8801
BASE = f"http://127.0.0.1:{PORT}"


def call(path: str, data=None, raw: bool = False):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json"}, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
    return body if raw else json.loads(body)


def main(zip_path: str) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp_dir / "app")
        exe = next((tmp_dir / "app").rglob("Automated Academics.exe"))
        home = tmp_dir / "home"
        env = {**os.environ, "AA_HOME": str(home), "AA_DB": ""}
        proc = subprocess.Popen([str(exe), "--no-browser", "--port", str(PORT)], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        problems: list[str] = []
        try:
            for _ in range(90):
                try:
                    call("/health")
                    break
                except OSError:
                    time.sleep(1)
            else:
                print("FAIL: the app did not start")
                return 1
            page = call("/", raw=True).decode()
            if 'id="root"' not in page:
                problems.append("the screen was not served")
            else:
                asset = page.split("assets/")[1].split('"')[0]
                if len(call("/assets/" + asset, raw=True)) < 10_000:
                    problems.append("the screen's script is missing")
            iid = call("/institutions", call("/institutions/starter?kind=sample"))["id"]
            jid = call(f"/institutions/{iid}/solve", {"time_limit_s": 10})["job_id"]
            while (job := call(f"/jobs/{jid}"))["status"] in ("queued", "running"):
                time.sleep(1)
            if job["status"] != "done":
                problems.append(f"generating failed: {job['error']}")
            else:
                if call(f"/jobs/{jid}/export.pdf", raw=True)[:4] != b"%PDF":
                    problems.append("PDF export is not a PDF")
                if call(f"/jobs/{jid}/export.xlsx", raw=True)[:2] != b"PK":
                    problems.append("Excel export is not a workbook")
            if not (home / "automated_academics.db").is_file():
                problems.append("data was not saved in the data folder")
            again = subprocess.run([str(exe), "--no-browser", "--port", str(PORT)], env=env,
                                   capture_output=True, text=True, timeout=60).stdout
            if "already running" not in again:
                problems.append("a second launch did not find the first")
        finally:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
            time.sleep(1)  # let Windows release the files before the temporary folder is removed
        for p in problems:
            print("FAIL:", p)
        print("PASS" if not problems else "FAILED")
        return 1 if problems else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
