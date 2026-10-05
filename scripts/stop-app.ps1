# Stops Automated Academics (the engine on port 8000 and the screen on port 5173).
# Your data is kept: it lives in backend\automated_academics.db.
#
# Only processes that are recognisably this app are stopped. If some other program happens to be
# using one of the ports, it is left alone and you are told.
#
# Set AA_NO_BROWSER=1 to skip the closing pause (used by tests).

$quiet = [bool]$env:AA_NO_BROWSER
$root = Split-Path -Parent $PSScriptRoot
Write-Host "Automated Academics" -ForegroundColor Cyan
Write-Host ""

$parts = @(
    @{ Port = 8000; Name = "engine"; Is = { param($cmd) $cmd -match 'automated_academics\.api' } },
    @{ Port = 5173; Name = "screen"; Is = { param($cmd) ($cmd -match 'vite') -and ($cmd -match [regex]::Escape($root)) } }
)

$foreign = @()
foreach ($part in $parts) {
    $label = "{0,-7}" -f ($part.Name + ":")
    $owners = @(Get-NetTCPConnection -LocalPort $part.Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
    if ($owners.Count -eq 0) { Write-Host "$label was not running"; continue }
    $stopped = $false
    foreach ($id in $owners) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue
        if ($p -and $p.CommandLine -and (& $part.Is $p.CommandLine)) {
            & taskkill.exe /PID $id /T /F | Out-Null  # /T also ends anything it started
            $stopped = $true
        } else {
            $foreign += "port $($part.Port) is used by another program ($($p.Name)), which was left alone"
        }
    }
    if ($stopped) { Write-Host "$label stopped" } else { Write-Host "$label not ours" }
}

Start-Sleep 1
$left = @()
foreach ($part in $parts) {
    foreach ($id in @(Get-NetTCPConnection -LocalPort $part.Port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique)) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue
        if ($p -and $p.CommandLine -and (& $part.Is $p.CommandLine)) { $left += $part.Name }
    }
}

Write-Host ""
foreach ($note in ($foreign | Select-Object -Unique)) { Write-Host "Note: $note." -ForegroundColor Yellow }
if ($left.Count -gt 0) {
    Write-Host ("The {0} could not be stopped. Try again, or restart the computer." -f ($left -join " and the ")) -ForegroundColor Red
    if (-not $quiet) { Read-Host "Press Enter to close this window" | Out-Null }
    exit 1
}
Write-Host "Stopped. Your data is saved." -ForegroundColor Green
if (-not $quiet) { Start-Sleep 3 }
