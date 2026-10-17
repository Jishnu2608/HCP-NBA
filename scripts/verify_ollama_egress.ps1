# Read-only check of the operating-system controls that keep the local model provider offline.
# Run from the repository root:  .\scripts\verify_ollama_egress.ps1
#
# It changes nothing: it reads firewall rules, network connections, (if readable) blocked-
# connection audit events, and makes one request to the local Ollama on 127.0.0.1. Creating the
# firewall rules is a separate, reviewed host change described in docs/ollama-network-isolation.md.
#
# Exit code 0 only when every required control is in place and local inference still answers.
param(
    [string]$OllamaDir = "$env:LOCALAPPDATA\Programs\Ollama",
    [string]$BaseUrl = "http://127.0.0.1:11434",
    [int]$EventHours = 24
)
$ErrorActionPreference = "Stop"
$failures = @()
$warnings = @()

function Ok([string]$m) { Write-Host "  [ok]   $m" }
function Bad([string]$m) { Write-Host "  [FAIL] $m"; $script:failures += $m }
function Warn([string]$m) { Write-Host "  [warn] $m"; $script:warnings += $m }

$programs = @(
    (Join-Path $OllamaDir "ollama.exe"),
    (Join-Path $OllamaDir "ollama app.exe")
)

Write-Host "`n1. Outbound block rules for the Ollama executables"
$filters = Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue
foreach ($program in $programs) {
    if (-not (Test-Path $program)) { Warn "not installed here: $program"; continue }
    $rules = @($filters | Where-Object { $_.Program -ieq $program } | Get-NetFirewallRule -ErrorAction SilentlyContinue |
        Where-Object { $_.Direction -eq "Outbound" -and $_.Action -eq "Block" -and $_.Enabled -eq "True" })
    if ($rules.Count -eq 0) { Bad "no enabled outbound Block rule for $program" ; continue }
    $profiles = ($rules | ForEach-Object { $_.Profile }) -join ","
    Ok "outbound blocked for $program (rule: $($rules[0].DisplayName); profiles: $profiles)"
    foreach ($r in $rules) {
        $addr = ($r | Get-NetFirewallAddressFilter).RemoteAddress
        if ($addr -ne "Any") { Warn "rule '$($r.DisplayName)' limits RemoteAddress to $addr (expected Any)" }
    }
}

Write-Host "`n2. Running Ollama processes and their connections"
$procs = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and ($programs -contains $_.Path) })
if ($procs.Count -eq 0) { Warn "no Ollama process running (local inference cannot be checked)" }
foreach ($p in $procs) {
    $conns = @(Get-NetTCPConnection -OwningProcess $p.Id -ErrorAction SilentlyContinue)
    $listen = $conns | Where-Object { $_.State -eq "Listen" }
    foreach ($l in $listen) {
        if ($l.LocalAddress -in @("127.0.0.1", "::1")) { Ok "$($p.ProcessName) ($($p.Id)) listens on $($l.LocalAddress):$($l.LocalPort) only" }
        else { Bad "$($p.ProcessName) ($($p.Id)) listens on $($l.LocalAddress):$($l.LocalPort) (not loopback; check OLLAMA_HOST)" }
    }
    $remote = $conns | Where-Object { $_.State -ne "Listen" -and $_.RemoteAddress -notin @("127.0.0.1", "::1", "0.0.0.0", "::") }
    if ($remote) { foreach ($c in $remote) { Bad "$($p.ProcessName) has a connection to $($c.RemoteAddress):$($c.RemotePort) ($($c.State))" } }
    else { Ok "$($p.ProcessName) ($($p.Id)) has no connection to a non-loopback address right now" }
}
if ($procs | Where-Object { $_.ProcessName -eq "ollama app" }) {
    Warn "the Ollama tray app is running; it makes its own update checks (run only 'ollama serve')"
}

Write-Host "`n3. Blocked-connection audit events (Security log 5157, last $EventHours h)"
try {
    $since = (Get-Date).AddHours(-$EventHours)
    $events = @(Get-WinEvent -FilterHashtable @{ LogName = "Security"; Id = 5157; StartTime = $since } -ErrorAction Stop |
        Where-Object { $_.Message -match "ollama" })
    if ($events.Count -gt 0) { Ok "$($events.Count) blocked outbound attempts by Ollama recorded (the block works)" }
    else { Warn "no blocked attempt by Ollama recorded (none attempted, or 'Filtering Platform Connection' auditing is off)" }
} catch {
    Warn "Security log not readable here (run elevated, with 'Filtering Platform Connection' failure auditing on)"
}

Write-Host "`n4. Local inference still answers on loopback"
try {
    $tags = Invoke-RestMethod -Uri "$BaseUrl/api/tags" -TimeoutSec 5
    Ok "local Ollama answers at $BaseUrl ($(@($tags.models).Count) models installed)"
} catch {
    Bad "local Ollama does not answer at $BaseUrl"
}

Write-Host ""
if ($failures.Count -gt 0) {
    Write-Host "NOT VERIFIED: $($failures.Count) required control(s) missing. Network isolation must be reported as unverified."
    exit 1
}
Write-Host "Required controls present ($($warnings.Count) warning(s)). This is a point-in-time check of this machine, not a proof for other hosts."
exit 0
