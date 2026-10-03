# Healthcare Next Best Action (NBA) — Proof of Concept

*The right message, to the right person, on the right channel, at the right moment.*

An explainable, compliance-gated next-best-action engine for two audiences: healthcare professionals (HCPs) and patients. For each person it recommends what to send, on which channel and when, attaches a plain-language reason, and blocks anything that violates content approval, patient consent or contact limits.

It is a communication-decision tool, not a clinical one. All data is synthetic. No real patient or HCP data is used anywhere.

## The problem

- HCPs are saturated with generic outreach and tune it out.
- About half of patients with chronic conditions do not take medication as prescribed; some never fill a first prescription.
- Adherence feeds quality ratings (HEDIS, Medicare STAR) and value-based-care payments, so non-adherence is a direct cost.
- The data needed to personalise sits in separate systems.

## The idea

One engine for both audiences, on one data foundation, sitting on top of existing CRM and marketing tools rather than replacing them.

```
Unify → Segment → Predict NBA → Personalize → Orchestrate → Engage → Capture → Learn
```

| Step | What happens |
|---|---|
| Unify | One profile per HCP and per patient: fills, interactions, consent, content exposure |
| Segment | Patient adherence risk; HCP value and engagement |
| Predict NBA | The single most useful next interaction, with reasons |
| Personalize | Wording built only from approved content |
| Orchestrate | One recommendation per person per cycle; contact limits respected |
| Engage | A person reviews and approves; the message goes out on the chosen channel |
| Capture | Opened, clicked, replied, call completed, prescription filled |
| Learn | Outcomes feed the next cycle and the dashboard |

## What the application does

| Area | Detail |
|---|---|
| Patient 360 | Therapies, days covered (PDC), gaps, a fill-coverage timeline, consent, outreach history |
| HCP 360 | Segment and value, topics and channels the HCP engages with, interaction history |
| Adherence risk | Rule-based 0-100 score with named drivers: days covered, current gap, widening gaps, non-response, never filled |
| Propensity models | Chance of a response and of a fill, per action and channel. Logistic regression, compared against a boosted-tree challenger and a no-model baseline |
| Recommendation engine | Lists candidate actions, applies hard gates, ranks what is left, picks one, keeps the options that lost |
| Hard gates | Content approval (MLR) and validity window, consent per channel, contact frequency. Deterministic code, checked at generation, at approval and at send |
| Rationale | Who, why this action, why this channel, why now, compliance status, and any better option a gate held back |
| Drafting | Message wording from the approved content module, through a model-agnostic interface with an offline default. Output is validated before it is stored |
| Review | Approve, edit, reject, send. Human-in-the-loop always |
| Closed loop | Simulated delivery, response capture, a demo clock that advances time, retraining, next cycle |
| Analytics | Recommendation mix, why options were held back, response by channel, adherence by measure over time, engine versus earlier outreach on equal terms |
| Audit | Append-only record of every recommendation, gate result, decision, send, response and settings change, with the actor |
| Interface | One design system in clinical blue and cool slate, light and dark themes (saved per browser, otherwise following the system setting), responsive from phones to wide screens and at high browser zoom, a bento-grid layout for dashboards and 360 pages, consistent error pages (404, 400, 401, 403, 500, data failures) |

Therapy areas match the three Medicare STAR adherence measures: diabetes, hypertension, cholesterol.

## Roles

Access follows one chain, enforced on the server for every request:

```
Authenticated account → Role → Permissions → Data scope → UI
```

| Role | Sees | Can do |
|---|---|---|
| Administrator | Every module and all data, including users and assignments | Run the engine, change settings, manage accounts, review recommendations. **Cannot approve content.** |
| Compliance / MLR Reviewer | Content library, blocked and held-back recommendations (identities hidden), audit log, metrics | Approve or reject content. The only role that can. |
| Medical Representative | Assigned HCPs and their recommendations, approved HCP content | Approve, edit, reject, send; log visit outcome |
| Care Manager | Assigned patients and their recommendations, approved patient content | Approve, edit, reject, send; log call outcome |
| Healthcare Professional | Own profile, own inbox, adherence summary of own patients who consent to sharing | Read and respond to content |
| Patient | Own medications, messages and consent | Change consent, respond, confirm a refill |

