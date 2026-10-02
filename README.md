# Healthcare NBA POC

Explainable, compliance-gated next-best-action engine for two audiences: healthcare professionals (HCPs) and patients. For each person it recommends what to send, on which channel and when, attaches a plain-language rationale, and blocks anything that violates MLR approval, patient consent or contact limits.

It is a communication-decision tool, not a clinical one. All data is synthetic; no real patient or HCP data is used anywhere.

## Quick start (Windows)

Requires Python 3.12 or 3.13 and Node 20 or later.

```powershell
.\scripts\setup.ps1
.\scripts\run.ps1
```

Open http://localhost:8000. Setup creates the Python environment, builds the frontend, creates the database, generates 3,000 patients and 300 HCPs, trains the models and runs the first recommendation cycle (about one minute).

The walkthrough is in [docs/demo-script.md](docs/demo-script.md). The access model is described in [docs/architecture-auth-rbac.md](docs/architecture-auth-rbac.md).

## Signing in

The application opens on a landing page with **Log in** and **Sign up**. There is no persona picker: the account you sign in with decides the role, and the role decides the dashboard.

```
Landing  →  Log in or Sign up  →  (sign-up only) email code  →  role dashboard
```

### Administrator

| Email | Password | Role |
|---|---|---|
| `admin@admin.com` | value of `NBA_ADMIN_PASSWORD` in `.env` | Administrator |

The administrator is created automatically (by the data generator and again at start-up if missing). It cannot be created through sign-up. The email is `NBA_ADMIN_EMAIL`.

**No password is stored in this repository.** `setup.ps1` (or `python -m app.bootstrap`) writes random values for `NBA_JWT_SECRET`, `NBA_ADMIN_PASSWORD` and `NBA_DEMO_PASSWORD` into the git-ignored `.env` file. Open `.env` to read them. To change them later, edit `.env`, run `python -m app.auth.rotate` from `backend`, and restart the server. The old passwords and any open sessions stop working at once.

### Demo accounts

Seeded with the synthetic data so each role can be shown immediately. All share one password: the value of `NBA_DEMO_PASSWORD` in `.env`.

| Email | Role | What it is linked to |
|---|---|---|
| `cm01@nba.demo` (to `cm10`) | Care Manager | 300 patients each; `cm01` has the six scenario patients |
| `rep01@nba.demo` (to `rep20`) | Medical Representative | HCPs in one territory; `rep01` has the three scenario HCPs |
| `compliance1@nba.demo`, `compliance2@nba.demo` | Compliance / MLR Reviewer | No assignment needed |
| `pat00001@nba.demo` to `pat00006@nba.demo` | Patient | Margaret Doyle, Daniel Reyes, Helen Baker, George Whitman, Rosa Delgado, Samuel Nguyen |
| `hcp0001@nba.demo` to `hcp0003@nba.demo` | Healthcare Professional | Dr. Elena Marsh, Dr. Rajan Iyer, Dr. Thomas Okafor |

To remove the demo accounts and keep only the administrator, set `NBA_SEED_DEMO_ACCOUNTS=false` and reseed.

### Sign-up

Name, email, password, confirm password and one of five roles: Care Manager, Medical Representative, Compliance / MLR Reviewer, Patient, Healthcare Professional. Administrator is not offered and is rejected by the API.

The account is created unverified. A 6-digit code must be entered before it becomes active. The code expires after 10 minutes, works once, allows 5 attempts, and can be re-requested after 30 seconds. Only a hash of it is stored, and that is deleted when the code is used.

On verification the account is given starting data from the synthetic pool:

| Role | Receives |
|---|---|
| Patient | One unused synthetic patient record, shown under the account's name |
| Healthcare Professional | One unused synthetic HCP record |
| Care Manager | A panel of 30 patients across high, medium and low risk |
| Medical Representative | 15 HCPs across value segments |
| Compliance / MLR Reviewer | Nothing: the role works without assignments |

Existing assignments of the demo accounts are never altered by a sign-up.

### How the verification code is delivered

