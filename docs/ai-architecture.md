# Shared AI layer: architecture

Status (2026-10-16): built for a **synthetic-data prototype**. The shared infrastructure in
`backend/app/ai/` is in use by one registered operation, the Case Brief, which is the
integration example, not the scope limit. The deterministic template is the default output
(`NBA_AI_CASE_BRIEF_MODE=template`). Nothing here is a production-readiness or regulatory claim.
Real patient data, production deployment and cloud inference are out of scope.

## Principles

- **Deterministic rules stay authoritative.** NBA eligibility, consent, MLR approval, contact
  limits, delivery and access control are decided in code. Core modules never import `app.ai`
  (enforced by `tests/test_read_services.py`).
- **The model only words what the server already decided it may say,** from evidence the
  server chose. Its output is untrusted until the server's checks pass. A model cannot mark its
  own output safe: the output schema forbids extra fields and every check runs on the server.
- **Model output never acts.** The only writes on a generation are its own `ai_generation` row
  and audit rows. No record, consent, permission, case status or workflow state changes.
- **One provider, chosen on the server, never switched silently.** Local Ollama only in this
  phase. Off, misconfigured or failing means the operation's deterministic template.
- **Fail closed.** An unclassified field, an authorisation doubt, a data-policy doubt or any
  failed check means no model call or no model text, never partial data.

## Components

| Module | Job |
|---|---|
| `app/ai/contracts.py` | Strict types (`extra="forbid"`): `ContextItem` (id, kind, text, source, source_id, recorded_at, sensitivity), `ReaderNote`, `ContextRequest`, `AuthorizedContext`, `GenerationRequest`/`GenerationResult`, `GateFinding`/`GateOutcome`, `GenerationStatus`, `FailureCategory`, `Mode`, `AuditEvent`, `OperationSpec` |
| `app/ai/registry.py` | Explicit registration; only registered operations exist; an operation that opts into cloud or declares no allowlist is refused |
| `app/ai/operations/` | Approved operations. Today: `case_brief.py` |
| `app/ai/context.py` | The context service (below) |
| `app/ai/context_policy.py` | Field classification for Patient 360 and its nested records; free-text isolation checks |
| `app/ai/safety.py` | Shared deterministic checks on model text |
| `app/ai/providers.py` | `OllamaProvider` (loopback only, no cloud tags), `get_provider`, `check_data_policy`; `CloudProvider` is a placeholder that never sends |
| `app/ai/orchestrator.py` | The single generation path: `submit`, `run`, `current` |
| `app/api/ai.py` | `GET /api/ai/{operation}/patients/{id}`, `POST /api/ai/{operation}/patients/{id}/generations` |
| `ai_generation` table | One row per attempt (migration `b4f8d2a6c9e1`) |
| `frontend/src/ai.tsx`, `frontend/src/pages/CaseBrief.tsx` | Reusable result components; the Case Brief panel on Patient 360 |

## Request path

```
POST .../generations (session cookie + CSRF)
  submit   resolve spec -> operation permission + scoped read (403 / 404, audited)
           -> mode (template refuses, audited) -> duplicate check -> rate limit (per account)
           -> capacity cap -> ai_generation row (queued) -> job with lock ai:<hash(op, subject)>
worker job
  run      re-authorise as the requester (assignment may have changed)
           -> rebuild context -> provider selection (local only) -> data policy
           -> free-text and identifier checks on the exact request -> provider call
           -> schema -> shared safety checks + operation checks -> completed | fallback | failed
           -> audit
GET ...
  current  re-authorise -> rebuild context -> template, or the checked model result when the
           mode is prototype and the result is current -> notes, sources, generation status
```

## Context service and data allowlists

`app/ai/context.build(db, user, request, permission, allowed_kinds)`:

1. Checks the operation permission (`ai:case_brief`, care manager only).
2. Reads through `services.patients.patient_profile`, which applies the read permission and the
   record scope (out of scope looks like not found).
3. Classifies every Patient 360 field and every field of nested therapies, fills, interactions
   and the open recommendation as MODEL, READER_ONLY or EXCLUDED. An unclassified field raises.
