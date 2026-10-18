# Shared AI layer: architecture

Status (2026-10-17): built for a **synthetic-data prototype**.

- The shared infrastructure in `backend/app/ai/` is an application-wide, role-aware context layer: registered context sources over the typed read services, one central field policy, shared safety and evidence checks, multi-subject and aggregate contexts, retrieval and turn contracts, and one orchestration path.
- One operation is registered, the Case Brief. It is a consumer of the layer, not its centre.
- The deterministic template is the default output (`NBA_AI_CASE_BRIEF_MODE=template`).
- Administrators can inspect the layer under **AI context diagnostics**.
- Nothing here is a production-readiness, regulatory or network-isolation claim. Real patient data, production deployment and cloud inference are out of scope.

## Principles

- **Deterministic rules stay authoritative.** NBA eligibility, consent, MLR approval, contact limits, delivery and access control are decided in code. Core modules never import `app.ai` (enforced by `tests/test_read_services.py`).
- **Access is decided by the read services, never by the AI layer.** A context source reads through the same typed, scoped services as the screens. Their `Forbidden`, `NotFound` and `NoRecord` propagate unchanged, so out of scope looks like missing. Registering a source or an operation grants nothing.
- **The model only words what the server already decided it may say,** from evidence the server chose. Its output is untrusted until the server's checks pass. A model cannot mark its own output safe: the output schema forbids extra fields and every check runs on the server.
- **Model output never acts.** The only writes on a generation are its own `ai_generation` row and audit rows. Building a context writes nothing at all (`readonly.guard`).
- **"Not in this context" is never "not recorded".** Every category carries a state, and absence claims are checked against it.
- **One provider, chosen on the server, never switched silently.** Local Ollama only in this phase. Off, misconfigured or failing means the operation's deterministic template.
- **Fail closed.** Any of these means no model call or no model text, never silently partial data:
  - an unclassified field;
  - an authorisation doubt;
  - a data-policy doubt;
  - an incomplete context;
  - a failed check.

## Components

| Module | Job |
|---|---|
| `app/ai/contracts.py` | Strict types (`extra="forbid"`): `SubjectType` (16 subject types, each backed by a source), `ScopeKind`, `CategoryState`, `CategoryStatus`, `ContextRequest` (subject, categories, page size, cursor, freshness, completeness, correlation id), `ContextItem` (with optional field path, category, source version, opaque subject reference, as-of date), `ReaderNote`, `AuthorizedContext` (with category states, `complete`, policy version, source, scope key), `GenerationRequest`/`GenerationResult`, `GateFinding`/`GateOutcome`, `GenerationStatus`, `FailureCategory`, `Mode`, `AuditEvent`, `OperationSpec` |
| `app/ai/sources/` | The context source registry (`sources/__init__.py`) and one module per domain: `patient.py`, `hcp.py`, `recommendation.py`, `content.py`, `care.py` (care request, queue, consultations, patient self, cycle times), `operational.py` (AI telemetry, audit activity, job queue, quality measures, access scope). Unsupported domains are inventoried there with their reason |
| `app/ai/policy/` | The central field policy (`fields.py`: every field of every record type a source reads) and its enforcement (`engine.project`) |
| `app/ai/engine.py` | `build_context(db, user, request, consumer)`: the one way to build a context |
| `app/ai/context.py` | Compatibility entry point used by the orchestrator and the Case Brief (`build(..., permission, allowed_kinds)`) |
| `app/ai/context_policy.py` | The original Patient 360 classification, kept for the Case Brief's projection and free-text checks |
| `app/ai/aggregate.py` | Bounded multi-subject contexts (`build_many`) |
| `app/ai/retrieval.py` | Task, turn, retrieval query and evidence-passage contracts; `retrieve`, `render_evidence`, `validate_passages` |
| `app/ai/safety.py` | Shared deterministic checks on model text, including absence, status, code and instruction checks, and `screen_injection` |
| `app/ai/readonly.py` | `guard(db)`: refuses any flush while a context is read |
| `app/ai/netguard.py` | Loopback resolution check and a no-redirect, no-proxy opener for the local provider |
| `app/ai/health.py` | Provider availability: configuration only, or one explicit loopback probe |
| `app/ai/providers.py` | `OllamaProvider` (loopback only, no cloud tags, `netguard`), `get_provider`, `check_data_policy`; `CloudProvider` is a placeholder that never sends |
| `app/ai/orchestrator.py` | The single generation path: `submit`, `run`, `current` |
| `app/ai/fixtures.py`, `app/ai/diagnostics.py` | The synthetic fixture world and the Admin diagnostics logic |
| `app/api/ai.py`, `app/api/ai_diagnostics.py` | Case Brief routes; Admin diagnostics routes |
| `ai_generation` table | One row per attempt (migration `b4f8d2a6c9e1`, AI database only) |
| `frontend/src/ai.tsx`, `pages/CaseBrief.tsx`, `pages/AiDiagnostics.tsx` | Result components, the Case Brief panel, the diagnostics page |

