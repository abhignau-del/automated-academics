# Starts Automated Academics (the engine and the screen), waits until both are ready, and opens the
# browser. Safe to run again: anything already running is left alone.
#
# Set AA_NO_BROWSER=1 to skip opening the browser and skip the "press Enter" pauses (used by tests).

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root "backend\.venv\Scripts\python.exe"
$logs = Join-Path $root "logs"
$quiet = [bool]$env:AA_NO_BROWSER
$url = "http://localhost:5173"

# a shortcut started from Explorer may carry an older PATH than a fresh terminal would
$env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")

function Fail([string]$message) {
    Write-Host ""
    Write-Host $message -ForegroundColor Red
    Write-Host ""
    if (-not $quiet) { Read-Host "Press Enter to close this window" | Out-Null }
    exit 1
}

function IsUp([string]$address) {
    try { Invoke-WebRequest $address -UseBasicParsing -TimeoutSec 2 | Out-Null; return $true } catch { return $false }
}

function WaitFor([string]$what, [string]$address, [string]$logFile, $process) {
    for ($i = 0; $i -lt 90; $i++) {
        if (IsUp $address) { Write-Host " ready"; return }
        if ($process.HasExited) { break }  # it crashed: no point waiting out the clock
        Write-Host -NoNewline "."
        Start-Sleep 1
    }
    $why = if ($process.HasExited) { "stopped as soon as it started" } else { "did not start within 90 seconds" }
    $hint = ""
    if (Test-Path $logFile) { $hint = "`n`nThe last lines of $logFile say:`n" + ((Get-Content $logFile -Tail 8) -join "`n") }
    if ($what -eq "screen") {
        $hint += "`n`nA common cause: another program is already using port 5173. Close it (or restart the computer) and try again."
    }
    Fail "The $what $why.$hint"
}

Write-Host "Automated Academics" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path $py)) {
    Fail "The engine is not set up yet (backend\.venv is missing).`nFollow 'Quick start' in README.md once, then try again."
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Fail "Node.js was not found. Install Node.js (the 'LTS' version from nodejs.org), then try again."
}
if (-not (Test-Path (Join-Path $root "frontend\node_modules"))) {
    Write-Host "First run: installing the screen's components (needs internet, about a minute)..."
    Push-Location (Join-Path $root "frontend")
    try { npm install | Out-Host } finally { Pop-Location }
    if (-not (Test-Path (Join-Path $root "frontend\node_modules"))) { Fail "Installing the screen's components failed." }
}
New-Item -ItemType Directory -Force $logs | Out-Null

# ---- the engine (port 8000) ----
if (IsUp "http://127.0.0.1:8000/health") {
    Write-Host "Engine: already running"
} else {
    Write-Host -NoNewline "Engine: starting"
    # run from the backend folder so the data file (automated_academics.db) always lands in the same place
    $engine = Start-Process -FilePath $py -WorkingDirectory (Join-Path $root "backend") -WindowStyle Hidden -PassThru `
        -ArgumentList @("-m", "uvicorn", "automated_academics.api:create_app", "--factory", "--port", "8000") `
        -RedirectStandardOutput (Join-Path $logs "engine.out.log") -RedirectStandardError (Join-Path $logs "engine.log")
    WaitFor "engine" "http://127.0.0.1:8000/health" (Join-Path $logs "engine.log") $engine
}

# ---- the screen (port 5173; strict, because the engine only accepts requests from that address) ----
# It serves the built (production) version, which is about twice as fast to type into as the development
# server on large data. The build runs only when it is missing or the source is newer (about 20 seconds).
if (IsUp $url) {
    Write-Host "Screen: already running"
} else {
    $frontend = Join-Path $root "frontend"
    $built = Join-Path $frontend "dist\index.html"
    $sources = @(Get-ChildItem (Join-Path $frontend "src") -Recurse -File) +
        @(Get-Item (Join-Path $frontend "index.html"), (Join-Path $frontend "package.json"), (Join-Path $frontend "vite.config.ts") -ErrorAction SilentlyContinue)
    $newest = $sources | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not (Test-Path $built) -or ($newest -and $newest.LastWriteTime -gt (Get-Item $built).LastWriteTime)) {
        Write-Host "Screen: preparing (about 20 seconds)..."
        Push-Location $frontend
        try { npm run build 2>&1 | Out-Host } finally { Pop-Location }
        if (-not (Test-Path $built)) { Fail "Preparing the screen failed (see the messages above)." }
    }
    Write-Host -NoNewline "Screen: starting"
    $screen = Start-Process -FilePath "npm.cmd" -WorkingDirectory $frontend -WindowStyle Hidden -PassThru `
        -ArgumentList @("run", "preview", "--", "--port", "5173", "--strictPort") `
        -RedirectStandardOutput (Join-Path $logs "screen.out.log") -RedirectStandardError (Join-Path $logs "screen.log")
    WaitFor "screen" $url (Join-Path $logs "screen.out.log") $screen
}

Write-Host ""
Write-Host "Running at $url" -ForegroundColor Green
Write-Host "When you are finished, double-click 'Stop Automated Academics'."
if (-not $quiet) {
    Start-Process $url
    Start-Sleep 4
}