Email only.

- **Mail configured:** put SMTP settings in `.env` (see [.env.example](.env.example)). The code is emailed and never shown on screen or returned by the API.
- **No mail configured (default):** while `NBA_DEMO_MODE` is on, the verify page shows the code in a box labelled "Development mode: no email was sent", and the server logs it. Nothing pretends an email went out.
- If sending fails while demo mode is on, it falls back to the development box. With demo mode off, sign-up reports that the email could not be sent.

#### Setting up email delivery

Any SMTP service on port 587 with STARTTLS works. With Gmail:

1. Turn on 2-Step Verification for the sending account (Google Account → Security).
2. Create an app password at https://myaccount.google.com/apppasswords (16 characters; remove the spaces).
3. Add to `.env` at the repository root:

   ```
   NBA_SMTP_HOST=smtp.gmail.com
   NBA_SMTP_PORT=587
   NBA_SMTP_USER=<your address>
   NBA_SMTP_PASSWORD=<app password>
   NBA_SMTP_FROM=<your address>
   ```

4. Restart the server. Sign up with an inbox you can read.

The verify page then says "We emailed a code" and shows no code. If it still shows the development box, the send failed; the server log names the failure type. Other providers: Brevo (`smtp-relay.brevo.com`, SMTP key), Resend (`smtp.resend.com`, user `resend`, API key, needs a verified domain).

### Role to dashboard

| Role | Lands on |
|---|---|
| Administrator | `/dashboard` |
| Care Manager | `/queue` (adherence queue) |
| Medical Representative | `/queue` (HCP queue) |
| Compliance / MLR Reviewer | `/content` |
| Patient | `/medications` |
| Healthcare Professional | `/inbox` |

### Trying each role

1. Log in with the demo account for the role, or sign up as that role.
2. Open two browser tabs to hold two roles at once: each tab keeps its own session.
3. To see the access rules hold, type another role's address (for example `/admin` as a patient): the page shows Access Denied and the API refuses the data.

## Access control

```
Authenticated account  →  Role  →  Permission set  →  Data scope  →  UI
```

- **Role** is stored on the account at creation. No endpoint changes it.
- **Permissions** come from one map, `ROLE_PERMISSIONS` in `backend/app/core/permissions.py`. Endpoints ask for a permission, never for a role name.
- **Data scope** comes from assignments (`rep_hcp`, `care_manager_patient`, `user.patient_id`, `user.hcp_id`) applied as SQL filters in `backend/app/core/rbac.py`.
- **UI** receives the permission list from the server and uses it to build the menu and guard routes. Changing anything in the browser grants nothing: every request is authorised on the server.

| Role | Sees | Can do |
|---|---|---|
| Administrator | Every module and all data, including Users and assignments | Run cycles, advance the clock, change settings, reset, review recommendations, manage accounts. **Cannot approve content.** |
| Compliance / MLR | Content library, blocked and held-back recommendations (identities hidden), audit log, metrics | Approve or reject content (the only role that can) |
| Medical Representative | Assigned HCPs, their recommendations, approved HCP content | Approve, edit, reject, send; log visit outcome |
| Care Manager | Assigned patients, their recommendations, approved patient content | Approve, edit, reject, send; log call outcome |
| Healthcare Professional | Own profile, own inbox, adherence summary of own patients who consent to sharing | Read and respond to content |
| Patient | Own medications, messages, consent | Change consent, respond, confirm refill |

Out-of-scope records return "not found"; a missing permission returns "forbidden".

**Users and assignments (Administrator):** view every account, change what it is assigned to, disable or re-enable it. Disabling signs the account out everywhere at once. Changing an assignment changes data scope only; role and permissions are untouched.

## What is in it