## Subject types and sources

| Subject type | Scope | Source | Read service | Model-visible categories |
|---|---|---|---|---|
| `patient` | single | `patient_360` | `patients.patient_profile(refresh=False)` | `patient`, `rx`, `pdc`, `touch`, `nba`, `absent`; on request `care_request`, `condition`, `medication_record`. `consent` is server text only |
| `hcp` | single | `hcp_360` | `hcps.hcp_profile` | `hcp`, `contact_window`, `hcp_touch`, `hcp_nba`, `hcp_work` |
| `recommendation` | single | `recommendation` | `recommendations.recommendation_detail` | `decision`, `reason` (codes only), `candidate`, `response` |
| `content` | single | `content` | `content.content_version` | `content` (metadata, never the wording) |
| `care_request` | single | `care_request` | `care_requests.care_request` | `care_request` |
| `patient_self` | single (session) | `patient_self` (**policy blocked**) | `self_service.own_health` | `own_request`, `own_condition`, `own_medication`, for deterministic use and diagnostics only |
| `care_queue` | collection | `care_queue` | `care_requests.care_queue` | `queue_item` (paged), `queue_counts` |
| `consultation_queue` | collection (session) | `consultation_queue` (**policy blocked**: HCP-facing AI not decided) | `consultations.hcp_consultations` | `consultation` (paged), `consultation_counts`, for deterministic use and diagnostics only |
| `care_workflow` | aggregate | `care_workflow` | `care_requests.request_times` | `cycle_time` (median and p90 days, computed in code) |
| `content_lineage` | aggregate | `content_lineage` | `content_lineage.content_lineage` | `version`, `reference` (counts within the reader's recommendation scope) |
| `ai_telemetry` | aggregate | `ai_telemetry` | `ai_telemetry.ai_telemetry` | `generation_outcome` (subjects counted, never listed) |
| `audit_activity` | aggregate | `audit_activity` | `audit.audit_activity` | `audit_event` (action, day, count) |
| `job_queue` | aggregate | `job_queue` | `jobs.job_queue` | `job_state` |
| `quality_measures` | aggregate | `quality_measures` | `analytics.quality_measures` | none: `measure` figures are pending review (server text only) |
| `access_scope` | single | `access_scope` | `access.access_scope` | none (server text only) |

New typed read services moved out of routers, which now only translate HTTP:
- `services/care_requests.py`: `GET /api/care/requests`;
- `services/consultations.py`: `GET /api/me/consultations`;
- `services/self_service.py`: `GET /api/me/health`;
- `services/audit.py`: `GET /api/audit`, identity masking unchanged.

Also new: `ai_telemetry.py`, `jobs.py`, `content_lineage.py`, `access.py`.

Inventoried but not context sources (reason in the registry):
- inboxes (personalised delivered wording, free-text questions);
- legal acceptances and privacy requests;
- invitations (contact data; reading refreshes their status);
- health goal plans, readings and coins (clinical readings need a decision);
- an HCP's own profile;
- MLR review records and the governance conversation (free text).

## Building a context

`engine.build_context(db, user, request, consumer)`:

1. Checks the consumer's permission (`core.permissions.can`). A `Consumer` names its permission, its category allowlist, whether it calls a model, and its item and character budget.
2. Resolves the source for the subject type. It refuses:
   - unsupported and disabled sources;
   - policy-blocked sources, for a consumer that calls a model.
3. Reads through the source's typed read service under `readonly.guard`, inside a SAVEPOINT. The read services decide access. A failed query is undone back to the savepoint only, so the caller's own uncommitted work is kept, and its categories become `unavailable`. Without the deployment secret (`NBA_JWT_SECRET`) no context is built: opaque references need a key that is the same in every process.
4. Projects every record through the central field policy. An unclassified field raises.
5. Writes model-eligible facts as items, with provenance: source, record id, field, version, date and an opaque subject reference.
6. Gives every category a state:

   | State | Meaning |
   |---|---|
   | `included` | Read and present |
   | `verified_empty` | Read; the source holds no matching record |
   | `not_requested` | Not read |
   | `excluded_policy` | The field policy keeps it from a model |
   | `excluded_allowlist` | Read but outside the consumer's allowlist |
   | `unavailable` | The read failed |
   | `truncated` | The page or the budget cut it |
   | `stale` | Derived figures older than today, or (with `max_age_days`) the category's newest fact is older than the limit or carries no date (`older_than_max_age`, `freshness_unknown`) |

7. Reduces the model view to the allowlist, intersected with the policy, within the budget (whole items only; a cut category is `truncated`, reason `context_budget`). Every model item is then checked against the consumer's own allowlist, the model categories and its sensitivity; a violation refuses the build.

Freshness: a category is judged by its items' dates (`Category.freshness = "item"`) or, for facts describing the current state as read (counts, aggregates, profile attributes), as fresh as the context (`"current"`). An operation sets `OperationSpec.max_age_days`.
8. Fingerprints the items. For principal-dependent views (queues, scoped counts) the reader's scope key is added, so a stored result is never reused across readers.

`complete` is False when any requested category is truncated, unavailable or stale. The orchestrator then falls back:
- `context_unavailable` when a source could not be read;
- `context_incomplete` otherwise.

An operation must set `allow_partial` to describe partial data. None does. With it, absence claims about stale, truncated or unavailable categories are still rejected.

Reads never write. A real patient's adherence figures that are not as of today are reported as `stale` rather than recomputed. The Case Brief routes keep the screen behaviour: like opening Patient 360, they bring the figures up to today before the AI layer reads them (`patients.refresh_if_stale`).

## Field policy

`app/ai/policy/fields.py` classifies every field of every record type:

| Disposition | Meaning |
|---|---|
| `MODEL` | May be written into model-visible items |
| `MODEL_IF_VOCABULARY` | A medicine name: model-visible only if it is in the approved vocabulary; a typed name becomes "an unlisted medication" and the original is withheld |
| `DETERMINISTIC_ONLY` | For server-written text and figures |
| `READER_ONLY` | Free text: shown verbatim to an authorised reader under `Untrusted; review for clinical content.`, withheld from every model request |
| `RESTRICTED` | Identifiers, contact and personal details, internal text |
| `PENDING_REVIEW` | Needs a governance decision (approved MLR wording, HCP commercial fields, group adherence figures including quality-measure shares). Never model-visible; available to server-side deterministic code only. Approved for MLR does not mean approved for AI processing |

Further rules:
- `GLOBAL_PROHIBITED` field names (names, ids, contact and location fields, notes, reasons, bodies, comments, feedback and so on) can never be model-visible. This is checked at import and by tests.
- The `health` block is classified record by record: care requests, conditions, medications and notes. Structured request status, routing state, due dates and timestamps can reach a model; reasons, resolutions, notes and typed labels cannot.
- A consumer's allowlist only narrows the policy; a category whose items the server cites but a model never reads (consent, access scope) stays out whatever is asked.
- The registry refuses an operation that lists such a category, a category its source does not supply, a policy-blocked subject, or limits above the global caps.

## Multi-subject, collections and aggregates

- **Collections** are subject types with their own paging: `limit` ≤ 50, plus a `cursor`. A truncated page is reported, with a next cursor.
- **Aggregates** are computed in code by their sources from scoped records. Records appear as ordinals and figures only.
- **`aggregate.build_many`** combines up to 20 record subjects:
  - each is authorised on its own;
  - an unauthorised or missing id is counted, never named;
  - a failed read is counted;
  - ids beyond the limit are dropped and counted;
  - the result is complete only when nothing was dropped, refused or failed.
- **Generation rows for self, collection and aggregate subjects** are filed under an opaque key of (subject type, reader). A run as anyone else is refused (`access_lost`). The job lock is per operation and subject or scope.

## Retrieval and turns (contracts only)

`app/ai/retrieval.py` provides the contracts a future approved interactive consumer would use. No such consumer, screen or conversation store exists.

- `TaskInput`: up to 500 characters, control characters removed. Instruction-like patterns are flagged (`screen_injection`) and the retrieval is refused.
- `Turn`: server-made session and turn ids. It belongs to the account that opened it.
- `RetrievalQuery`: registered subjects and categories only, all within the consumer's map.
- `retrieve` rebuilds every subject's context through the engine on every call (per-turn re-authorisation). Passages are ranked deterministically and kept only above `min_relevance`. Below it the result is `insufficient` and a consumer must refuse.
- `render_evidence` delimits passages as data.
- `validate_passages` rejects citations to passages that are not in the result, or that a fresh retrieval shows to be stale.

Nothing is persisted, so no retention or deletion policy is needed until a store is approved.

## Safety checks

Shared checks run on every text value the model writes, judged against what the model was given (`safety.check_text_fields`). Which values those are is derived from the output schema (`app/ai/output_contract.py`), not from what an operation lists: every string path of `output_model` must be cited text (`cited_fields`, with a sibling citation list), uncited text (`text_fields`, checked with no citations, so a record fact there fails) or a `Literal` / `Enum` value. Registration refuses anything else (an undeclared text field, `dict` or `Any`, unions, models that accept extra fields), and `evaluate` refuses an output holding a string at an uncovered path (`unchecked_field`), so an unregistered or mutated spec fails closed too. Nested models and lists use paths such as `points[].text`. From the start:
- invalid citations;
- fabricated dates, numbers, ids, drugs and conditions;
- dosing, medication advice and clinical claims;
- medication and symptom wording;
- a recommendation named when none is recorded;
- consent or permission claims;
- claims that an action was taken;
- contact details, links, identifiers and copied note text;
- facts not in the cited items.

Added for the application-wide layer:
- `unsupported_absence:<state>`: a "not recorded / missing / no X" claim about a category that is not `verified_empty`;
- `unverifiable_absence`: an absence claim that names no known category;
- `fabricated_status` and `contradiction:status`: a decision or workflow status no item states;
- `fabricated_code`: a reason or gate code no item contains;
- `leak:instructions`: the operation's own instructions repeated;
- `injection_echo`: instruction-like patterns in the output.

Absence phrases include paraphrases ("could not find any refills", "no recorded fills", "has never refilled", "has not been logged", "unable to locate") and inflected category words. An absence phrase is mapped to the category words inside it, or, if it names none, the first one that closely follows it, or the nearest one before it. A status is checked when asserted ("was approved") and when used as an adjective of an item ("the blocked reminder"); named material ("the approved cost-support offer content") is not a status claim; and when the field cites items that state a status, the claimed status must be one of theirs. A recommendation may be named when an item of any category whose words name a recommendation is in the context (not one consumer's item kind). It is supported only when every target category is `verified_empty`, or when a cited item of that category states the same negated fact about the same medicine ("no fill recorded" with "not yet filled"). These are layered, conservative keyword checks, not semantic proof. When in doubt they reject, and the template stands. Operation checks add their own (Case Brief: length). Schema validation comes first; anything over the size limit is rejected. Before the provider call the request is checked for withheld free text, identifiers and the text of any item outside the model view (a prompt builder that copies server-text-only or non-allowlisted items is refused, `data_policy`). Retrieval drops passages whose text reads like an instruction (`RetrievalResult.flagged`).

## Modes, configuration and fallback

| Mode | Model call | Reader sees |
|---|---|---|
| `template` (default) | none; a request is refused (409) and audited | template |
| `shadow` | yes, checked and audited | template; status says the wording was recorded, not shown |
| `prototype` | yes | model wording when every check passed and the result is current, marked AI-generated and review-required; otherwise template |

One map configures every operation:
- `NBA_AI_OPERATION_MODES` (JSON, e.g. `{"case_brief": "shadow"}`) is read first;
- `NBA_AI_OPERATIONS_DISABLED` forces template whatever the mode says;
- the legacy `NBA_AI_CASE_BRIEF_MODE` still applies when the map has no entry;
- unknown values mean template.

A new operation needs no settings field.

Failure categories:
- `provider_disabled`;
- `provider_unavailable` (error or timeout);
- `data_policy`;
- `schema_invalid`;
- `gate_rejected`;
- `access_lost`;
- `context_incomplete`;
- `context_unavailable`;
- `interrupted`;
- `internal`.

## Jobs

Generations reuse the `job` table and worker (`python -m app.worker`, or the embedded thread in a local run). Each takes its own lock `ai:<hash>` (one queued or running generation per operation and subject or reader scope; a second request returns the active one). It never takes the engine lock.

- `NBA_AI_MAX_ACTIVE_JOBS` (2) caps queued and running generations.
- `ai_generate` limits each account to 10 requests per 10 minutes.
- There is no automatic retry.
- A worker that stops marks its job interrupted, and the generation is then marked failed (`interrupted`).
- The job row holds only the generation id and its final status.

## Audit, telemetry and retention

Actions:
- `ai_generation_requested`, `ai_generation_completed`, `ai_generation_fallback`, `ai_generation_failed`;
- `ai_generation_rejected` (forbidden, not found, template mode, rate limited, busy, context unavailable);
- `ai_diagnostics_run` (section and codes only).

Detail (`AuditEvent`):
- operation and version, prompt version, mode;
- subject type and an opaque, keyed **subject reference** (`core.pseudonym`), never the raw id;
- generation and job ids;
- provider, model, model digest;
- context fingerprint and context item ids;
- gate reason codes, failure category, duration, token counts.

Never in the detail: prompts, notes, model text or patient narrative. A rejected request's audit row keeps the subject as its entity id, under the audit log's existing masking (identities masked unless `audit:read:identified`).

AI telemetry is read only through `services/ai_telemetry.py`, which requires `ai:diagnostics`. It counts subjects and never lists them. On a database without the AI tables it reports `available: false` rather than zero.

The `ai_generation` row keeps the composed result only when every check passed; rejected model text is never stored. Rows beyond `NBA_AI_KEEP_GENERATIONS` (5) per subject are deleted; all rows go when the patient is deleted.

## Admin diagnostics

`/ai-diagnostics` (permission `ai:diagnostics`, administrator only). See `docs/Reports/AI_Context_Layer_Admin_Diagnostics.md`.

| Section | What it does |
|---|---|
| A | Sources and the field policy |
| B, C, D | Context builds with every check explained. Fixture mode uses the synthetic fixture world as its own synthetic account of the chosen role. Record preview uses this database with the administrator's own access, every value redacted |
| E | Safety cases |
| F | Multi-subject and aggregate runs |
| G | Health: read-service probes, provider state (configuration only unless probed), jobs, telemetry |
| H | A sanitized report |
| Context evaluation | Readiness of the shared layer for an unseen, policy-approved consumer (below) |

The panel never calls a model and never writes a business record. Every route is rate limited and audited: reads `ai_diagnostics_view` (bucket `ai_diagnostics_read`, 120 / 10 min), runs `ai_diagnostics_run` (`ai_diagnostics`, 30 / 10 min).

### AI context evaluation

`GET /api/ai/diagnostics/evaluation` (last result, from the audit log) and `POST /api/ai/diagnostics/evaluation/run` (bucket `ai_evaluation`, 10 / 10 min; no input). Code: `app/ai/evaluation/` (`rubric.py`, `scenarios.py`, `probes.py`, `trace.py`, `runner.py`); frontend `pages/AiEvaluation.tsx` (tab "Context evaluation").

- About 114 predefined scenarios run the real registry validation, engine, sources, read services, policy, pipeline (`orchestrator.execute`, the same function the worker runs) and checks on two fixture worlds built for the run. The only double is the model (`ScriptedProvider`). Test-only consumer specs (`probes.py`, tag `test_only`) are validated, never registered: `registry.register` refuses them.
- Score: a weighted evidence index over fixed controls in ten dimensions (`rubric.py`, `rubric-1`), not the pass rate. A control earns credit only from scenarios that ran; external controls (OS egress, PostgreSQL, load, meaning-level verification) earn nothing until evidence exists. Hard blockers (unauthorised access, leakage, unchecked output, policy bypass, data corruption, provider fallback, isolation) or an open Critical / High finding make readiness Blocked. States: Not evaluated, Blocked, Insufficient evidence, Below threshold (under 95), Eligible for owner review. Nothing is enabled by any state.
- Per pipeline scenario, an inference trace: contract, principal, source and provenance, category states, policy decisions, prompt template, the payload as sent and as a redacted preview, evidence, output contract, checks and findings, provider and mode, disposition. No model reasoning exists to show.
- Each run is recorded as `ai_evaluation_run` (codes, counts, per-scenario statuses); the next run compares against it and names only the scenario's own finding and tests for a change.

## Databases: traditional and AI

The AI tables live only in `data/nba_demodb_ai.db`, a verified copy of the traditional `data/nba_demo.db` (created 2026-10-16 from backup `backups/nba_demo-20261016T102429Z.db`, integrity ok, equal row counts; then migrated to `b4f8d2a6c9e1`). The traditional file stays at `a7d2e9c4b1f6` and unchanged.

- `NBA_DATABASE_PROFILE=traditional|ai` selects the file (default traditional). Setting it together with `NBA_DATABASE_URL` is refused, and so is an unknown value.
- The web process and the worker refuse to start if the configured SQLite file is missing (`core.db.require_existing`), so no empty replacement is ever created.
- `alembic/env.py` refuses `upgrade`, `downgrade` and `stamp` on the existing traditional file unless `-x allow_traditional=yes`. Reading commands are unaffected.
- Without the AI tables:
  - AI routes answer 503 `ai_unavailable` after the usual 403/404 access check (the refusal is audited);
  - a queued job cannot write;
  - AI telemetry and the diagnostics say "unavailable";
  - every other source and feature works unchanged.
- During tests the application's default engine is rebound to an in-memory database, and both persistent files are hashed before and after the run (`tests/conftest.py`).

## Network isolation

What the application does, and tests:
- `OllamaProvider` accepts only a loopback URL;
- it resolves the host on every request and requires every address to be loopback (`netguard.assert_loopback`);
- it uses no proxy and follows no redirect;
- it refuses model tags containing `cloud`.

`tests/test_ai_network.py` runs the real HTTP path against a stub server on 127.0.0.1 with a socket watcher. Only loopback connections are made, a redirect to another address is refused, and a name resolving off the machine is refused before any connection.

What it does **not** prove: what other processes may do. The Ollama server and its tray app are separate programs: model pulls, update checks and any future cloud features are theirs. Full egress isolation needs an operating-system control. On Windows:
- run only `ollama serve` with `OLLAMA_NO_CLOUD=1` (not the tray app);
- add an outbound firewall rule blocking `ollama.exe` except loopback;
- verify the rule with `Get-NetTCPConnection -OwningProcess (Get-Process ollama).Id` while a generation runs.

The application never creates firewall rules. The exact rules, scope, verification procedure and limitations are in `docs/ollama-network-isolation.md`; `scripts/verify_ollama_egress.ps1` checks them read-only. **Status 2026-10-17: unverified** (no rule present on the development machine).

## Adding a consumer later

1. Choose a subject type (or register a new source with its categories and record policies in `app/ai/sources/` and `app/ai/policy/fields.py`).
2. Write an `OperationSpec` with:
   - id, version and permission;
   - subject type and allowlist (and `max_age_days` if freshness matters);
   - prompt builder (from `ctx.model_items` only), and an output schema built with `output_contract.constrained_schema` so citations can only be this call's evidence ids;
   - strict output model and schema, with every text field declared in `cited_fields` or `text_fields`;
   - operation checks;
   - compose and fallback.
3. Add a permission to the role map.
4. Register the spec.
5. Evaluate it on synthetic data with fixed thresholds before any reader sees its wording.

Registration refuses a permission held by the administrator alone (for example `engine:operate`, `user:manage`, `patient:read:all`) or by no role: there is no admin-only AI operation (owner decision 2026-10-18). A permission the administrator shares with another role is allowed. The Admin context evaluation shows whether the shared layer is ready; it does not evaluate or approve a consumer.

The engine, sources, policy, shared checks, orchestrator, jobs, audit and API need no change. New operations are a product decision; none is approved beyond the Case Brief.

## Operations and routes

Approved operations (registry): `case_brief` (2026-10-16) and, since 2026-10-19, `decision_trace` (X1, care manager, a recommendation), `engagement_brief` (M1, medical rep, an assigned HCP), `queue_brief` (C1, care manager, own care-request queue), `compliance_summary` (R2, compliance, audit activity per action and day) and `cycle_time` (X2, care manager, cycle times computed in code). Each has its own `ai:*` permission (never the administrator's), template mode by default, its own checks, and a deterministic template.

Routes are subject-agnostic: `GET /api/ai/{operation}/current[?subject_id=]` and `POST /api/ai/{operation}/generations` (body `{"subject_id": ...}`); the operation fixes the subject type, so a request cannot choose another. The Case Brief's patient routes remain.

The five newer operations share `app/ai/operations/common.py`: one view (`statements` the model may word, `key_points` and `notes` the server writes, `not_recorded`), the common prompt rules (figures only as items state them, ids only in citation lists, describe never decide), `constrained_schema` citations and `literal_numbers` (`derived_number`). Their own checks:

| Operation | Own checks |
|---|---|
| `decision_trace` | `uncited_decision`, `recommendation_change`, `miscited_code` |
| `engagement_brief` | `commercial_claim`, `contact_window_violation` |
| `queue_brief` | `uncited_counts`, `reordered_queue` (start with the server's first open request), `derived_number` |
| `compliance_summary` | `inferred_misconduct`, `derived_number` |
| `cycle_time` | `invented_cause`, `derived_number` (template states the figures fully; meant to stay in template mode) |

Frontend: one panel (`frontend/src/pages/AiArtifact.tsx`) on the recommendation detail (X1), HCP 360 (M1), care requests (C1, X2) and audit (R2), each shown only with its permission.

## Case Brief

Prompt v10, operation version 2 (`app/ai/operations/case_brief.py`).

- The server writes key points, consent, what is not recorded and the template summary and focus.
- Since version 2 the template also states unanswered outreach from recorded outcomes, citing exactly those records.
- The model writes `summary` and `suggested_focus` with citations, constrained to the item ids of the call.
- Its context is produced by the `patient_360` source exactly as before the layer was generalised: 60 frozen contexts in `tests/data/case_brief_context_snapshot.json` are reproduced item for item (`tests/test_context_compatibility.py`).

Evaluation history:
- `backend/evals/case_brief/` (standalone, frozen copy of the checks);
- `backend/evals/case_brief_app/` (integrated, on the application path).

## Run locally

```powershell
cd backend
$env:NBA_DATABASE_PROFILE = "ai"            # the AI tables live only in the AI database
.\.venv\Scripts\alembic upgrade head
$env:NBA_AI_CASE_BRIEF_MODE = "prototype"   # or NBA_AI_OPERATION_MODES; default is template
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000
```

Ollama must be running locally with the model pulled (`ollama pull qwen3.5:9b-q4_K_M`), with `OLLAMA_NO_CLOUD=1` and, on an Intel integrated GPU, `OLLAMA_IGPU_ENABLE=1`.
