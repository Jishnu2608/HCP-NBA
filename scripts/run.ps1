# Start the app (API + built frontend) on http://localhost:8000
# Run from the repository root:  .\scripts\run.ps1
$root = Split-Path -Parent $PSScriptRoot
Push-Location "$root\backend"
try { & .venv\Scripts\python -m uvicorn app.main:app --port 8000 }
finally { Pop-Location }