| Area | What it does |
|---|---|
| Synthetic data | Seeded generator. Each person has hidden behaviour traits that drive fills, responses and what happens after a send. |
| Features | Days covered (PDC), MPR, gap days and refill-lateness trend; engagement rates by channel, action and topic, computed point-in-time. |
| Scoring | Rule-based adherence risk score with named drivers; logistic-regression propensity models with a boosted-tree challenger. |
| Engine | Candidate actions per target, hard gates, ranking, one recommendation per target per cycle, the losing options kept, a rationale built from data. |
| Gates | MLR approval and validity, consent per channel, contact frequency. Deterministic. Checked at generation, at approval and at send. |
| Drafting | Provider interface with an offline template provider and an optional Claude provider. Output is validated; failures fall back to templates. |
| Accounts | Email and password, sign-up with email code, permission-based access, admin account management. |
| Loop | Approve, send, capture response, advance the demo clock, retrain, next cycle. |
| Analytics | Recommendation mix, gate reasons, response by channel, adherence by measure over time, engine versus earlier outreach on equal terms. |

## Commands

Run from `backend` with the virtual environment's Python (`.venv\Scripts\python`).

| Command | Purpose |
|---|---|
| `python -m app.bootstrap` | Create missing secrets in `.env` with random values. Never changes existing ones. |
| `python -m app.auth.rotate` | Apply the passwords in `.env` to the administrator and demo accounts already in the database, and end their sessions. No reseed. |
| `python -m app.datagen [--patients N --hcps N --seed N]` | Rebuild the synthetic population and demo accounts. Registered accounts are kept. |
| `python -m app.cycle [--retrain]` | Refresh features, optionally retrain, generate recommendations and drafts |
| `python -m app.pipeline` | Feature refresh and model training only, with holdout metrics |
| `python -m app.nba [ids...]` | Run the engine only and print chosen recommendations |
| `python -m pytest` | Backend tests (260) |
| `ruff check .` | Lint |
| `alembic upgrade head` | Apply database migrations |

Frontend, from `frontend`: `npm run dev` (hot reload on port 5173, proxies the API on 8000), `npm run build`, `npm run typecheck`.

## Configuration

Settings are in `backend/app/core/config.py` and can be overridden with `NBA_`-prefixed environment variables or a `.env` file at the repository root. See [.env.example](.env.example).

| Variable | Default | Notes |
|---|---|---|
| `NBA_DATABASE_URL` | SQLite file in `data/` | Any SQLAlchemy URL, for example `postgresql+psycopg://user:pass@host/db` (install the `postgres` extra) |
| `NBA_JWT_SECRET` | none (required) | Signs session tokens. Created by `python -m app.bootstrap`. |
| `NBA_ADMIN_EMAIL` | `admin@admin.com` | The fixed administrator's email |
| `NBA_ADMIN_PASSWORD` | none (required) | The fixed administrator's password. Created by `python -m app.bootstrap`. |
| `NBA_SEED_DEMO_ACCOUNTS` | `true` | `false` seeds only the administrator |
| `NBA_DEMO_PASSWORD` | none (required while demo accounts are seeded) | Shared password of the seeded demo accounts |
| `NBA_DEMO_MODE` | `true` | Allows the on-screen code when no mail server is configured |
| `NBA_SMTP_HOST`, `_PORT`, `_USER`, `_PASSWORD`, `_FROM` | unset | When set, verification codes are emailed |
| `NBA_LLM_PROVIDER` | `template` | `claude` uses the Anthropic SDK (`pip install -e ".[claude]"`) |

Engine policy (risk weights, thresholds, frequency caps, channel costs) is editable on the Engine page and stored in the database.

## Layout

```
backend/app/core        config, database, password hashing, permissions, data scope, demo clock
backend/app/auth        accounts: repository, one-time codes, sessions, service, assignments
backend/app/models      tables and enums
backend/app/datagen     generator, hidden behaviour model, hero records, validation
backend/app/features    adherence arithmetic, point-in-time engagement features
backend/app/scoring     risk score, propensity models and their explanations
backend/app/nba         gates, engine, rationale, gate re-check
backend/app/llm         drafting contract, template and Claude providers, output validator
backend/app/engagement  delivery adapters, response capture, outcome simulator
backend/app/analytics   dashboard queries
backend/app/api         routers and serializers
backend/alembic         migrations
backend/tests           pytest suite
frontend/src            React app: session, permissions, route table, pages
docs/                   demo script, access architecture
scripts/                setup and run
data/                   SQLite database and model files (not committed)
```

