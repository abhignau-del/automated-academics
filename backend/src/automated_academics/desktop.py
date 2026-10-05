"""Run Automated Academics as an ordinary program: engine and screen on one local address, opened in the browser.

This is what the packaged Windows app starts. Your data lives in your own user folder
(the "Automated Academics" folder inside %APPDATA%), so it survives upgrades; set AA_HOME to keep it elsewhere.
Nothing leaves the computer: the engine only listens on this machine.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

NAME = "Automated Academics"
PREFERRED_PORT = 8765  # a fixed home port lets a second launch find the copy that is already running


def data_dir() -> Path:
    """Where the database lives; created if missing."""
    home = os.environ.get("AA_HOME")
    if home:
        path = Path(home)
    elif os.environ.get("APPDATA"):
        path = Path(os.environ["APPDATA"]) / NAME
    else:
        path = Path.home() / ".automated-academics"
    path.mkdir(parents=True, exist_ok=True)
    return path


def screen_dir() -> Path | None:
    """The built screen: bundled inside the packaged app, or the local build when run from the source tree."""
    bundled = getattr(sys, "_MEIPASS", None)
    candidates = [Path(bundled) / "screen"] if bundled else []
    candidates.append(Path(__file__).resolve().parents[3] / "frontend" / "dist-app")
    return next((c for c in candidates if (c / "index.html").is_file()), None)


def is_ours(port: int) -> bool:
    """True if this app is already answering on the port."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1.5) as r:
            return json.load(r).get("status") == "ok"
    except (OSError, ValueError):
        return False


def is_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def any_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _open_when_ready(url: str, port: int, show: bool) -> None:
    for _ in range(100):
        if is_ours(port):
            if show:
                webbrowser.open(url)
            return
        time.sleep(0.2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="automated-academics-app", description=__doc__)
    ap.add_argument("--port", type=int, default=None, help=f"port to use (default {PREFERRED_PORT}, or any free one)")
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = ap.parse_args(argv)
    show = not args.no_browser

    port = args.port or PREFERRED_PORT
    url = f"http://127.0.0.1:{port}"
    if is_ours(port):
        print(f"{NAME} is already running at {url}. Opening it.")
        if show:
            webbrowser.open(url)
        return 0
    if not is_free(port):
        if args.port:
            print(f"Port {port} is in use by another program. Try --port with a different number.")
            return 1
        port = any_free_port()
        url = f"http://127.0.0.1:{port}"

    import uvicorn

    from .api import create_app

    screen = screen_dir()
    db = Path(os.environ.get("AA_DB") or data_dir() / "automated_academics.db")
    app = create_app(str(db), static_dir=str(screen) if screen else None)

    print(f"{NAME}\n")
    if screen is None:
        print("(The screen was not found in this copy; only the engine is running.)\n")
    print(f"  Running at {url}")
    print(f"  Your data is kept in {db}")
    print("\n  Leave this window open while you work. Close it to stop.\n")

    threading.Thread(target=_open_when_ready, args=(url, port, show), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


def run() -> None:
    """Entry point for the packaged app: if something goes wrong, keep the window open long enough to read it."""
    try:
        code = main()
    except KeyboardInterrupt:
        code = 0
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        code = 1
    if code and getattr(sys, "frozen", False):
        input("\nPress Enter to close this window.")
    sys.exit(code)


if __name__ == "__main__":
    run()
