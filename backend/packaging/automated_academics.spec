# PyInstaller recipe for the Windows app. Run through scripts/build-app.ps1, which builds the screen first.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

root = Path(SPECPATH).resolve().parents[1]
screen = root / "frontend" / "dist-app"
assert (screen / "index.html").is_file(), "build the screen first (npm run build:app in frontend/)"

datas = [(str(screen), "screen")] + collect_data_files("reportlab")
binaries, hiddenimports = [], [
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.loops.asyncio", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl", "uvicorn.lifespan.on", "python_multipart",
]
for pkg in ("ortools",):  # the solver ships native libraries that must travel with it
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h

a = Analysis(
    [str(Path(SPECPATH) / "app_entry.py")],
    pathex=[str(root / "backend" / "src")],
    datas=datas, binaries=binaries, hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "matplotlib", "IPython"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Automated Academics", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="Automated Academics")
