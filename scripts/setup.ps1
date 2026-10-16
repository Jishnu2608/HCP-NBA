# One-time setup on Windows: Python environment, frontend build, database, seeded demo data.
# Run from the repository root:  .\scripts\setup.ps1
#
# Every step must succeed before the next one runs. Windows PowerShell does not stop when a
# program (python, npm, alembic) exits with an error, so each one is checked here: a failed
# step stops the script, and nothing after it runs. In particular, if `alembic upgrade head`
# is refused (for example on an existing traditional database), the demo data is not rebuilt.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Invoke-Step([string]$Name, [scriptblock]$Command) {
    Write-Host "`n==> $Name"
    $global:LASTEXITCODE = 0
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Setup stopped: '$Name' failed (exit code $LASTEXITCODE). Nothing after it ran."
    }
}

Push-Location "$root\backend"
try {
    if (-not (Test-Path ".venv")) { Invoke-Step "Create the Python environment" { py -3.13 -m venv .venv } }
    Invoke-Step "Upgrade pip" { & .venv\Scripts\python -m pip install --quiet --upgrade pip }
    Invoke-Step "Install the backend" { & .venv\Scripts\python -m pip install --quiet -e ".[dev]" }
}
finally { Pop-Location }

Push-Location "$root\frontend"
try {
    Invoke-Step "Install frontend packages" { npm install --no-audit --no-fund }
    Invoke-Step "Build the frontend" { npm run build }
}
finally { Pop-Location }

Push-Location "$root\backend"
try {
    Invoke-Step "Create local secrets" { & .venv\Scripts\python -m app.bootstrap }
    Invoke-Step "Migrate the database" { & .venv\Scripts\alembic upgrade head }
    Invoke-Step "Generate the demo data" { & .venv\Scripts\python -m app.datagen }
    Invoke-Step "Train models and run the first cycle" { & .venv\Scripts\python -m app.cycle --retrain }
}
finally { Pop-Location }

Write-Host "`nReady. Start the app with .\scripts\run.ps1 and open http://localhost:8000"
Write-Host "Account passwords are in .env (NBA_ADMIN_PASSWORD, NBA_DEMO_PASSWORD)."
