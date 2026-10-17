# Read-only check of the operating-system controls that keep the local model provider offline.
# Run from the repository root:  .\scripts\verify_ollama_egress.ps1   (add -Json for a record)
#
# It changes nothing: it reads firewall profiles and rules, network connections, (if readable)
# blocked-connection audit events, and makes one request to the local Ollama on 127.0.0.1.
# Creating the firewall rules is a separate, reviewed host change described in
# docs/ollama-network-isolation.md; this script never creates, enables or edits a rule.
#
# It reports five things separately, because they prove different things:
#   A. application-level restrictions  - not checked here (backend/tests/test_ai_network.py)
#   B. Windows Firewall configuration  - required: an enabled outbound Block rule per executable,
#      bound to that exact path, on every profile, to any remote address, port and protocol,
#      with the firewall itself enabled on every profile
#   C. observed blocked attempts       - evidence only (Security log 5157), never required
#   D. local inference availability    - required: Ollama still answers on loopback
#   E. OS-level isolation verdict      - VERIFIED only when B and D pass and nothing listens or
#      connects off loopback
#
# Exit code 0 only when the verdict is VERIFIED. Any doubt is NOT VERIFIED (exit 1).
param(
    [string]$OllamaDir = "$env:LOCALAPPDATA\Programs\Ollama",
    [string]$BaseUrl = "http://127.0.0.1:11434",
    [int]$EventHours = 24,
    [switch]$Json,
    # Checks this script's own rule evaluation against synthetic rule values, reads nothing.
    [switch]$SelfTest
)
$ErrorActionPreference = "Stop"
$failures = @()
$warnings = @()
$sections = [ordered]@{}

function Ok([string]$m) { if (-not $Json) { Write-Host "  [ok]   $m" } }
function Bad([string]$m) { if (-not $Json) { Write-Host "  [FAIL] $m" }; $script:failures += $m }
function Warn([string]$m) { if (-not $Json) { Write-Host "  [warn] $m" }; $script:warnings += $m }
function Head([string]$m) { if (-not $Json) { Write-Host "`n$m" } }

$programs = @(
    (Join-Path $OllamaDir "ollama.exe"),
    (Join-Path $OllamaDir "ollama app.exe")
)
$allProfiles = @("Domain", "Private", "Public")

function Covers-AllProfiles($profileValue) {
    $text = [string]$profileValue
    if ($text -eq "Any") { return $true }
    foreach ($p in $allProfiles) { if ($text -notmatch $p) { return $false } }
    return $true
}

# Why a Block rule does not isolate its executable (empty when it does).
function Rule-Problems($enabled, $profileValue, $remoteAddress, $protocol, $remotePort) {
    $why = @()
    if ([string]$enabled -ne "True") { $why += "disabled" }
    if (-not (Covers-AllProfiles $profileValue)) { $why += "profiles $profileValue (needs Any)" }
    if ((@($remoteAddress) -join ",") -ne "Any") { $why += "RemoteAddress $(@($remoteAddress) -join ',') (needs Any)" }
    if ([string]$protocol -ne "Any") { $why += "protocol $protocol only (needs Any)" }
    if ((@($remotePort) -join ",") -ne "Any") { $why += "RemotePort $(@($remotePort) -join ',') (needs Any)" }
    return ,$why
}

if ($SelfTest) {
    $cases = @(
        @{ name = "full scope"; args = @("True", "Any", "Any", "Any", "Any"); bad = $false },
        @{ name = "all three profiles"; args = @("True", "Domain, Private, Public", "Any", "Any", "Any"); bad = $false },
        @{ name = "disabled"; args = @("False", "Any", "Any", "Any", "Any"); bad = $true },
        @{ name = "public only"; args = @("True", "Public", "Any", "Any", "Any"); bad = $true },
        @{ name = "one remote range"; args = @("True", "Any", "10.0.0.0/8", "Any", "Any"); bad = $true },
        @{ name = "tcp only"; args = @("True", "Any", "Any", "TCP", "Any"); bad = $true },
        @{ name = "port 443 only"; args = @("True", "Any", "Any", "Any", "443"); bad = $true }
    )
    $wrong = 0
    foreach ($c in $cases) {
        $a = $c.args
        $problems = Rule-Problems $a[0] $a[1] $a[2] $a[3] $a[4]
        $isBad = $problems.Count -gt 0
        $mark = $(if ($isBad -eq $c.bad) { "ok" } else { $wrong++; "WRONG" })
        Write-Host "  [$mark] $($c.name): $(if ($isBad) { $problems -join '; ' } else { 'sufficient' })"
    }
    if ($wrong -gt 0) { Write-Host "self-test failed: $wrong case(s)"; exit 1 }
    Write-Host "self-test passed: $($cases.Count) cases"
    exit 0
}

Head "A. Application-level restrictions"
if (-not $Json) {
    Write-Host "  [info] not checked by this script: loopback resolution, no redirects, no proxy and no"
    Write-Host "         cloud provider are tested in backend/tests/test_ai_network.py"
}
$sections["application_level"] = "not_checked_here"

