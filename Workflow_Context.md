# Healthcare NBA POC — Current Implementation State

Living context file. Describes what the code does **now**. Update the relevant section in place whenever implementation changes; do not append a changelog. If this file and the code disagree, the code wins: inspect it and fix this file.

Last verified against code: 2026-10-02 (commit `c3f55df` on `main`, pushed to `https://github.com/Jishnu2608/HCP-NBA.git`).

## Project Status

Working end-to-end prototype. All ten planned build phases have code; the full loop (generate data, score, recommend, gate, draft, review, send, capture response, advance clock, retrain, next cycle) runs locally and was checked in the browser.

Current milestone: **prototype complete, pre-hardening**. Not yet done: browser end-to-end tests, any hosted deployment, a real LLM provider in use, branding.

Source documents the build is based on (outside the repo, in the user's Downloads): `Healthcare_NBA_POC_Field_Guide.docx`, `Healthcare_NBA_POC_Blueprint.docx`, `Healthcare_NBA_Report.pdf`. Approved plan: `C:\Users\Jishnudeep\.claude\plans\c-users-jishnudeep-downloads-healthcare-hidden-octopus.md`.

## Architecture

One process, one deployable unit: FastAPI serves the API under `/api` and, when `frontend/dist` exists, the built React app for every other path (`backend/app/main.py`).

| Layer | Technology | Location |
|---|---|---|
| API | FastAPI, Pydantic v2 | `backend/app/api/` |
| ORM / migrations | SQLAlchemy 2, Alembic (single initial migration) | `backend/app/models/`, `backend/alembic/` |
| Database | SQLite file `data/nba_demo.db` (any SQLAlchemy URL via `NBA_DATABASE_URL`) | `backend/app/core/db.py` |
| ML | pandas, scikit-learn, joblib; model files in `data/models/` | `backend/app/scoring/` |
| Auth | PyJWT (HS256), scrypt password hashes | `backend/app/core/security.py`, `backend/app/api/deps.py` |
| Frontend | React 18, TypeScript, Vite 6, Tailwind 4, TanStack Query, React Router 6, Recharts 2, lucide-react | `frontend/src/` |

Tooling on the dev machine: `py -3.13` venv at `backend/.venv` with pip, and npm. No uv, pnpm or Docker installed.

Backend module map (`backend/app/`):

| Module | Responsibility |
|---|---|
| `core/config.py` | Settings (`NBA_` env prefix): database URL, JWT, `demo_mode`, `llm_provider`, seed |
| `core/clock.py` | Demo clock: the single "today", stored in `engine_config` under `demo_as_of_date` |
| `core/engine_config.py` | Tunable policy defaults (risk weights, cut-offs, frequency caps, channel costs) with DB overrides |
| `core/rbac.py` | Row-level filters per role; `can_review`; `shared_patient_ids` |
| `models/tables.py`, `models/enums.py` | All tables and enums |
| `datagen/` | Synthetic generator (`generate.py`), hidden behaviour model (`behavior.py`), hero records (`heroes.py`), content library (`content.py`), data-quality checks (`validate.py`) |
| `features/` | `adherence.py` (PDC, MPR, gap, lateness trend), `engagement.py` (point-in-time engagement state and feature rows), `population.py` (loader of observable data) |
| `scoring/` | `risk.py` (rule-based risk score with drivers), `propensity.py` (train, predict, contributions, model registry) |
| `pipeline.py` | Feature refresh and model training; model specs `PATIENT_ENGAGE`, `PATIENT_FILL`, `HCP_ENGAGE` |
| `nba/` | `gates.py`, `engine.py` (cycle), `rationale.py`, `revalidate.py` (gate re-check on a stored recommendation) |
| `llm/` | `base.py` (contract), `template.py`, `claude.py`, `validator.py`, `service.py` |
| `cycle.py` | Full cycle: refresh features, optional retrain, engine, template drafts |
| `engagement/` | `delivery.py` (channel adapters, send, capture response), `simulator.py` (clock advance) |
| `audit/log.py` | Insert-only audit writer |
| `analytics/queries.py` | Dashboard aggregates |
| `api/` | Routers: `system`, `auth`, `nba`, `people`, `content`, `governance`, `me`, `analytics`; `serializers.py`; `deps.py` |

## Completed

- Foundation: config, DB session, schema, Alembic migration, demo clock.
- Synthetic data generator with validation report (22 checks) and six hero patients, three hero HCPs.
- Adherence and engagement features; rule-based risk score; three propensity models with holdout metrics, baseline and boosted-tree challenger.
- NBA engine with hard gates, ranking, rationale, kept alternatives, withheld-option reporting, audit.
- Drafting layer: template provider (default), Claude provider (written, never run), output validator, fallback.
- JWT auth, demo persona switcher, six roles with server-side row-level scoping.
- Review workflow: approve, edit draft, reject, redraft, send, log outcome; gates re-checked at approval and at send.
- Portals: patient (medications, messages, consent) and HCP (inbox, consented patients, profile).
- Closed loop: delivery adapters, response capture, outcome simulator, clock advance with retrain, demo reset, bulk-send shortcut.
- Analytics dashboard, including a like-for-like engine versus baseline comparison.
- Frontend for all six roles.
- Docs: `README.md`, `docs/demo-script.md`. Scripts: `scripts/setup.ps1`, `scripts/run.ps1`. Preview config: `.claude/launch.json` (`nba-app`, port 8000).
- Initial commit pushed to GitHub `main`.

## In Progress

Nothing partially implemented.

## Planned

Approved in the plan but not started:

- Playwright end-to-end tests running the six demo scenarios.
- Hosted deployment (Databricks Apps or Vercel plus container API) with Postgres.
- Real LLM provider selection and a live run (Claude, Gemini, or an org cloud model). No Gemini provider exists.
- OIDC/SSO behind `get_current_user`.
- Product name and branding for a client-facing version.

Written but never executed: `Dockerfile`, `docker-compose.yml`, `scripts/setup.ps1`, the Postgres path (`postgres` extra in `backend/pyproject.toml`).

## Authentication & RBAC

Flow: `POST /api/auth/login` (OAuth2 password form) or `POST /api/auth/demo-login` (passwordless, only while `NBA_DEMO_MODE` is true) returns a bearer JWT. `get_current_user` in `api/deps.py` resolves it to a `user` row; this is the seam for SSO. `require_roles(...)` guards endpoints. The frontend keeps the token in `sessionStorage` (one persona per browser tab).

Seeded users (password "<NBA_DEMO_PASSWORD>"): `admin`, `compliance1`, `compliance2`, `rep01`..`rep20`, `cm01`..`cm10`, `hcp0001`..`hcp0003`, `pat00001`..`pat00006`.

| Role | Data scope | Actions |
|---|---|---|
| `admin` | Everything | Run cycle, advance clock, bulk send, reset, edit settings; review any recommendation |
| `compliance` | Content library; recommendations that are blocked or have a withheld option, with identities hidden; audit log; analytics; models | Approve or reject content. Cannot review recommendations. Only this role can review content (admin cannot). |
| `medical_rep` | HCPs in `rep_hcp`; their recommendations; usable HCP-audience content | Approve, edit, reject, send, log visit outcome |
| `care_manager` | Patients in `care_manager_patient`; their recommendations; usable patient-audience content | Approve, edit, reject, send, log call outcome |
| `hcp` | Own profile (no segment, value score or volume); own inbox; adherence summary of own prescribed therapies for attributed patients with provider-sharing consent in effect | Respond to inbox items |
| `patient` | Own profile and therapies (no risk scores); own consents; own inbox | Change consent, respond, confirm refill |

Rules: scoping is applied in SQL through `rbac.hcp_filter`, `rbac.patient_filter`, `rbac.nba_filter`. Out-of-scope records return 404, wrong-role endpoints return 403. `/api/me/*` endpoints never take an id; the record is bound to the signed-in user. `/api/health` and `/api/meta` (row counts only) are public.

## Data & Database

22 tables: `hcp`, `patient`, `patient_hcp`, `user`, `rep_hcp`, `care_manager_patient`, `patient_therapy`, `medication_fill`, `adherence_snapshot`, `consent`, `content`, `content_review`, `engine_cycle`, `nba`, `nba_candidate`, `message_draft`, `interaction`, `feature_snapshot`, `audit_log`, `model_version`, `engine_config`, `sim_latent`.

Key points:

- `nba` and `interaction` reference targets polymorphically (`target_type` `HCP`/`PATIENT` + `target_id`).
- `nba` status lifecycle: `ready_for_review` or `blocked` → `approved` / `rejected` → `sent` → `responded`; unreviewed ones become `expired` when the next cycle runs. `nba.withheld` (JSON) holds a better option a gate held back.
- `consent` is per patient, purpose (`outreach`, `provider_sharing`) and channel, with effective dates; history is kept by closing a record and opening a new one.
- `content` carries `mlr_status` (`approved`, `pending`, `rejected`) plus effective and expiry dates; "usable" means approved and inside its window.
- `interaction.outcome` includes `pending` (sent by the engine, response unknown). `interaction.source` is `history` (seeded baseline) or `nba` (engine).
- Nullable JSON columns use `JSON(none_as_null=True)` so `IS NULL` filters work.
- `sim_latent` holds hidden ground-truth traits and each therapy's unprompted next fill date.
- `audit_log` is insert-only.

Synthetic data (`python -m app.datagen`, default seed 20260101, as-of date 2026-09-30): 3,000 patients, 300 HCPs, 20 reps, 10 care managers, about 30,000 fills, about 11,000 historical interactions, 43 content items. Patients have a hidden archetype (steady, forgetful, drifter, cost_barrier, never_starter), channel responsiveness and action effects; HCPs have topic interest, channel responsiveness and fatigue. History and the post-send simulator draw from the same traits. Synthetic NPIs start with 9 so they cannot match a real provider.

Hero records (`datagen/heroes.py`): `PAT_00001` forgetful with widening gap; `PAT_00002` prefers SMS without SMS consent; `PAT_00003` adherent; `PAT_00004` opted out of everything; `PAT_00005` cost barrier; `PAT_00006` prescription never filled; `HCP_0001` cardiologist who answers email; `HCP_0002` endocrinologist whose best content is pending MLR; `HCP_0003` low-value, fatigued. All heroes are assigned to `rep01` / `cm01`.

Schema changes so far were folded into one regenerated initial migration because nothing had been deployed. After the first deployment, add new migrations instead.

## NBA Engine

Cycle (`app/cycle.py` → `nba/engine.py`): expire unreviewed recommendations from earlier cycles; for each target build candidates, run gates, score, pick one, write rationale, candidates and audit; then template drafts for every ready recommendation.

- **Patient candidates:** only for patients with a therapy needing action (risk not low, or in a gap, or due within `nba_due_soon_days` with a lateness history). Focus is the highest-risk therapy. Candidates are patient content × allowed channels; no refill reminder for a never-filled prescription.
- **HCP candidates:** HCP content × channels, excluding content the HCP already engaged with and content for another specialty.
- **Gates (`nba/gates.py`):** content audience and channel, MLR status and validity window, patient outreach consent per channel, frequency cap and minimum gap. Frequency is a target-level limit: if it applies, the target is deferred, not blocked.
- **Scores:** patient = `100 × (0.8 × p_fill + 0.2 × p_engage) − channel cost`; HCP = `100 × p_engage − channel cost`, halved for content sent in the last 90 days, with a minimum score to recommend anything. Queue priority = risk score × p_fill (patient) or value score × p_engage (HCP). Without trained models the engine falls back to smoothed historical rates.
- **Outcomes per target:** ready, blocked (nothing eligible), deferred (frequency), or none.
- **Withheld:** recorded when the top-scoring option was gated and beat the best eligible one by `nba_withheld_margin`, and when a patient's stated preferred channel lacks consent.
- **Risk score (`scoring/risk.py`):** points for PDC shortfall, gap days, widening gaps, unresponsiveness, never filled; segments high ≥ 40, medium ≥ 15.
- **Adherence (`features/adherence.py`):** PDC over a rolling 180-day window with carry-forward for early refills; gap days = days since supply ran out.
- **Models (`scoring/propensity.py`):** logistic regression champion (exact per-feature contributions), gradient-boosting challenger reported only, 60-day temporal holdout, registry in `model_version`.
- **Drafting (`llm/`):** `draft_cycle` always uses the template provider; `draft_for_nba` uses the configured provider on demand. `validator.py` rejects malformed output, unresolved placeholders, links, other content ids, SMS over 320 characters or with a subject, missing subject for email/portal, and banned patient wording. Human-edited drafts are preserved on redraft and go through the same validator.
- **Simulator (`engagement/simulator.py`):** on clock advance, resolves pending interactions first (a persuaded fill replaces the natural one, the same rule as seeded history), then natural fills, then runs a cycle with retrain.

## UI

`frontend/src/App.tsx` defines navigation and routes per role. Shared components in `ui.tsx`; API wrapper in `api.ts`; auth context in `auth.tsx`.

| Page | File | Roles |
|---|---|---|
| Login with persona picker | `pages/Login.tsx` | all |
| Work queue (titled per role) | `pages/Queue.tsx` | admin, compliance, medical_rep, care_manager |
| Recommendation detail: reasons, options considered, content, draft editor, decision, audit | `pages/NbaDetail.tsx` | same four |
| Patient list and Patient 360 with coverage timeline | `pages/Patients.tsx` | admin, care_manager |
| HCP list and HCP 360 | `pages/Hcps.tsx` | admin, medical_rep |
| Content library and MLR review | `pages/Content.tsx` | admin, compliance, medical_rep, care_manager |
| Audit log | `pages/Audit.tsx` | admin, compliance |
| Dashboard (Recharts) | `pages/Dashboard.tsx` | admin, compliance |
| Engine: run cycle, bulk send, advance clock, reset, settings, cycles | `pages/Admin.tsx` | admin |
| Under the hood: loop, design rules, model metrics, row counts | `pages/UnderTheHood.tsx` | admin, compliance |
| Portal: medications, inbox, consents, my patients, profile | `pages/Portal.tsx` | patient, hcp |

Chart colours are CSS roles in `index.css` (blue, orange, aqua from a validated palette); baseline is neutral grey. Engine bars on the dashboard appear only with at least 20 resolved sends.

## APIs / Services

| Area | Routes |
|---|---|
| System | `GET /api/health`, `GET /api/meta`, `GET /api/clock` |
| Auth | `POST /api/auth/login`, `POST /api/auth/demo-login`, `GET /api/auth/personas`, `GET /api/auth/me` |
| Recommendations | `GET /api/nba`, `GET /api/nba/{id}`, `POST .../approve`, `POST .../reject`, `PATCH .../drafts/{draft_id}`, `POST .../redraft`, `POST .../send`, `POST .../outcome` |
| Profiles | `GET /api/patients`, `GET /api/patients/{id}`, `GET /api/hcps`, `GET /api/hcps/{id}` |
| Content | `GET /api/content`, `POST /api/content/{id}/review` |
| Portal | `GET /api/me/profile`, `GET /api/me/consents`, `PUT /api/me/consents/outreach/{channel}`, `PUT /api/me/consents/provider-sharing`, `GET /api/me/patients`, `GET /api/me/inbox`, `POST /api/me/inbox/{id}/respond` |
| Governance | `GET /api/audit`, `GET /api/admin/config`, `PUT /api/admin/config/{key}`, `GET /api/admin/models`, `GET /api/admin/cycles` |
| Engine operations | `POST /api/admin/cycle`, `POST /api/admin/bulk-send`, `POST /api/admin/advance`, `POST /api/admin/reset` |
| Analytics | `GET /api/analytics/overview` |

Interactive reference at `/api/docs`. Responses are plain dicts built in `api/serializers.py` (no response models).

Command-line entry points (from `backend`): `python -m app.datagen`, `python -m app.pipeline`, `python -m app.nba`, `python -m app.cycle [--retrain]`.

## Testing

195 pytest tests in `backend/tests/`, all passing; run with `.venv\Scripts\python -m pytest`. Lint: `ruff check .` and `ruff format`.

| File | Covers |
|---|---|
| `test_foundation.py` | Schema table set, health, meta, clock |
| `test_datagen.py` | Validation checks, determinism, hero records, users per role |
| `test_features.py` | PDC/MPR/gap arithmetic on hand-computed cases, risk score, hero segments, no look-ahead, models beat baseline, contributions sum to the prediction |
| `test_nba.py` | Each gate, selection logic, invariants on the real seeded dataset, the scenario behaviours, MLR approval unlocking content, engine without models, static check that engine code never references `sim_latent` |
| `test_llm.py` | Template output validity per channel, validator rejections, fallback, edited drafts preserved |
| `test_api.py` | Login, token rejection, role × endpoint matrix, row scoping, compliance view, review workflow, consent re-check at approval, MLR review, config |
| `test_loop.py` | Send, pending semantics, patient and HCP inbox, send blocked after consent withdrawal, outcome logging, bulk send, clock advance, analytics, reset |

`tests/conftest.py` redirects model files to a temp directory. `test_nba.py` scenario tests use the default seed and scale, so generator changes can break them. No frontend tests. Frontend checks: `npm run typecheck`, `npm run build`.

## Known Issues / Limitations

- Models are trained on synthetic behaviour; holdout AUC is about 0.63 to 0.69. Observed uplift in simulation: HCP response about 41% versus 26% baseline in the first week; patient fill about 15.7% versus 13.3%.
- Delivery adapters only record the hand-off; no message leaves the system. "I have refilled" in the patient portal stands in for a pharmacy fill.
- Claude provider untested (no key). It enables server-side refusal fallback by default.
- No browser end-to-end tests; Docker, Postgres and `setup.ps1` never run.
- `demo_mode` is on by default: passwordless persona login, shared password "<NBA_DEMO_PASSWORD>", placeholder JWT secret. Must change before any hosted use.
- Interaction timestamps combine the demo date with wall-clock time of day.
- `POST /api/admin/reset` and `advance` are synchronous and take 15 to 30 seconds at full scale.
- Patients with several therapies get one recommendation per cycle, for the highest-risk therapy.
- HCPs have no consent or opt-out model.
- Frontend bundle is one 700 kB chunk (no code splitting).
- The running server does not auto-reload; restart it after backend changes and rebuild the frontend (`npm run build`) after UI changes.

## Important Decisions

1. **Gates are deterministic code**, evaluated at generation, approval and send. No model or prompt can override them.
2. **The LLM only words** an already-eligible action. Bulk drafting always uses templates so a cycle never depends on or pays for model calls.
3. **Logistic regression is the champion model** because its contributions are exact; the challenger is for comparison only.
4. **Hidden traits live only in `sim_latent`** and are read only by `datagen` and `engagement/simulator.py`. A test enforces that `nba`, `features`, `scoring`, `audit` and `pipeline.py` do not reference it.
5. **Point-in-time features:** training rows and live scoring share the same code in `features/engagement.py`.
6. **Out-of-scope returns 404**, so ids cannot be probed; compliance sees gate outcomes without identities.
7. **Only compliance approves content** (separation of duties); admin cannot.
8. **Vite SPA served by FastAPI** rather than Next.js, to keep one deployable unit for a laptop, a container or Databricks Apps.
9. **Patient adherence is the lead story**; HCP engagement stays in scope as the second audience.
10. **Fair comparison on the dashboard:** engine versus baseline is shown only for touches made while the patient was already in a gap, because the engine targets harder cases.
11. **One recommendation per target per cycle**; older unreviewed ones are superseded.
12. Simulator and seeded history must keep the same crediting rule for prompted fills, or engine-versus-baseline numbers become misleading.

## Next Recommended Task

Add Playwright end-to-end tests that drive the six scenarios in `docs/demo-script.md` against a freshly seeded database. It is the main unfinished item from the approved plan and protects the demo against regressions before any further feature work. After that: choose the LLM provider and run it live, then the first hosted deployment.