## Design rules

1. Eligibility gates are deterministic code, never a model. They run at generation, at approval and at send.
2. The language model only words an action that already passed every gate. Its output is validated before it is stored.
3. Every recommendation carries a rationale and an audit trail; blocked ones stay visible in audit and are never sendable.
4. Authorisation is by permission, enforced in the API. The role is read from the account in the database on every request, never from the client.
5. Assignments decide data scope and are separate from the role. Out-of-scope records return "not found".
6. Only Compliance approves content; the administrator cannot.
7. `sim_latent` holds hidden ground truth for the generator and simulator only. A test fails if engine, feature, scoring or audit code references it.
8. Model features are computed as they stood before each historical touch, by the same code used for live scoring.

## Limits of the local authentication

This is a proof of concept. What is in place and what is not:

| In place | Not in place |
|---|---|
| Passwords hashed with scrypt, never stored or returned in clear | Forgot-password and password change |
| Signed session token, 8-hour life, revoked on the server at sign-out or disable | Lockout or rate limiting on repeated wrong passwords |
| One-time code: hashed, expiring, single use, attempt-limited | SMS or authenticator-app codes; code at every sign-in (MFA) |
| Role and permissions decided on the server | Single sign-on |
| Audit entries for account creation, verification, status and assignment changes | Security monitoring and alerting |

No password or signing secret has a default in code or appears in this repository. They are read from the environment or the git-ignored `.env`, and the application refuses to start without the signing secret.

### Moving to production authentication

Each concern sits behind one interface in `backend/app/auth/`, so each can be replaced on its own without touching permissions or data scoping.

| Today | Later | What changes |
|---|---|---|
| SQLite file | PostgreSQL | `NBA_DATABASE_URL` only; `SqlUserRepository` is unchanged |
| Local accounts and passwords | Enterprise SSO / OAuth / OIDC | `sessions.resolve` in `auth/sessions.py`, called from `get_current_user` in `api/deps.py` |
| Email code at sign-up | MFA, SMS, authenticator app | A new `OtpSender` in `auth/otp.py`; the rules in `OTPService` stay |
| `ROLE_PERMISSIONS` in code | Role and permission tables | `permissions_for` in `core/permissions.py` |
| Signed stateless token | Server-side session store | `SessionService` in `auth/sessions.py` |
| Browser `sessionStorage` | Secure cookie or provider SDK | `frontend/src/session.ts` |

## Deployment

The API serves the built frontend, so the whole product is one process and one container.

- **Local:** as above. SQLite, no network needed.
- **Container:** `Dockerfile` and `docker-compose.yml` (app plus Postgres) are included. They have not been run on the development machine, which has no Docker; test before relying on them.
- **Databricks Apps:** the same single process fits the Apps model. Point `NBA_DATABASE_URL` at a Lakebase Postgres instance and start with `uvicorn app.main:app`. Not yet tried.
- **Vercel:** host `frontend/dist` as a static site with `/api` rewritten to the API on a container host, and use a managed Postgres. Not yet tried.

Before any hosted use: provide the secrets through the host's secret store (not a file in the image), set `NBA_SEED_DEMO_ACCOUNTS=false`, configure SMTP and turn `NBA_DEMO_MODE` off.

## Known limits

- Models are trained on synthetic behaviour; holdout AUC is modest (about 0.63 to 0.69) by design of the generator.
- Delivery adapters only record the hand-off. No outreach message leaves the system. (Verification emails do, once SMTP is configured.)
- Email delivery of verification codes is covered by tests with a stand-in mail server; a live send has not been run because no SMTP settings are configured here.
- The Claude drafting provider is written against the documented API but has not been run here (no key configured).
- No browser end-to-end test suite yet; flows are covered at the API level and were checked by hand in the browser.
- Timestamps mix the demo date with wall-clock time of day.