- The role is fixed when the account is created. No endpoint changes it.
- Permissions come from one map (`backend/app/core/permissions.py`). Endpoints ask for a permission, never a role name.
- Which records an account may see comes from its assignments, which are separate from the role. Changing an assignment never changes permissions.
- Out-of-scope records return "not found"; a missing permission returns "forbidden".
- The interface only mirrors these rules. Changing anything in the browser grants nothing.

More detail: [docs/architecture-auth-rbac.md](docs/architecture-auth-rbac.md).

## Signing in and signing up

```
Landing page → Log in or Sign up → (sign-up only) emailed code → role dashboard
```

- **Log in** with email and password. The account's role decides the dashboard; nothing is chosen afterwards.
- **Sign up** with name, email, password and one of five roles. The administrator role cannot be self-registered.
- A new account must confirm a 6-digit code before it becomes active. The code expires after 10 minutes, works once, allows 5 attempts, and is stored only as a hash.
- On confirmation the account receives starting data from the synthetic pool: one own record for a patient or HCP, a panel of patients for a care manager, a set of HCPs for a medical representative.
- An administrator can change assignments, and disable or re-enable accounts, on the Users and assignments page.

Account credentials are not documented in this repository. See "Secrets and accounts" below.

### Verification code delivery

Email only.

- With a mail server configured, the code is emailed and never shown on screen, returned by the API or written to the log.
- If the email cannot be sent, no code is created and the account stays pending. The verify page says the email could not be sent and offers to send a new code straight away.
- Without a mail server, and only while demo mode is on, the verify page shows the code in a box that states no email was sent.
- A new code can be requested after 30 seconds; each new code replaces the previous one.
- After a successful check, the account is activated, given its starting data, signed in and sent to its role's dashboard.

To configure email, add the `NBA_SMTP_*` settings listed in [.env.example](.env.example) to `.env` and restart. Any SMTP service on port 587 with STARTTLS works; with Gmail, use an app password (it needs two-step verification on the account), not the account password.

## Getting started

### Prerequisites

- Git
- Python 3.13. On Windows the setup script calls it through the `py` launcher (`py -3.13`), which the python.org installer adds.
- Node.js 20 or later (includes npm)

### From a fresh clone (Windows)

```powershell
git clone https://github.com/Jishnu2608/HCP-NBA.git
cd HCP-NBA
.\scripts\setup.ps1
.\scripts\run.ps1
```

Open http://localhost:8000.

`setup.ps1` runs once and takes a few minutes. It:

1. Creates the Python environment in `backend\.venv` and installs the backend with its development tools.
2. Installs the frontend packages and builds the web application into `frontend\dist`.
3. Creates `.env` with random values for every required secret (`python -m app.bootstrap`).
4. Creates the SQLite database in `data\` (`alembic upgrade head`).
5. Generates the synthetic population, trains the models and runs the first recommendation cycle.

If PowerShell refuses to run the scripts, allow local scripts for your user once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

### Signing in the first time

- **Administrator:** the email in `NBA_ADMIN_EMAIL` (default shown in [.env.example](.env.example)) with the password that `setup.ps1` generated in your `.env` as `NBA_ADMIN_PASSWORD`. Choose your own by editing `.env`, then run `python -m app.auth.rotate` from `backend` and restart.
- **Demo accounts** for the other roles are created with the synthetic data and share `NBA_DEMO_PASSWORD`. An administrator can see their emails on the Users and assignments page.
- **Your own account:** sign up with any role except Administrator. Without email configured, the verification code is shown on screen in development mode.

### Optional: email delivery of verification codes

Add the `NBA_SMTP_*` values from [.env.example](.env.example) to `.env` and restart the server. See "Verification code delivery" above.

### Working on the code

Run the API and the frontend dev server side by side for instant reload:

```powershell
# Terminal 1 - API with auto-reload, from backend\
.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000

