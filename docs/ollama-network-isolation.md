# Ollama network isolation: required host control

Status: **unverified on this machine** (2026-10-17). `scripts/verify_ollama_egress.ps1` reports no outbound block rule for the Ollama executables. The Windows Firewall default outbound action is "not configured", which means allow.

Until the rules below exist and the verification passes, AI network isolation must be reported as unverified. Application tests prove only what this application's own requests do (`backend/tests/test_ai_network.py`). They are not proof of operating-system isolation.

## What the application already enforces (application level)

- The provider URL must be loopback. The host is resolved on every request, and every address must be loopback (`app/ai/netguard.py`).
- No proxy and no redirect following. A local answer that redirects elsewhere is an error, never a second request.
- Model tags containing `cloud` are refused; `NBA_AI_PROVIDER=cloud` is refused.

What none of this controls: the Ollama server and its tray app are separate programs. Model pulls, update checks and any cloud feature of Ollama are theirs.

## Required control (a host change; review and authorise before applying)

Executables on this machine (read from the running processes):

| Program | Path |
|---|---|
| Server | `C:\Users\Jishnudeep\AppData\Local\Programs\Ollama\ollama.exe` |
| Tray app (updater) | `C:\Users\Jishnudeep\AppData\Local\Programs\Ollama\ollama app.exe` |

Rules to create, from an **elevated** PowerShell, by the machine owner (the application never does this):

```powershell
$dir = "$env:LOCALAPPDATA\Programs\Ollama"
New-NetFirewallRule -DisplayName "Ollama server: block outbound (local-only AI)" -Direction Outbound -Program "$dir\ollama.exe" -Action Block -Profile Any
New-NetFirewallRule -DisplayName "Ollama tray app: block outbound (local-only AI)" -Direction Outbound -Program "$dir\ollama app.exe" -Action Block -Profile Any
```

Scope and why it keeps local inference working:
- The rules block every outbound connection the two executables start, on every profile and to every remote address.
- Windows Firewall does not filter loopback traffic. The application reaches Ollama on 127.0.0.1, and Ollama answers on that same inbound loopback connection, so inference keeps working.
- Keep `OLLAMA_HOST` at its default (127.0.0.1) and start the server with `OLLAMA_NO_CLOUD=1`.
- Prefer running only `ollama serve` (quit the tray app).

Optional, for evidence of blocked attempts:

```powershell
auditpol /set /subcategory:"Filtering Platform Connection" /failure:enable
```

## Verification procedure

1. Create the rules (above) and restart Ollama.
2. Run `.\scripts\verify_ollama_egress.ps1` (read-only; `-Json` prints a record to keep, `-SelfTest` checks the script's own rule evaluation on synthetic values and reads nothing). It reports separately: A. application-level restrictions (not checked here: `backend/tests/test_ai_network.py`); B. Windows Firewall configuration; C. observed blocked attempts (evidence only); D. local inference availability; E. the OS-level isolation verdict. B fails when the firewall is off on any profile, or when a rule for either exact path is missing, disabled, limited to some profiles, some remote addresses, one protocol or some ports; a rule for an executable of the same name at another path does not count. It checks:
   - enabled outbound Block rules for both exact paths;
   - loopback-only listeners;
   - no non-loopback connections by Ollama;
   - blocked-attempt audit events (elevated shell);
   - local inference still answering on 127.0.0.1.

   Exit code 0 means the required controls are present.
3. Show that an unauthorised outbound connection is blocked: run `ollama pull library/does-not-exist-check` once. It must fail with a connection error, never "file does not exist", which would mean the registry was reached. It downloads nothing either way. With auditing on, a 5157 event naming `ollama.exe` appears in the Security log.
4. Show that permitted local inference still works: in the application, as administrator, open **AI context diagnostics → Health → Check the local provider** (one loopback request), and run one Case Brief generation in shadow mode on synthetic data.
5. Record the date, the rule names and the script output in `Workflow_Context.md`. Report isolation as verified only for this machine and this point in time.

## Limitations

- **DNS.** Name resolution is done by the Windows DNS Client service, not by `ollama.exe`. A blocked Ollama can still cause a DNS lookup of its registry or update host, revealing the hostname, not data. Blocking that needs DNS filtering or a network-level control.
- **Binary paths.** Rules are bound to executable paths. An Ollama reinstall to another folder needs new rules; re-run the script after every update.
- **Model updates.** With the rules in place, `ollama pull` and the tray app's updates stop working. Pulling a model is a deliberate, separately approved step: disable the rule, pull, re-enable, verify.
- **Policy.** Group Policy or other security software can override local rules; the script reads the effective rules only.
- **Scope.** This covers this machine only. Any other host running the application needs its own control and verification.