4. Writes items only from MODEL fields, each with provenance (read service, record id, date).
5. Keeps free text (care notes, outreach notes and reasons, the health record) for the reader
   only, verbatim, labelled `Untrusted; review for clinical content.` The label never depends on
   the note's wording, and a note never changes the context, authorisation or any workflow.
6. Limits the model's view to the operation's allowlisted kinds; `model_items` refuses anything
   else loudly.
7. Records identifiers (patient id, name parts, city, care team names) and withheld text, which
   are checked against the exact provider request before it is sent and against model output.
8. Fingerprints the items; a stored result whose fingerprint differs is stale and not shown.

Case Brief allowlist: `patient` (age), `rx` (drug, supply, last refill, next due), `pdc`,
`touch` (latest five outreach records), `nba` (action, channel, content id, status,
schedule), `absent` (what is not recorded). Not allowlisted: consent (stated by the server's
own key point), notes, contact details, names, identifiers, conditions, risk, cost.

## Safety checks

Shared (`safety.check_text_fields`), on every text field the model writes, judged against what
the model was given: invalid citations; fabricated dates, numbers, ids, drugs (fixed list plus the
subject's own record) and conditions; dosing, medication advice and clinical claims; medication
and symptom wording; a recommendation named when none is recorded; consent or permission
claims; claims that an action was taken; contact details, links, identifiers and copied note
text; facts not found in the cited items (miscited). Operation checks add their own (Case
Brief: length). Schema validation comes first; anything over the size limit is rejected.

## Modes and fallback

| Mode | Model call | Reader sees |
|---|---|---|
| `template` (default) | none; a request is refused (409) and audited | template |
| `shadow` | yes, checked and audited | template; status says the wording was recorded, not shown |
| `prototype` | yes | model wording when every check passed and the result is current, marked AI-generated and review-required; otherwise template |

Failure categories: `provider_disabled`, `provider_unavailable` (error or timeout),
`data_policy`, `schema_invalid`, `gate_rejected`, `access_lost`, `interrupted`, `internal`.
Unknown mode values mean template.

## Jobs

Generations reuse the `job` table and worker (`python -m app.worker`, or the embedded thread in
a local run). Each takes its own lock `ai:<hash>` (one queued or running generation per
operation and subject; a second request returns the active one) and never the engine lock, so
it neither blocks nor waits for engine operations. `NBA_AI_MAX_ACTIVE_JOBS` (2) caps queued and
running generations; `ai_generate` limits each account to 10 requests per 10 minutes. No
automatic retry: asking again creates a new generation. A worker that stops marks its job
interrupted, and the generation is then marked failed (`interrupted`). The job row holds only
the generation id and final status. The provider timeout is `NBA_AI_TIMEOUT_SECONDS` (180).

## Audit and retention

Actions: `ai_generation_requested`, `ai_generation_completed`, `ai_generation_fallback`,
`ai_generation_failed`, `ai_generation_rejected` (forbidden, not found, template mode, rate
limited, busy). Detail (`AuditEvent`): operation and version, prompt version, mode, subject,
generation and job ids, provider, model, model digest, context fingerprint, context item ids,
gate reason codes, failure category, duration, token counts. Never prompts, notes, model text
or patient narrative. Audit rows are read through the existing audit permissions (identities
masked unless `audit:read:identified`) and kept like every audit row.

The `ai_generation` row keeps the composed result only when every check passed (needed to show
it); rejected model text is never stored. Rows beyond `NBA_AI_KEEP_GENERATIONS` (5) per subject
are deleted; all rows go when the patient is deleted.

## Databases: traditional and AI

The AI tables live only in `data/nba_demodb_ai.db`, a verified copy of the traditional
`data/nba_demo.db` (created 2026-10-16 from backup `backups/nba_demo-20261016T102429Z.db`,
integrity ok, equal row counts; then migrated to `b4f8d2a6c9e1`). The traditional file stays at
`a7d2e9c4b1f6` and unchanged.

- `NBA_DATABASE_PROFILE=traditional|ai` selects the file (default traditional); setting it
  together with `NBA_DATABASE_URL` is refused; an unknown value is refused.
- The web process and the worker refuse to start if the configured SQLite file is missing
  (`core.db.require_existing`), so no empty replacement is ever created.
- `alembic/env.py` refuses `upgrade`, `downgrade` and `stamp` on the existing traditional file
  unless `-x allow_traditional=yes`; reading commands are unaffected.
- Without the AI tables: AI routes answer 503 `ai_unavailable` after the usual 403/404 access
  check (the refusal is audited); a queued job cannot write; the frontend panel says the Case
  Brief is not available here. Loops over every model table (demo reset wipe and counts, patient
  erasure, `/api/meta`) use `core.db.present_tables`, so the rest of the app works unchanged.
  `/api/meta` reports `database_profile` and `ai_tables`; start-up logs both.
- Fallback is an explicit switch to `traditional` and a restart. The two databases then
  diverge; nothing is copied or merged between them.

## Provider configuration and the cloud-disabled guarantee

| Variable | Meaning |
|---|---|
| `NBA_AI_PROVIDER` | `ollama` (default) or `off`. `cloud` is refused whatever else is set |
| `NBA_AI_OLLAMA_BASE_URL` | Loopback URL only (`127.0.0.1`, `localhost`, `::1`); no proxy |
| `NBA_AI_OLLAMA_MODEL` | Local tag; tags containing `cloud` are refused. Default `qwen3.5:9b-q4_K_M` |
| `NBA_AI_TIMEOUT_SECONDS` | Provider timeout (180) |
| `NBA_AI_CASE_BRIEF_MODE` | `template` (default), `shadow`, `prototype` |
| `NBA_AI_MAX_ACTIVE_JOBS`, `NBA_AI_KEEP_GENERATIONS` | Capacity cap, retention per subject |

Guarantees, each covered by tests: `get_provider` raises for `cloud`; no operation may register
with `allow_cloud`; the orchestrator refuses a non-local provider object; no automatic switch
to another provider. Run Ollama itself with `OLLAMA_NO_CLOUD=1`. The Ollama tray app makes its
own update-check connections; run only `ollama serve` (or block the tray app) for a fully
loopback machine. The older outreach drafter (`app/llm/`) still has a `ClaudeProvider` that can
call a cloud API if an operator sets `NBA_LLM_PROVIDER=claude` and
`NBA_ALLOW_EXTERNAL_IDENTIFIABLE_DATA=true`; both are off by default. It is tracked in `TODO.md`
to move onto this layer.

## Adding an operation later

Write an `OperationSpec` in `app/ai/operations/` (id, version, permission, context allowlist,
prompt builder, strict output model and schema, operation checks, compose and fallback), add a
permission to the role map, register it, evaluate it on synthetic data with fixed thresholds
before any reader sees its wording. The orchestrator, context service, shared checks, jobs,
audit and API need no change. No operation is approved beyond the Case Brief.

## Case Brief

Prompt v10, operation version 2 (`app/ai/operations/case_brief.py`). The server writes key
points, consent, what is not recorded and the template summary and focus; since version 2 the
template also states unanswered outreach from recorded outcomes ("No response to 4 of the last 5
outreach records (latest 2026-09-09).", citing exactly those records), so the template carries
the outreach pattern the model wording added in testing; the model writes `summary` and
`suggested_focus` with citations, constrained to the item ids of the call. Evaluation history:
`backend/evals/case_brief/` (standalone, frozen copy of the checks); integrated evaluation on the
application path: `backend/evals/case_brief_app/`. Known wording limits from the v10 hand read
(for example "contact the care manager" addressed to the care manager) are why every shown
result is marked for review.

## Run locally

```powershell
cd backend
$env:NBA_DATABASE_PROFILE = "ai"            # the AI tables live only in the AI database
.\.venv\Scripts\alembic upgrade head
$env:NBA_AI_CASE_BRIEF_MODE = "prototype"   # or set it in .env; default is template
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000
```

Ollama must be running locally with the model pulled (`ollama pull qwen3.5:9b-q4_K_M`), with
`OLLAMA_NO_CLOUD=1` and, on an Intel integrated GPU, `OLLAMA_IGPU_ENABLE=1`.