# Terminal 2 - frontend with hot reload, from frontend\
npm run dev
```

Open http://localhost:5173 while developing; it forwards `/api` to port 8000. `.\scripts\run.ps1` instead serves the last frontend build from port 8000, so run `npm run build` after frontend changes when using it.

Before committing:

| Where | Command |
|---|---|
| `backend` | `.venv\Scripts\python -m pytest` |
| `backend` | `.venv\Scripts\python -m ruff check .` and `ruff format .` |
| `backend` | `.venv\Scripts\alembic check` (after changing tables; must report no new operations) |
| `frontend` | `npm run typecheck` and `npm run build` |

### macOS and Linux

The scripts are PowerShell; the same steps by hand:

```bash
cd backend && python3.13 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cd ../frontend && npm install && npm run build
cd ../backend && .venv/bin/python -m app.bootstrap && .venv/bin/alembic upgrade head
.venv/bin/python -m app.datagen && .venv/bin/python -m app.cycle --retrain
.venv/bin/python -m uvicorn app.main:app --port 8000
```

### Viewing it on a phone

The server listens only on this computer by default. To open it from a phone on the same, trusted Wi-Fi, start it with `--host 0.0.0.0`, allow port 8000 for private networks in the firewall, and open `http://<computer's IPv4 address>:8000` on the phone. Stop it when done; anyone on that network can reach the sign-in page while it runs.

### Resetting the data

The Engine page has **Reset to seeded data**, which keeps registered accounts. `python -m app.datagen` from `backend` does the same from the command line. Deleting `data\nba_demo.db` and rerunning `setup.ps1` starts completely fresh.

## Secrets and accounts

**No password, signing secret, key or account login is stored in this repository, and none has a default in code.**

- Secrets live in a git-ignored `.env` file at the repository root.
- `python -m app.bootstrap` (run by `setup.ps1`) creates any missing secret with a random value and never changes an existing one.
- The administrator account and the optional demo accounts take their credentials from configuration. Whoever runs the application reads them in their own `.env`.
- To change passwords: edit `.env`, run `python -m app.auth.rotate` from `backend`, restart. Earlier passwords and open sessions stop working at once.
- The application refuses to start without its signing secret.

## Configuration

Set through `NBA_`-prefixed environment variables or `.env`. Names only; see [.env.example](.env.example).

| Variable | Purpose |
|---|---|
| `NBA_DATABASE_URL` | Database. SQLite file by default; any SQLAlchemy URL, for example PostgreSQL |
| `NBA_JWT_SECRET` | Signs session tokens. Required. |
| `NBA_ADMIN_EMAIL`, `NBA_ADMIN_PASSWORD` | The administrator account. Password required. |
| `NBA_SEED_DEMO_ACCOUNTS`, `NBA_DEMO_PASSWORD` | Whether demo accounts are created with the synthetic data, and their password |
| `NBA_DEMO_MODE` | Allows the on-screen verification code when no mail server is configured |
| `NBA_SMTP_*` | Mail server for verification codes |
| `NBA_LLM_PROVIDER` | Drafting provider. Offline templates by default. |

Engine policy (risk weights, thresholds, contact limits, channel costs) is edited on the Engine page and stored in the database.

## Commands

From `backend`, using the virtual environment's Python.

| Command | Purpose |
|---|---|
| `python -m app.bootstrap` | Create missing secrets in `.env` |
| `python -m app.auth.rotate` | Apply the passwords in `.env` to existing administrator and demo accounts |
| `python -m app.datagen` | Rebuild the synthetic population. Registered accounts are kept. |
| `python -m app.cycle [--retrain]` | Refresh features, optionally retrain, generate recommendations and drafts |
| `python -m app.pipeline` | Feature refresh and model training, with holdout metrics |
| `python -m uvicorn app.main:app --reload --port 8000` | Run the API with auto-reload |
| `python -m pytest` | Backend tests |
| `ruff check .` | Lint |
| `alembic upgrade head` | Apply database migrations |

