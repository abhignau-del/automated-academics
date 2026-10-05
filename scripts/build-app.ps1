# Builds the Windows app: a folder you can zip and give to someone who has no Python or Node installed.
#
#   scripts\build-app.ps1
#
# Needs (on the machine that builds, not on the machine that uses the result): Python 3.11+ with the
# backend installed (pip install -e "backend[dev,build]") and Node 20+.
# Result: dist-app\Automated Academics\ and dist-app\Automated-Academics-<version>-windows.zip

$ErrorActionPreference = "Continue"  # native tools write progress to stderr; failures are caught by exit code below
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

Write-Host "1/3 Building the screen..." -ForegroundColor Cyan
Push-Location (Join-Path $root "frontend")
try { if (-not (Test-Path node_modules)) { npm ci; if ($LASTEXITCODE) { throw "npm ci failed" } }; npm run build:app; if ($LASTEXITCODE) { throw "screen build failed" } } finally { Pop-Location }

Write-Host "2/3 Packaging the app..." -ForegroundColor Cyan
$out = Join-Path $root "dist-app"
& $py -m PyInstaller (Join-Path $root "backend\packaging\automated_academics.spec") --noconfirm `
    --distpath $out --workpath (Join-Path $out "work")
if ($LASTEXITCODE) { throw "packaging failed" }

Write-Host "3/3 Zipping..." -ForegroundColor Cyan
$version = & $py -c "import automated_academics as a; print(a.__version__)"
$zip = Join-Path $out "Automated-Academics-$version-windows.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path (Join-Path $out "Automated Academics") -DestinationPath $zip
Write-Host "Built $zip" -ForegroundColor Green


