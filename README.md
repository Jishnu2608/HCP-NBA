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
| Patient 360 | Therapies, days covered (PDC), gaps, a fill-coverage timeline, consent, outreach history; for real patients, their conditions, reported and confirmed medications, care requests and instructions |
| Patient lists | Every list of patients is ordered on the server by latest activity, newest first (sign-ups, changes, entries, refills, messages, consent changes, sign-ins), with a "Last activity" column; ties are broken by patient ID |
| Patient health profile | A patient's own conditions and medications, care team (care manager and HCPs), instructions from the care team, and a "Consult a HCP" request. New patients start empty and build it themselves |
| Care management | Care managers work a queue of care requests: confirm or dismiss what patients reported (only confirmed medications count for adherence; a last refill date keeps an already-taken medication from looking unfilled), record medications, refills and instructions, route patients to an HCP by the condition's specialty, schedule follow-ups with a due date, and set up and invite clinic patients. Every real patient always has one active care manager |
| Consultations | A patient's consultation stays open after routing: the HCP sees it in the app and answers or declines (or the care manager records the answer of an HCP without an account), then the care manager closes it with a written outcome (or withdraws it with a reason the HCP and the patient both see). The patient sees each step and the advice under their request |
| Work awareness | Menu counts show new work (care requests, outreach to review or send, call outcomes to record, registered patients' responses, consultations, unread messages, administrator requests), with the same figures on every page; recommendations show whether their safeguards still pass before anyone acts; adherence is always as of today and updates the moment a medication is confirmed, refilled or stopped |
| Content governance (MLR) | Medical representatives propose HCP content with its claims and supporting references, indication, safety and benefit-risk information, labelling note and the countries it may be used in, and submit it. The MLR reviewer records a medical, legal and regulatory verdict and approves, requests changes or rejects, with feedback the author reads (an internal note stays with Compliance). A decided version never changes: the author opens a new version, which is reviewed again, and approving it replaces the earlier approved version everywhere. Approved material can be withdrawn, or sent back for a new version, when new evidence or a concern arises. Author and reviewer talk on the content itself |
| Role insights | Each role sees a few charts that answer its own question, drawn from the records and refreshed after every action: a patient's supply on hand over 30 days and where each consultation stands; an HCP's consultations and time to answer; a care manager's due dates, consultations waiting for an HCP and who may need attention (risk against time since contact); MLR's waiting submissions, approvals ending soon, time to decision, concerns by perspective and version history; a representative's next 14 days, when each HCP may next be contacted and where each HCP stands; an administrator's open work per account, sign-ins and failures. Streaks count finished work only (medicine on hand, no overdue items, follow-ups on time, consultations answered). Small samples are labelled, never charted as trends |
| Commercial loop (representatives) | From HCP 360 a representative proposes contact with approved content; the same safeguards as the engine decide (MLR approval, specialty, country, channel, contact limits, with the next allowed date shown). The HCP answers from their inbox (interested, request a meeting, need information or evidence, not now, not interested); the answer reaches the representatives currently assigned to them as work. Meetings (in person, phone, video, with time zone), follow-ups and visit or call outcomes with a reason and note close the loop, and the engine reads them: no other outreach while work is open, declined material not offered again, evidence requests answered with study material. **My HCP work** shows what is due, requests, meetings, overdue items and what was done today |
| Controlled delivery | What a patient or HCP receives is the approved content of one version, word for word, in a fixed template frame. Nobody edits the wording before sending; the send path refuses edited or altered wording and records which version and which wording went to whom. Withdrawn or replaced material stops being recommended at once, and copies already delivered are marked as no longer current |
| Delivery oversight | For every content item and version, the MLR reviewer sees recommendations waiting, deliveries, responses by outcome and channel, recipients without identity, the exact wording delivered, and copies still in inboxes after a withdrawal |
| HCP portal | Inbox of approved content with the sender and version; an HCP can ask about any item, and the question reaches the MLR reviewers (who see the HCP only by specialty and country) and the representative who sent it, with replies shown under the item. Consultations routed to the HCP are grouped as waiting, with the care manager, or finished; the patient's conditions and medicines are shown only while the consultation is with the HCP. My patients lists the patients whose care team the HCP is on and who share their adherence. Location and specialty decisions on the profile |
| HCP 360 | Segment and value, topics and channels the HCP engages with, interaction history |
| Adherence risk | Rule-based 0-100 score with named drivers: days covered, current gap, widening gaps, non-response, never filled |
| Propensity models | Chance of a response and of a fill, per action and channel. Logistic regression, compared against a boosted-tree challenger and a no-model baseline |
| Recommendation engine | Lists candidate actions, applies hard gates, ranks what is left, picks one, keeps the options that lost |
| Hard gates | Content approval (MLR) of the exact version and its validity window, the countries the content is cleared for, consent per channel, contact frequency. Deterministic code, checked at generation, at approval and at send |
| Rationale | Who, why this action, why this channel, why now, compliance status, and any better option a gate held back |
| Drafting | The approved content module set in a fixed template frame, through a model-agnostic interface with an offline default. A model may only frame the module; output that does not carry it word for word is discarded |
| Review | Approve, reject, send, choosing among the approved wording variants. Human-in-the-loop always |
| Closed loop | Simulated delivery, response capture, "play out responses now" for the simulated population, retraining, next cycle. Everything runs on today's real date; simulated refills follow real days |
| Analytics | Recommendation mix, why options were held back, response by channel, adherence by measure over time, engine versus earlier outreach on equal terms |
| Audit | Append-only record of every recommendation, gate result, decision, send, response, content decision (with its version and the status before and after) and settings change, with the acting account's durable id |
| Interface | One design system in clinical blue and cool slate, light and dark themes (saved per browser, otherwise following the system setting), responsive from phones to wide screens and at high browser zoom, a bento-grid layout for dashboards and 360 pages, consistent error pages (404, 400, 401, 403, 500, data failures) |

Therapy areas match the three Medicare STAR adherence measures: diabetes, hypertension, cholesterol.

## Roles

Access follows one chain, enforced on the server for every request:

```
Authenticated account → Role → Permissions → Data scope → UI
```

| Role | Sees | Can do |
|---|---|---|
| Administrator | Every module and all data, including users, invitations and assignments | Run the engine, change settings, manage accounts, permanently delete patient accounts, review recommendations, invite any professional role. **Cannot approve content.** |
| Compliance / MLR Reviewer | Content library with every submitted version and its review history, delivery and response per item (recipients unidentified), HCP questions, blocked and held-back recommendations (identities hidden), audit log with identities masked and their own actions marked, metrics | Review content (medical, legal, regulatory), approve, request changes, reject, withdraw, ask for a new version; maintain library content as new versions; answer authors and HCPs. The only role that decides on content. |
| Medical Representative | Assigned HCPs (location, contact window, history) and their recommendations, approved HCP content, own content proposals and the MLR feedback on them, HCP requests and questions, My HCP work (today, requests, follow-ups, meetings, overdue, done today) | Propose contact with a chosen HCP through the engine's safeguards; approve, reject, send; log visits and calls with what was learned; schedule meetings and follow-ups; answer HCP requests; propose, revise and submit HCP content; reply to MLR and to HCPs |
| Care Manager | Assigned patients, their care requests and recommendations, approved patient content | Approve, edit, reject, send; log call outcome; confirm what patients report, record medications, refills and instructions, route patients to an HCP by specialty, create clinic patients and invite them |
| Healthcare Professional | Own profile and specialties, own inbox, consultations routed to them, adherence summary of patients whose care team they are on and who consent to sharing | Read and respond to content, ask MLR about it; answer or decline a consultation; request a specialty change; invite a Medical Representative (who is then assigned to them) or Care Manager |
| Patient | Own conditions, medications, care team, instructions, messages and consent | Build a health profile, ask to consult an HCP, change consent, respond, log a refill |

- The role is fixed when the account is created: by patient sign-up, or by the invitation. No endpoint changes it.
- Inviting is an onboarding authority, not a hierarchy: Administrator → HCP, MR, CM, MLR; HCP → MR, CM; Care Manager → a clinic patient, from that patient's record; nobody else invites. Only the administrator can bring in an MLR reviewer.
- Permissions come from one map (`backend/app/core/permissions.py`). Endpoints ask for a permission, never a role name.
- Which records an account may see comes from its assignments, which are separate from the role. Changing an assignment never changes permissions.
- Out-of-scope records return "not found"; a missing permission returns "forbidden".
- The interface only mirrors these rules. Changing anything in the browser grants nothing.

More detail: [docs/architecture-auth-rbac.md](docs/architecture-auth-rbac.md).

## Signing in, signing up and invitations

```
Patients:       Sign up (name, date of birth, email, password) → emailed code → own empty health profile
Clinic patients: Care manager records the visit → invitation email → emailed code → the prepared record
Professionals:  Invitation email → /invite/<link> → name, date of birth, password → emailed code → role dashboard
Everyone:       Log in with email and password → the account's own dashboard
```

- **Log in** with email and password. The account's role decides the dashboard; nothing is chosen afterwards.
- **Patient sign-up** is public and always creates a patient account. There is no role choice.
- **Professional accounts** (HCP, Medical Representative, Care Manager, Compliance / MLR) are created only through an invitation. The invitation fixes the role and the email; the recipient cannot change either. Links work once and expire after 72 hours; the inviter can resend (which cancels the old link) or revoke them.
- A new account must confirm a 6-digit code before it becomes active. The code expires after 10 minutes, works once, allows 5 attempts, and is stored only as a hash.
- **Verified mark:** an invited professional who completes the code gets a blue check next to their name. Seeded demo staff carry it as platform-provisioned. Patients and the administrator do not. Email verification and professional verification are separate.
- **Date of birth** is required at sign-up and acceptance, checked on the server, and never shown to other users. Professionals must be at least 18 (configurable); patients under that age may register and are marked as minors for administrators.
- **A patient's data is their own.** A patient who signs up gets a new, empty record and a care manager; nothing from the synthetic demo population is attached. They can add conditions and medications and ask to consult a healthcare professional; each entry goes to their care manager, who confirms it, adds supply details and routes the patient to an HCP whose specialty suits the condition. Only confirmed medications count for adherence and recommendations. Location is the country (and US state) the person chose.
- **HCP specialties:** an invited HCP starts with a blank record and only the specialties the inviting administrator chose (several allowed; none shows "Specialty not configured"). The HCP can ask for a change from their profile; an administrator approves or rejects it in **Requests**. Care managers route patients using every specialty an HCP holds.
- **Patient lists** (Patients, a care manager's panel, an HCP's patients, assignment lists, search) always show the most recently active patient first: new sign-ups, changes, entries, refills, messages, consent changes and sign-ins all count. The order is set on the server; there is no sort control. The recommendation queue stays ranked by priority.
- **Clinic patients:** a care manager can set up a record for someone seen at the clinic, record conditions, medications and the doctor's instructions, and invite the patient by email; the invitation is bound to that record and the care manager stays responsible.
- On confirmation a professional account receives its starting data: an HCP gets a new blank record of their own (with the country they gave), a care manager a panel of demo patients, a medical representative a set of demo HCPs (plus the inviting HCP, when an HCP invited them). An invited HCP with an active account receives approved content through the same engine and safeguards as everyone else.
- **Compliance / MLR reviewers** exist only by invitation from the administrator; none is seeded with the demo data. Any number may exist: a review in progress shows its reviewer, and another reviewer takes it over explicitly.
- **Deleting a patient account:** an administrator can delete a patient account permanently (typing its email to confirm), also straight from the patient's deletion request. The account and the patient's own data go; the audit log keeps what happened without naming them; the same email can sign up again as a new, empty patient. Professional accounts are disabled, not deleted.
- The administrator manages invitations on **Invitations** and accounts on **Users and assignments** (assignments, disable or re-enable, who invited whom). An HCP sees the invitations they sent under **Team**.
- Sessions are kept in a cookie the page's JavaScript cannot read. Sign-out ends the session on the server. Two different accounts in two tabs of the same browser are no longer possible; use a private window or another browser profile for the second one.

Account credentials are not documented in this repository. See "Secrets and accounts" below.

### Verification code delivery

Email only.

- With a mail server configured, the code is emailed and never shown on screen, returned by the API or written to the log.
- If the email cannot be sent, no code is created and the account stays pending. The verify page says the email could not be sent and offers to send a new code straight away.
- Without a mail server, and only while demo mode is on, the verify page shows the code in a box that states no email was sent.
- A new code can be requested after 30 seconds; each new code replaces the previous one.
- After a successful check, the account is activated, given its starting data, signed in and sent to its role's dashboard.
- Invitation emails go through the same mail server. Without one, in a local run, the inviter sees the invitation link in a box that says no email was sent.
- Someone signing up with an email that already has an account sees the same "Check your email to continue" screen; the owner receives a notice instead of a code. The form cannot be used to find out who is registered.
- A failed sign-in always says "Incorrect email or password", whether the email is unknown, the password is wrong or the account is disabled.

### Privacy, terms and consent

- **Documents:** Privacy Policy, Terms & Conditions and Cookie Policy at `/legal/privacy`, `/legal/terms` and `/legal/cookies`, linked from every page footer, sign-up, invitation acceptance and Data & privacy. They are versioned files on the server; earlier versions stay readable. They are **drafts pending legal review**: facts only the operator can supply (entity, address, privacy contact, DPO, EU representative, governing law, hosting region) come from `NBA_LEGAL_*` settings and show as "[To be confirmed]" until set.
- **Agreements at sign-up:** an unticked box to agree to the Terms and acknowledge the Privacy Policy, and, for patients only, a separate unticked box giving explicit consent to process health information. The server refuses a sign-up or invitation acceptance without them and records each one with the document version, time, the person's country / state, and where it was given. Records are never edited.
- **Re-acceptance:** when a new document version requires it (or a patient withdraws health-information consent), the server refuses everything except reading the documents, Data & privacy and sign-out until it is accepted. Existing accounts, including the administrator and demo accounts, are asked once at their next sign-in.
- **Data & privacy** (every signed-in person): accepted versions, consents with withdrawal, consent history, a download of your own data, and privacy requests (access, correction, deletion, restriction, portability, objection, consent withdrawal). Requests are tickets worked by a person on **Privacy requests** (administrator); submitting one does not change or delete anything by itself.
- **Residence:** sign-up asks for country (and US state). Adulthood is decided per jurisdiction (18 unless an override applies; Alabama and Nebraska 19, Mississippi 21, all to be confirmed by counsel).
- **Cookies:** only the session, CSRF and verification cookies and two display preferences, all necessary or chosen by you. No analytics, advertising or tracking, so there is no cookie banner.
- **External language models** receive no identifiable patient or professional data unless `NBA_ALLOW_EXTERNAL_IDENTIFIABLE_DATA` is turned on.

The application makes no claim of HIPAA, GDPR, CCPA or any other compliance or certification; it is designed with applicable privacy and security requirements in mind, and its legal documents must be reviewed by qualified counsel before production use.

### Rate limits

Enforced by the server and stored in the database, so they survive a restart and cannot be reset from the browser. A refused request gets "Too many attempts" and a `Retry-After` time; nothing is ever locked permanently.

| Limit | Key | Allowed | Notes |
|---|---|---|---|
| Failed sign-ins | email + client address | 5 / 15 min | Only failures count; a successful sign-in clears it. Someone on another address cannot lock the owner out. |
| Failed sign-ins | client address | 20 / 15 min | Stops one address trying many emails. |
| Patient sign-up | client address | 5 / 15 min | Every attempt counts. |
| Code emails (sign-up, resend, invitation acceptance) | email + client address | 3 / 10 min | Counted only when an email is actually sent; the 30-second resend cool-down still applies. |
| Code entry | the pending account | 5 / 10 min | A new code does not reset it; each code also locks after 5 wrong tries. |
| Invitation acceptance | client address | 5 / 15 min | |
| Invitation lookup | client address | 30 / 10 min | |
| Invitations sent | inviter | 30 / hour | |


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
- **An MLR reviewer:** none is seeded. Invite one from **Invitations** with the role Compliance / MLR Reviewer; content proposals, reviews and approvals then happen on **Content**.
- **A professional account of your own:** sign in as the administrator, open **Invitations**, invite your email with a role, and open the link from the email (or, without email configured, from the development box).
- **A patient account:** use **Patient sign-up**. Without email configured, the verification code is shown on screen in development mode. The new patient starts with an empty health profile and is given a care manager; sign in as that care manager (shown under "Your care team") to see the requests on **Care requests**.
- **A clinic patient:** sign in as a care manager, open **Care requests** → **New clinic patient**, record their care on the patient page, then **Invite to portal**.

`NBA_PUBLIC_BASE_URL` sets the start of links in emails; set it to the address people will open (for example your LAN address when testing on a phone).

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

Open http://localhost:5173 while developing; it forwards `/api` to port 8000 (allowed by `NBA_ALLOWED_ORIGINS`). The interactive API reference at `/api/docs` is available only in a local run; its "Try it out" calls need the CSRF header, so the backend tests are the easier way to exercise the API. `.\scripts\run.ps1` instead serves the last frontend build from port 8000, so run `npm run build` after frontend changes when using it.

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

The Engine page has **Reset to seeded data**, which keeps registered and invited accounts, real patients with everything recorded for them, invitations, the security audit trail, and all content with its versions, MLR decisions and conversations, and signs everyone out. It never creates Compliance accounts. Account and invitation ids are never reused, not even after a deletion or a reset, so the audit history of one account can never appear under another; demo and administrator accounts get new, higher ids after each reset. `python -m app.datagen` from `backend` does the same from the command line. Deleting `data\nba_demo.db` and rerunning `setup.ps1` starts completely fresh.

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
| `NBA_JWT_SECRET` | Master signing secret (verification step, CSRF, code hashing). Required. |
| `NBA_ADMIN_EMAIL`, `NBA_ADMIN_PASSWORD` | The administrator account. Password required. |
| `NBA_SEED_DEMO_ACCOUNTS`, `NBA_DEMO_PASSWORD` | Whether demo accounts are created with the synthetic data, and their password |
| `NBA_DEMO_MODE` | Allows on-screen codes and invitation links in a local run without a mail server |
| `NBA_SMTP_*`, `NBA_SUPPORT_EMAIL` | Mail server for codes and invitations; contact shown in emails |
| `NBA_PUBLIC_BASE_URL`, `NBA_INVITATION_TTL_HOURS` | Start of links in emails; invitation lifetime |
| `NBA_ENVIRONMENT` | `local` by default. Anything else turns on Secure cookies and HSTS and turns off the API reference and on-screen codes |
| `NBA_SESSION_IDLE_MINUTES`, `NBA_ACCESS_TOKEN_MINUTES` | Session idle timeout and absolute lifetime |
| `NBA_ALLOWED_ORIGINS` | Extra origins allowed to send changes (the Vite dev server) |
| `NBA_MINOR_AGE` | Default age of adulthood (jurisdiction overrides in `core/jurisdiction.py`) |
| `NBA_LEGAL_*`, `NBA_PRIVACY_RESPONSE_DAYS` | Operator details shown in the legal documents; unset = "[To be confirmed]" |
| `NBA_ALLOW_EXTERNAL_IDENTIFIABLE_DATA` | Allow an external drafting provider to receive names (off by default) |
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
| Authentication | Server-side accounts and sessions, scrypt password hashes, HttpOnly session cookie with CSRF protection, invitation-only professional onboarding |
| Frontend | React, TypeScript, Vite, Tailwind, TanStack Query, Recharts; one design system with light and dark themes ([docs/design-system.md](docs/design-system.md)) |

```
backend/app/core        configuration, database, permissions, data scope, today's date
backend/app/auth        accounts, one-time codes, sessions, invitations, assignments, patient deletion
backend/app/clinical    real patient records, care management, condition and medication vocabulary
backend/app/legal       legal documents, consent records, privacy requests, data export
backend/app/models      tables and enums
backend/app/datagen     synthetic generator and validation
backend/app/features    adherence arithmetic, engagement features
backend/app/scoring     risk score, propensity models
backend/app/nba         gates, engine, rationale
backend/app/llm         drafting contract, providers, output validator
backend/app/engagement  delivery adapters, response capture, simulator
backend/app/analytics   dashboard queries
backend/app/insights    figures behind the role charts and streaks
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
4. Authorisation is by permission, enforced in the API. The role is read from the account on every request, never from the client. The browser is treated as untrusted: request bodies cannot carry fields the endpoint does not define.
5. Professional roles exist only through invitations, and who may invite whom is one permission table.
6. Assignments decide data scope and are separate from the role.
7. Only Compliance approves content. The administrator cannot.
8. The simulator's hidden behaviour traits are never readable by the engine, features or models.
9. Model features are computed as they stood before each historical touch; no look-ahead.
10. No secret in the repository.
11. The audit trail is append-only, and account, invitation and patient ids are never reused, so every entry keeps pointing at the account it was written for. The one exception: deleting a patient account replaces that person's identifiers in the audit rows with a neutral label.
12. Nothing tells an outsider whether an email is registered: sign-up and failed sign-in answer the same way either way, and rate limits are enforced on the server, never in the browser.
13. A real patient's data is their own. Sign-up never attaches synthetic demo data; clinical information enters only through the patient or their care team, and only synthetic people are simulated.
14. Care managers route patients to HCPs; nothing is diagnosed or assigned automatically.
15. Patient lists have one server-side order, latest activity first; the recommendation queue keeps its priority ranking.
16. A consultation closes only with an outcome: routing hands it to the HCP; the HCP's answer returns it to the care manager.
17. One time base: stored times are UTC and "today" is the UTC date; screens show local time.
18. What a recipient reads is the MLR-approved version, word for word. Changing the wording means a new content version that MLR reviews again.
19. One approved version per content item; decided versions never change and are never re-approved or re-dated.
20. Content governance survives a demo reset, and Compliance accounts exist only by invitation.
21. Routing a consultation never makes an HCP a prescriber, and an HCP sees a patient's clinical details only while a consultation is with them.
22. A representative contacts an HCP only through the engine's safeguards; HCP work follows the current assignment; an HCP's intent is a signal and a task, never a way around a safeguard.
23. Every chart figure is computed on the server within the reader's own scope; small samples are labelled instead of charted, charts add no clinical, compliance or commercial claim, and streaks count only finished work from records.
24. Motion explains a change and never decorates: transform and opacity only, no loops on working pages, nothing animates on first load, reduced motion turns it off.

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
- **Email verification codes and invitation emails:** both were checked end to end once with Gmail: a sign-up code, and an invitation that was accepted, verified with its emailed code and produced an active, verified HCP account. Real delivery has no automated test (tests use a fake mailbox), and the invitation email has not been checked in Outlook or Apple Mail. A personal Gmail account has daily sending limits and no domain authentication, so hosted use needs a transactional email service. Email is the only channel: no SMS or authenticator codes.
- No forgot-password, password change or MFA at sign-in yet. There is deliberately no permanent account lockout: repeated failures are slowed by time-limited rate limits instead, so nobody can lock another person out.
- Rate limits count by the client address the server sees. Behind a reverse proxy or load balancer every request would share one address; reading the forwarded client address is not configured yet.
- Unverified accounts: the correct password for an account that has not confirmed its email answers "verify your email" instead of the generic sign-in failure, so the owner can finish. Anyone holding that password can see the account exists.
- A professional cannot be invited to an email that already has a patient account.
- Only patient accounts can be deleted; professional accounts can only be disabled. Other privacy requests (correction, restriction) are still carried out by a person.
- Demo HCPs are all in the US; invited HCPs carry their own country, and routing puts HCPs in the patient's country first.
- MLR review is one reviewer's decision covering the medical, legal and regulatory perspectives (no separate sign-off per reviewer), approval validity is fixed at two years, and care managers cannot propose patient content (the MLR reviewer maintains it).
- **Legal documents are drafts.** Legal bases, retention periods, HIPAA applicability, sub-processors, hosting region, transfer mechanisms, governing law and adult-age rules are marked to be confirmed. No retention periods are enforced except for expired security records (sessions, codes, rate-limit counters).
- No guardian / parental-consent flow for minors, and no consent or opt-out model yet for content sent to healthcare professionals.
- The hosted language-model provider has not been exercised; drafting uses offline templates.
- No browser end-to-end tests yet; behaviour is covered by backend tests and manual checks, including a scripted layout check at phone, tablet and desktop widths. Real browser zoom and operating-system display scaling have not been tested.
- Explanation texts from the engine format dates day-month-year, while the interface uses US dates.
- The theme switch is per browser; it is not stored with the account.
