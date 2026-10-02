# One-time setup on Windows: Python environment, frontend build, database, seeded demo data.
# Run from the repository root:  .\scripts\setup.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Push-Location "$root\backend"
if (-not (Test-Path ".venv")) { py -3.13 -m venv .venv }
& .venv\Scripts\python -m pip install --quiet --upgrade pip
& .venv\Scripts\python -m pip install --quiet -e ".[dev]"
Pop-Location

Push-Location "$root\frontend"
npm install --no-audit --no-fund
npm run build
Pop-Location

Push-Location "$root\backend"
& .venv\Scripts\alembic upgrade head
& .venv\Scripts\python -m app.datagen
& .venv\Scripts\python -m app.cycle --retrain
Pop-Location

Write-Host "`nReady. Start the app with .\scripts\run.ps1 and open http://localhost:8000"
