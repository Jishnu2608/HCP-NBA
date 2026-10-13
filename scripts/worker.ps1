# Run the background worker (engine cycles, play-outs, the scheduled daily cycle) beside the
# web server, against the same database. Needed when NBA_WORKER_MODE=external (the default
# outside a local run); a local run hosts the worker inside the web process instead.
# Run from the repository root:  .\scripts\worker.ps1
$root = Split-Path -Parent $PSScriptRoot
Push-Location "$root\backend"
try { & .venv\Scripts\python -m app.worker }
finally { Pop-Location }
