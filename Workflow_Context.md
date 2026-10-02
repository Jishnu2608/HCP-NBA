# Healthcare NBA POC — Current Implementation State

Living context file. Describes what the code does **now**. Update the relevant section in place whenever implementation changes; do not append a changelog. If this file and the code disagree, the code wins: inspect it and fix this file.

One of three context files: `Master_Build.md` is the specification (what the system is supposed to be, and the rules that must be preserved); this file is the implementation state; `TODO.md` is the backlog. Read all three before major work. They are deliberately not duplicates of each other.

Last verified against code: 2026-10-02, at the commit "security: remove hardcoded credentials" on `main` (remote `https://github.com/Jishnu2608/HCP-NBA.git`; history rewritten that day).

## Project Status

Working end-to-end prototype. All ten planned build phases have code; the full loop (generate data, score, recommend, gate, draft, review, send, capture response, advance clock, retrain, next cycle) runs locally and was checked in the browser.

Current milestone: **prototype complete with real account flow, pre-hardening**. The demo persona picker has been replaced by landing page, email + password login, sign-up with email one-time code, a fixed administrator, permission-based RBAC and admin user/assignment management. Not yet done: browser end-to-end tests, any hosted deployment, a real LLM provider in use, a live SMTP send, branding.

Source documents the build is based on (outside the repo, in the user's Downloads): `Healthcare_NBA_POC_Field_Guide.docx`, `Healthcare_NBA_POC_Blueprint.docx`, `Healthcare_NBA_Report.pdf`. The plan file `C:\Users\Jishnudeep\.claude\plans\c-users-jishnudeep-downloads-healthcare-hidden-octopus.md` now holds the approved auth/RBAC refactor plan (it replaced the original build plan).

## Architecture

One process, one deployable unit: FastAPI serves the API under `/api` and, when `frontend/dist` exists, the built React app for every other path (`backend/app/main.py`).

| Layer | Technology | Location |
|---|---|---|
| API | FastAPI, Pydantic v2 | `backend/app/api/` |
| ORM / migrations | SQLAlchemy 2, Alembic (`50d7c5d998ec` initial, `a1f4c2e97b30` auth accounts and OTP) | `backend/app/models/`, `backend/alembic/` |
| Database | SQLite file `data/nba_demo.db` (any SQLAlchemy URL via `NBA_DATABASE_URL`) | `backend/app/core/db.py` |
| ML | pandas, scikit-learn, joblib; model files in `data/models/` | `backend/app/scoring/` |
| Auth | PyJWT (HS256) session tokens, scrypt password hashes, stdlib `smtplib` for verification email | `backend/app/auth/`, `backend/app/core/security.py`, `backend/app/api/deps.py` |
| Frontend | React 18, TypeScript, Vite 6, Tailwind 4, TanStack Query, React Router 6, Recharts 2, lucide-react | `frontend/src/` |

Tooling on the dev machine: `py -3.13` venv at `backend/.venv` with pip, and npm. No uv, pnpm or Docker installed.

Backend module map (`backend/app/`):

| Module | Responsibility |
|---|---|
| `core/config.py` | Settings (`NBA_` env prefix). Secrets (`jwt_secret`, `admin_password`, `demo_password`) have **no default** and are read with `settings.secret(name)`, which raises `MissingSecret` naming the variable. Also: database URL, `admin_email`, `seed_demo_accounts`, `demo_mode`, SMTP, OTP timings, sign-up panel sizes, `llm_provider`, seed |
| `bootstrap.py` | `python -m app.bootstrap`: appends any missing required secret to the git-ignored `.env` with a random value; never changes existing ones |
| `core/permissions.py` | `Permission` enum, `ROLE_PERMISSIONS`, `SIGNUP_ROLES`, `ROLE_HOME`, `can`, `can_any`. The only place roles map to abilities. |
| `core/security.py` | scrypt `hash_password` / `verify_password` only |
| `auth/` | `repository.py` (`UserRepository`, `SqlUserRepository`), `otp.py` (`OTPService`, `OtpSender`, `EmailOtpSender`, `LocalDemoOtpSender`), `sessions.py` (`SessionService`), `service.py` (signup, verify, resend, login, logout, `ensure_system_admin`, `account_out`), `provisioning.py` (`AssignmentService`), `errors.py` (`AuthError`) |
| `core/clock.py` | Demo clock: the single "today", stored in `engine_config` under `demo_as_of_date` |
| `core/engine_config.py` | Tunable policy defaults (risk weights, cut-offs, frequency caps, channel costs) with DB overrides |
| `core/rbac.py` | Data scope: SQL filters driven by permissions + assignment tables (`hcp_filter`, `patient_filter`, `nba_filter`), `can_review`, `can_see_target_profile`, `shared_patient_ids` |
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
| `api/` | Routers: `system`, `auth`, `users`, `nba`, `people`, `content`, `governance`, `me`, `analytics`; `serializers.py`; `deps.py` (`get_current_user`, `require_permission`) |

## Completed

- Foundation: config, DB session, schema, Alembic migration, demo clock.
- Synthetic data generator with validation report (22 checks) and six hero patients, three hero HCPs.
- Adherence and engagement features; rule-based risk score; three propensity models with holdout metrics, baseline and boosted-tree challenger.
- NBA engine with hard gates, ranking, rationale, kept alternatives, withheld-option reporting, audit.
- Drafting layer: template provider (default), Claude provider (written, never run), output validator, fallback.
- Account flow: landing page, email + password login, sign-up with role cards, email one-time code (SMTP or development fallback), fixed administrator, server-side sign-out.
- Permission-based RBAC (`require_permission`) on every protected endpoint; six roles; row-level scoping from assignments.
- Automatic assignment on verification; admin Users and assignments page (view, change assignments, disable / re-enable).
- Review workflow: approve, edit draft, reject, redraft, send, log outcome; gates re-checked at approval and at send.
- Portals: patient (medications, messages, consent) and HCP (inbox, consented patients, profile).
- Closed loop: delivery adapters, response capture, outcome simulator, clock advance with retrain, demo reset, bulk-send shortcut.
- Analytics dashboard, including a like-for-like engine versus baseline comparison.
- Frontend for all six roles.
- Docs: `README.md`, `docs/demo-script.md`, `docs/architecture-auth-rbac.md`, `.env.example`. Context files: `Master_Build.md`, `Workflow_Context.md`, `TODO.md`, with the workflow rule in `CLAUDE.md`. Scripts: `scripts/setup.ps1`, `scripts/run.ps1`. Preview config: `.claude/launch.json` (`nba-app`, port 8000).
- All work to date, including the auth/RBAC refactor, is committed and pushed to GitHub `main`.

## In Progress

Nothing partially implemented.

## Planned

The backlog lives in `TODO.md` (Upcoming, Enhancements, Technical Debt, Testing, Production Readiness). Not repeated here.

Written but never executed, so not counted as implemented: `Dockerfile`, `docker-compose.yml`, `scripts/setup.ps1`, the PostgreSQL path (`postgres` extra in `backend/pyproject.toml`), the Claude drafting provider, live SMTP delivery.

## Authentication & RBAC

Chain: **account → role → permission set → data scope → UI**. Full description in `docs/architecture-auth-rbac.md`.

**Flow.** `POST /api/auth/signup` creates a `pending`, unverified account and issues a 6-digit code; it returns a 30-minute *verification token* (not a session). `POST /api/auth/verify-otp` activates the account, auto-assigns data and returns a session. `POST /api/auth/login` takes email + password (form field `username` carries the email). `POST /api/auth/logout` bumps `user.token_version`, killing the token server-side. `get_current_user` (`api/deps.py`) calls `sessions.resolve`, which loads the account and rejects unknown, unverified, non-active or version-mismatched ones. The role is never read from the token.

**One-time code** (`auth/otp.py`): HMAC hash only in `otp_challenge`; 10-minute expiry; 5 attempts then deleted; 30-second resend cool-down; deleted on success. Delivery is email only: `EmailOtpSender` (SMTP) when `NBA_SMTP_HOST` is set, otherwise `LocalDemoOtpSender` while `demo_mode` is on (code returned as `dev_otp` and logged; the UI states no email was sent). SMTP failure falls back to the development sender only in demo mode.

**Accounts.** Fixed administrator `admin@admin.com` (email from `admin_email`, password from `NBA_ADMIN_PASSWORD` in `.env`; `source=system`; ensured by the generator and at start-up; never creatable by sign-up). Seeded demo accounts `<username>@nba.demo` sharing the password in `NBA_DEMO_PASSWORD` (`source=seed`): `compliance1`, `compliance2`, `rep01`..`rep20`, `cm01`..`cm10`, `hcp0001`..`hcp0003`, `pat00001`..`pat00006`. `NBA_SEED_DEMO_ACCOUNTS=false` seeds only the administrator (the user may ask for this later). Registered accounts have `source=signup` and `username = email`.

**Permissions** (`core/permissions.py`):

| Role | Permissions |
|---|---|
| `admin` | `patient:read:all`, `hcp:read:all`, `nba:read:all`, `nba:review:patient`, `nba:review:hcp`, `content:read:all`, `audit:read`, `analytics:read`, `models:read`, `engine:operate`, `config:manage`, `user:manage` (no `content:approve`) |
| `compliance` | `content:read:all`, `content:approve`, `nba:read:gated`, `audit:read`, `analytics:read`, `models:read` |
| `medical_rep` | `hcp:read:assigned`, `nba:read:hcp_assigned`, `nba:review:hcp`, `content:read:approved_hcp` |
| `care_manager` | `patient:read:assigned`, `nba:read:patient_assigned`, `nba:review:patient`, `content:read:approved_patient` |
| `hcp` | `self:profile:read`, `self:inbox`, `self:patients:read` |
| `patient` | `self:profile:read`, `self:inbox`, `self:consent:manage` |

**Data scope** (`core/rbac.py`): `*:read:all` = every row; `*:read:assigned` = rows in `rep_hcp` / `care_manager_patient`; `nba:read:gated` = blocked or withheld recommendations with identity hidden; `self:*` = the record in `user.patient_id` / `user.hcp_id` (`/api/me/*` takes no id). Out-of-scope returns 404; missing permission 403; no linked record 409 `no_assignment`.

**Assignments** (`auth/provisioning.py`, separate from RBAC, never touch `user.role`): on verification a patient or HCP account claims one unused synthetic record (preferring one with a ready recommendation) and the record takes the account's name; a care manager gets `signup_panel_patients` (30) patients spread over risk segments; a rep gets `signup_panel_hcps` (15) HCPs spread over value. Rows are added, seeded rows are never changed. Admin edits via `PUT /api/admin/users/{id}/assignments`; the field sent must match the account's assignment kind. `generate()` snapshots `source=signup` accounts and their assignments before wiping and restores them after, so a demo reset keeps registered users; `POST /api/admin/reset` returns a fresh session for the caller.

**No endpoint changes a role.** Admin may change status (`active` / `disabled`; not own account, not the system admin; cannot activate an unverified account). Disabling revokes sessions.

Error shape for auth and permission failures: `detail = {code, message, ...}`. Codes: `invalid_credentials`, `verification_required`, `account_disabled`, `email_exists`, `invalid_role`, `invalid_email`, `invalid_name`, `password_mismatch`, `weak_password`, `otp_incorrect`, `otp_expired`, `otp_locked`, `otp_missing`, `otp_resend_wait`, `otp_delivery_failed`, `otp_delivery_unavailable`, `already_verified`, `verification_expired`, `not_authenticated`, `forbidden`, `no_assignment`, `invalid_assignment`, `unknown_record`, `record_in_use`, `no_assignments_for_role`, `cannot_change_self`, `system_account`, `not_verified`.

## Data & Database

23 tables: `hcp`, `patient`, `patient_hcp`, `user`, `otp_challenge`, `rep_hcp`, `care_manager_patient`, `patient_therapy`, `medication_fill`, `adherence_snapshot`, `consent`, `content`, `content_review`, `engine_cycle`, `nba`, `nba_candidate`, `message_draft`, `interaction`, `feature_snapshot`, `audit_log`, `model_version`, `engine_config`, `sim_latent`.

Key points:

- `nba` and `interaction` reference targets polymorphically (`target_type` `HCP`/`PATIENT` + `target_id`).
- `nba` status lifecycle: `ready_for_review` or `blocked` → `approved` / `rejected` → `sent` → `responded`; unreviewed ones become `expired` when the next cycle runs. `nba.withheld` (JSON) holds a better option a gate held back.
- `consent` is per patient, purpose (`outreach`, `provider_sharing`) and channel, with effective dates; history is kept by closing a record and opening a new one.
- `content` carries `mlr_status` (`approved`, `pending`, `rejected`) plus effective and expiry dates; "usable" means approved and inside its window.
- `interaction.outcome` includes `pending` (sent by the engine, response unknown). `interaction.source` is `history` (seeded baseline) or `nba` (engine).
- Nullable JSON columns use `JSON(none_as_null=True)` so `IS NULL` filters work.
- `user` columns: `username` (audit actor handle), `email` (unique, lower-cased), `display_name`, `password_hash`, `role`, `hcp_id`, `patient_id`, `verified`, `status` (`pending` / `active` / `disabled`), `source` (`system` / `seed` / `signup`), `token_version`, `created_at`, `updated_at`, `last_login_at`.
- `otp_challenge`: one active code per account, hash only, deleted when used, expired or locked.
- `sim_latent` holds hidden ground-truth traits and each therapy's unprompted next fill date.
- `audit_log` is insert-only.

Synthetic data (`python -m app.datagen`, default seed 20260101, as-of date 2026-09-30): 3,000 patients, 300 HCPs, 20 reps, 10 care managers, about 30,000 fills, about 11,000 historical interactions, 43 content items. Patients have a hidden archetype (steady, forgetful, drifter, cost_barrier, never_starter), channel responsiveness and action effects; HCPs have topic interest, channel responsiveness and fatigue. History and the post-send simulator draw from the same traits. Synthetic NPIs start with 9 so they cannot match a real provider.

Hero records (`datagen/heroes.py`): `PAT_00001` forgetful with widening gap; `PAT_00002` prefers SMS without SMS consent; `PAT_00003` adherent; `PAT_00004` opted out of everything; `PAT_00005` cost barrier; `PAT_00006` prescription never filled; `HCP_0001` cardiologist who answers email; `HCP_0002` endocrinologist whose best content is pending MLR; `HCP_0003` low-value, fatigued. All heroes are assigned to `rep01` / `cm01`. A registered care manager's panel can include hero patients too (assignments are many-to-many).

Schema changes now go in new migrations (the repository is pushed). `alembic/env.py` uses a plain engine, because SQLite batch table rebuilds fail while the app's per-connection foreign-key enforcement is on. Migration `a1f4c2e97b30` back-fills existing users as verified, active seed accounts with `<username>@nba.demo`.

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

`frontend/src/routes.tsx` is the single route table: each route lists the permissions that allow it; the menu and the route guards are both derived from it using the permission list from `GET /api/auth/me`. `App.tsx` holds the shell and guards. `session.ts` is the only module touching browser storage (`sessionStorage`: survives refresh, one session per tab). `auth.tsx` is the auth context (`login`, `signup`, `verify`, `resend`, `logout`, `can`, `adopt`). `permissions.ts` mirrors the server's permission names. `api.ts` is the fetch wrapper (`ApiError.code`).

Public pages (signed out): `/` `pages/Landing.tsx`, `/login` `pages/Login.tsx`, `/signup` `pages/Signup.tsx` (role cards from `GET /api/auth/roles`), `/signup/verify` `pages/VerifyOtp.tsx`. Shared frame: `pages/AuthLayout.tsx`.

| Route | File | Needs any of |
|---|---|---|
| `/dashboard` | `pages/Dashboard.tsx` | `analytics:read` |
| `/queue`, `/nba/:id` | `pages/Queue.tsx`, `pages/NbaDetail.tsx` | any `nba:read:*` |
| `/patients`, `/patients/:id` | `pages/Patients.tsx` | `patient:read:*` |
| `/hcps`, `/hcps/:id` | `pages/Hcps.tsx` | `hcp:read:*` |
| `/content` | `pages/Content.tsx` | `content:read:*` |
| `/audit` | `pages/Audit.tsx` | `audit:read` |
| `/users` | `pages/Users.tsx` | `user:manage` |
| `/admin` | `pages/Admin.tsx` | `engine:operate` |
| `/under-the-hood` | `pages/UnderTheHood.tsx` | `models:read` |
| `/medications`, `/consent` | `pages/Portal.tsx` | `self:consent:manage` |
| `/inbox` | `pages/Portal.tsx` | `self:inbox` |
| `/my-patients`, `/profile` | `pages/Portal.tsx` | `self:patients:read` |

Behaviour: signed out → any app address redirects to `/login`; signed in → `/`, `/login`, `/signup` redirect to the account's `home`; an address the account may not open shows `pages/AccessDenied.tsx`; after sign-in the user returns to a remembered page only if `mayOpen` allows it; sign-out ends on `/`. The shell footer shows "Signed in as", name, role, email and Sign out. No role-name comparisons remain in pages; they use `can(...)`.

Role homes (from the API): admin `/dashboard`, care_manager and medical_rep `/queue`, compliance `/content`, patient `/medications`, hcp `/inbox`.

Chart colours are CSS roles in `index.css` (blue, orange, aqua from a validated palette); baseline is neutral grey. Engine bars on the dashboard appear only with at least 20 resolved sends.

## APIs / Services

| Area | Routes |
|---|---|
| System | `GET /api/health`, `GET /api/meta`, `GET /api/clock` |
| Auth | `GET /api/auth/roles`, `POST /api/auth/signup`, `POST /api/auth/verify-otp`, `POST /api/auth/resend-otp`, `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` |
| Users (admin) | `GET /api/admin/users`, `GET /api/admin/users/{id}`, `PATCH /api/admin/users/{id}/status`, `PUT /api/admin/users/{id}/assignments` |
| Recommendations | `GET /api/nba`, `GET /api/nba/{id}`, `POST .../approve`, `POST .../reject`, `PATCH .../drafts/{draft_id}`, `POST .../redraft`, `POST .../send`, `POST .../outcome` |
| Profiles | `GET /api/patients`, `GET /api/patients/{id}`, `GET /api/hcps`, `GET /api/hcps/{id}` |
| Content | `GET /api/content`, `POST /api/content/{id}/review` |
| Portal | `GET /api/me/profile`, `GET /api/me/consents`, `PUT /api/me/consents/outreach/{channel}`, `PUT /api/me/consents/provider-sharing`, `GET /api/me/patients`, `GET /api/me/inbox`, `POST /api/me/inbox/{id}/respond` |
| Governance | `GET /api/audit`, `GET /api/admin/config`, `PUT /api/admin/config/{key}`, `GET /api/admin/models`, `GET /api/admin/cycles` |
| Engine operations | `POST /api/admin/cycle`, `POST /api/admin/bulk-send`, `POST /api/admin/advance`, `POST /api/admin/reset` |
| Analytics | `GET /api/analytics/overview` |

Public without a session: `/api/health`, `/api/meta`, `/api/auth/roles`, `signup`, `verify-otp`, `resend-otp`, `login`. Everything else returns 401. Interactive reference at `/api/docs`. Responses are plain dicts built in `api/serializers.py` (no response models).

Command-line entry points (from `backend`): `python -m app.datagen`, `python -m app.pipeline`, `python -m app.nba`, `python -m app.cycle [--retrain]`.

## Testing

264 pytest tests in `backend/tests/`, all passing; run with `.venv\Scripts\python -m pytest`. Lint: `ruff check .` and `ruff format`.

| File | Covers |
|---|---|
| `test_foundation.py` | Schema table set, health, meta, clock |
| `test_datagen.py` | Validation checks, determinism, hero records, users per role |
| `test_features.py` | PDC/MPR/gap arithmetic on hand-computed cases, risk score, hero segments, no look-ahead, models beat baseline, contributions sum to the prediction |
| `test_nba.py` | Each gate, selection logic, invariants on the real seeded dataset, the scenario behaviours, MLR approval unlocking content, engine without models, static check that engine code never references `sim_latent` |
| `test_llm.py` | Template output validity per channel, validator rejections, fallback, edited drafts preserved |
| `test_api.py` | Login, token rejection, persona endpoints gone, role × endpoint matrix, row scoping, compliance view, review workflow, consent re-check at approval, MLR review (admin refused), config |
| `test_auth.py` | Sign-up validation, duplicate email, code success / wrong / locked / expired / resend / single use, email delivery with a fake sender and fallback, admin login, full flow, forged tokens, auto-assignment per role, admin user management, assignment edits leave role and permissions unchanged, disable, reset preserves accounts |
| `test_secrets.py` | Secrets have no default, missing secret raises, bootstrap creates only what is missing, `.env` is git-ignored, no credential literal in tracked files |
| `test_permissions.py` | Role → permission sets, admin lacks content approval, every `/api` route outside a public allow-list refuses anonymous callers, no role-name authorization in routers or `rbac.py` |
| `test_loop.py` | Send, pending semantics, patient and HCP inbox, send blocked after consent withdrawal, outcome logging, bulk send, clock advance, analytics, reset |

`tests/conftest.py` sets random test secrets in the environment before importing the app, forces mail delivery off (so a developer's `.env` SMTP settings can never send from a test), redirects model files to a temp directory and provides `sign_in` / `auth` (real email + password login, token cached per client). `test_nba.py` scenario tests use the default seed and scale, so generator changes can break them. No frontend tests. Frontend checks: `npm run typecheck`, `npm run build`.

## Known Issues / Limitations

- Models are trained on synthetic behaviour; holdout AUC is about 0.63 to 0.69. Observed uplift in simulation: HCP response about 41% versus 26% baseline in the first week; patient fill about 15.7% versus 13.3%.
- Delivery adapters only record the hand-off; no message leaves the system. "I have refilled" in the patient portal stands in for a pharmacy fill.
- Claude provider untested (no key). It enables server-side refusal fallback by default.
- No browser end-to-end tests; Docker, Postgres and `setup.ps1` never run.
- `demo_mode` is on by default (on-screen code when no SMTP) and demo accounts are seeded. Turn both off for hosted use.
- Git history was rewritten on 2026-10-02 to remove credential literals (old hashes `c3f55df`, `6d3f691`, `541cb19` no longer exist on `main`). The earlier demo passwords were public for a time and should be treated as exposed.
- Account passwords in the database only change on reseed (`python -m app.datagen`); editing `.env` alone does not change existing accounts.
- Live SMTP delivery never exercised (no mail settings on this machine); covered only by a fake sender in tests.
- No forgot-password, password change, login lockout or rate limiting. No MFA at sign-in.
- A claimed synthetic patient/HCP record is renamed to the account holder; if an admin later links the account to another record, the earlier record keeps that name until the next reseed.
- Admin cannot delete accounts (disable only).
- Interaction timestamps combine the demo date with wall-clock time of day.
- `POST /api/admin/reset` and `advance` are synchronous and take 15 to 30 seconds at full scale.
- Patients with several therapies get one recommendation per cycle, for the highest-risk therapy.
- HCPs have no consent or opt-out model.
- Frontend bundle is one 700 kB chunk (no code splitting).
- The running server does not auto-reload; restart it after backend changes and rebuild the frontend (`npm run build`) after UI changes.

## Important Decisions

Product and architecture decisions are in `Master_Build.md` section 13 and are not repeated here. Implementation-level facts a new session needs:

1. Role names may appear only in `core/permissions.py`, the sign-up role list (`api/auth.py` `ROLE_INFO`) and seed data. `tests/test_permissions.py` fails otherwise, and also fails if any `/api` route outside its public allow-list answers an anonymous caller.
2. `sim_latent` may be read only by `datagen/` and `engagement/simulator.py`. `tests/test_nba.py` fails if `nba`, `features`, `scoring`, `audit` or `pipeline.py` reference it.
3. Simulator and seeded history must keep the same crediting rule for prompted fills (outreach is resolved before natural fills), or engine-versus-baseline numbers become misleading.
4. Scenario tests in `tests/test_nba.py` run on the default seed and scale; changing generator behaviour can change which action or channel the heroes get.
5. Content approval is guarded by `Permission.CONTENT_APPROVE`, held only by `compliance`. `tests/test_api.py` asserts the administrator gets 403.
6. `alembic/env.py` must keep using a plain engine (no foreign-key pragma) for SQLite batch migrations.
7. Seeded demo accounts are kept for now; the user may ask to remove them (`NBA_SEED_DEMO_ACCOUNTS=false`).
8. One-time codes are email only; SMS is deferred until the user asks.
9. Vite SPA served by FastAPI (not Next.js) to keep one deployable unit.
10. No secret in the repository: `tests/test_secrets.py` fails if a password, secret, key or token is assigned a string literal in a tracked file. Docs name the `.env` variable, never a value.

## Next Recommended Task

See `TODO.md` → Upcoming. First unblocked item: Playwright end-to-end tests. First item overall: a live sign-up with real email delivery, blocked until the user adds SMTP settings to `.env`.