Head "B. Windows Firewall configuration"
$fwFail = $failures.Count
try {
    $profilesNow = @(Get-NetFirewallProfile -ErrorAction Stop)
    foreach ($p in $profilesNow) {
        if ([string]$p.Enabled -ne "True") { Bad "Windows Firewall is disabled on the $($p.Name) profile: rules there do nothing" }
    }
    if ($profilesNow.Count -gt 0 -and -not ($profilesNow | Where-Object { [string]$_.Enabled -ne "True" })) {
        Ok "Windows Firewall enabled on every profile"
    }
} catch {
    Bad "firewall profiles not readable: $($_.Exception.Message)"
}
$filters = @(Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue)
foreach ($program in $programs) {
    if (-not (Test-Path $program)) { Warn "not installed here: $program"; continue }
    $leaf = Split-Path $program -Leaf
    # Block rules for an executable of the same name at another path do not protect this one.
    $elsewhere = @($filters | Where-Object { $_.Program -and ((Split-Path $_.Program -Leaf) -ieq $leaf) -and ($_.Program -ine $program) } |
        Get-NetFirewallRule -ErrorAction SilentlyContinue | Where-Object { $_.Direction -eq "Outbound" -and $_.Action -eq "Block" })
    foreach ($r in $elsewhere) {
        $path = ($r | Get-NetFirewallApplicationFilter).Program
        Warn "rule '$($r.DisplayName)' blocks $leaf at a different path ($path), not $program"
    }
    $all = @($filters | Where-Object { $_.Program -ieq $program } | Get-NetFirewallRule -ErrorAction SilentlyContinue |
        Where-Object { $_.Direction -eq "Outbound" -and $_.Action -eq "Block" })
    if ($all.Count -eq 0) { Bad "no outbound Block rule bound to $program"; continue }
    $good = @()
    foreach ($r in $all) {
        $addr = ($r | Get-NetFirewallAddressFilter).RemoteAddress
        $port = $r | Get-NetFirewallPortFilter
        $why = Rule-Problems $r.Enabled $r.Profile $addr $port.Protocol $port.RemotePort
        if ($why.Count -gt 0) { Warn "rule '$($r.DisplayName)' for $leaf is insufficient: $($why -join '; ')" }
        else { $good += $r }
    }
    if ($good.Count -eq 0) { Bad "no enabled, full-scope outbound Block rule for $program" }
    else { Ok "outbound blocked for $program (rule: $($good[0].DisplayName); all profiles, any address, port and protocol)" }
}
$sections["windows_firewall"] = $(if ($failures.Count -eq $fwFail) { "configured" } else { "missing_or_insufficient" })

Head "Listeners and connections of running Ollama processes"
$netFail = $failures.Count
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
$sections["connections"] = $(if ($failures.Count -eq $netFail) { "loopback_only_now" } else { "non_loopback_seen" })

Head "C. Observed blocked attempts (Security log 5157, last $EventHours h; evidence only)"
try {
    $since = (Get-Date).AddHours(-$EventHours)
    $events = @(Get-WinEvent -FilterHashtable @{ LogName = "Security"; Id = 5157; StartTime = $since } -ErrorAction Stop |
        Where-Object { $_.Message -match "ollama" })
    if ($events.Count -gt 0) { Ok "$($events.Count) blocked outbound attempts by Ollama recorded (the block works)"; $sections["blocked_attempts"] = "observed" }
    else { Warn "no blocked attempt by Ollama recorded (none attempted, or 'Filtering Platform Connection' auditing is off)"; $sections["blocked_attempts"] = "none_recorded" }
} catch {
    Warn "Security log not readable here (run elevated, with 'Filtering Platform Connection' failure auditing on)"
    $sections["blocked_attempts"] = "not_readable"
}

Head "D. Local inference still answers on loopback"
try {
    $tags = Invoke-RestMethod -Uri "$BaseUrl/api/tags" -TimeoutSec 5
    Ok "local Ollama answers at $BaseUrl ($(@($tags.models).Count) models installed)"
    $sections["local_inference"] = "available"
} catch {
    Bad "local Ollama does not answer at $BaseUrl"
    $sections["local_inference"] = "unavailable"
}

$verdict = $(if ($failures.Count -gt 0) { "NOT_VERIFIED" } else { "VERIFIED" })
$sections["os_isolation"] = $verdict
if ($Json) {
    [ordered]@{
        checked_at = (Get-Date).ToString("s")
        verdict = $verdict
        sections = $sections
        failures = $failures
        warnings = $warnings
    } | ConvertTo-Json -Depth 4
} else {
    Head "E. OS-level isolation verdict"
    if ($failures.Count -gt 0) {
        Write-Host "NOT VERIFIED: $($failures.Count) required control(s) missing or insufficient. Network isolation must be reported as unverified."
    } else {
        Write-Host "VERIFIED for this machine at this time ($($warnings.Count) warning(s)). A point-in-time check, not a proof for other hosts."
    }
}
if ($failures.Count -gt 0) { exit 1 }
exit 0