From `frontend`: `npm run dev`, `npm run build`, `npm run typecheck`.

## Architecture

One process and one deployable unit: the API also serves the built web application.

| Layer | Technology |
|---|---|
| API | FastAPI, Pydantic |
| Database | SQLAlchemy, Alembic; SQLite locally, PostgreSQL-ready |
| Features and models | pandas, scikit-learn |
| Authentication | Server-side accounts, scrypt password hashes, signed session tokens |
| Frontend | React, TypeScript, Vite, Tailwind, TanStack Query, Recharts; one design system with light and dark themes ([docs/design-system.md](docs/design-system.md)) |

```
backend/app/core        configuration, database, permissions, data scope, demo clock
backend/app/auth        accounts, one-time codes, sessions, assignments
backend/app/models      tables and enums
backend/app/datagen     synthetic generator and validation
backend/app/features    adherence arithmetic, engagement features
backend/app/scoring     risk score, propensity models
backend/app/nba         gates, engine, rationale
backend/app/llm         drafting contract, providers, output validator
backend/app/engagement  delivery adapters, response capture, simulator
backend/app/analytics   dashboard queries
backend/app/api         routers
backend/tests           test suite
frontend/src            web application
docs/                   demo walkthrough, access architecture, design system
scripts/                setup and run
```

## Design rules

1. Eligibility gates are deterministic code, never a model. They run at generation, at approval and at send.
2. The language model only words an action that already passed every gate. It never decides consent, approval, contact limits, authorisation or selection.
3. Every recommendation carries a rationale and an audit trail. Blocked ones stay visible in audit and are never sendable.
4. Authorisation is by permission, enforced in the API. The role is read from the account on every request, never from the client.
5. Assignments decide data scope and are separate from the role.
6. Only Compliance approves content. The administrator cannot.
7. The simulator's hidden behaviour traits are never readable by the engine, features or models.
8. Model features are computed as they stood before each historical touch; no look-ahead.
9. No secret in the repository.

## Path to production

Each concern sits behind an interface, so each is a replacement rather than a redesign.

| Today | Later |
|---|---|
| SQLite | PostgreSQL |
| Local accounts | Enterprise single sign-on, MFA |
| Email code over SMTP (a personal mailbox works for a demo) | Transactional email service; SMS or authenticator codes |
| Synthetic data | Governed claims, pharmacy, CRM and consent data |
| Simulated delivery | CRM, marketing automation, telephony |
| Offline templates | Private enterprise language model |
| Local run | Containers and cloud deployment |

`Dockerfile` and `docker-compose.yml` are included but have not been exercised.

## Known limits

- Models are trained on synthetic behaviour; measured quality is modest by design and real-world results will differ.
- Outreach delivery is simulated. No outreach message leaves the system.
- **Email verification codes:** working with a configured SMTP account and checked once end to end with Gmail, but real delivery has no automated test (tests use a fake sender). A personal Gmail account has daily sending limits and no domain authentication, so hosted use needs a transactional email service. Email is the only channel: no SMS or authenticator codes.
- No forgot-password, password change, login lockout, rate limiting or MFA at sign-in yet.
- An administrator can disable accounts but not delete them from the app.
- The hosted language-model provider has not been exercised; drafting uses offline templates.
- No browser end-to-end tests yet; behaviour is covered by backend tests and manual checks, including a scripted layout check at phone, tablet and desktop widths. Real browser zoom and operating-system display scaling have not been tested.
- Explanation texts from the engine format dates day-month-year, while the interface uses US dates.
- The theme switch is per browser; it is not stored with the account.
