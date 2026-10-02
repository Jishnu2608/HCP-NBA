# Healthcare NBA POC

Explainable, compliance-gated next-best-action engine for two audiences: healthcare professionals (HCPs) and patients. For each person it recommends what to send, on which channel and when, attaches a plain-language rationale, and blocks anything that violates MLR approval, patient consent or contact limits.

It is a communication-decision tool, not a clinical one. All data is synthetic; no real patient or HCP data is used anywhere.

## Quick start (Windows)

Requires Python 3.12 or 3.13 and Node 20 or later.

```powershell
.\scripts\setup.ps1
.\scripts\run.ps1
```

Open http://localhost:8000 and pick a persona. Setup creates the Python environment, builds the frontend, creates the database, generates 3,000 patients and 300 HCPs, trains the models and runs the first recommendation cycle (about one minute).

The walkthrough is in [docs/demo-script.md](docs/demo-script.md).

## What is in it

| Area | What it does |
|---|---|
| Synthetic data | Seeded generator. Each person has hidden behaviour traits that drive fills, responses and what happens after a send, so models have real patterns to learn. |
| Features | Days covered (PDC), MPR, gap days and refill-lateness trend from fill history; engagement rates by channel, action and topic, computed point-in-time. |
| Scoring | Rule-based adherence risk score with named drivers; logistic-regression propensity models for response and fill, with a boosted-tree challenger for comparison. |
| Engine | Candidate actions per target, hard gates, ranking, one recommendation per target per cycle, the losing options kept, a rationale built from data. |
| Gates | MLR approval and validity, consent per channel, contact frequency. Deterministic. Checked at generation, at approval and at send. |
| Drafting | Provider interface with an offline template provider and an optional Claude provider. Output is validated; failures fall back to templates. |
| Roles | Admin, Compliance, Medical Rep, Care Manager, HCP, Patient. Row-level access enforced in the API. |
| Loop | Approve, send, capture response, advance the demo clock, retrain, next cycle. |
| Analytics | Recommendation mix, gate reasons, response by channel, adherence by measure over time, engine versus earlier outreach on equal terms. |

## Roles

| Role | Sees | Can do |
|---|---|---|
| Admin | Everything | Run cycles, advance the clock, change settings, reset |
| Compliance | Content library, blocked and held-back recommendations (identities hidden), audit log, metrics | Approve or reject content |
| Medical Rep | Assigned HCPs, their recommendations, approved HCP content | Approve, edit, reject, send; log visit outcome |
| Care Manager | Assigned patients, their recommendations, approved patient content | Approve, edit, reject, send; log call outcome |
| HCP | Own profile, own inbox, adherence summary of own patients who consent to sharing | Read and respond to content |
| Patient | Own medications, messages, consent | Change consent, respond, confirm refill |

Seeded logins use the password "<NBA_DEMO_PASSWORD>"; the login page also offers one-click personas while `NBA_DEMO_MODE` is on.

## Commands

Run from `backend` with the virtual environment's Python (`.venv\Scripts\python`).

| Command | Purpose |
|---|---|
| `python -m app.datagen [--patients N --hcps N --seed N]` | Replace all data with a seeded synthetic population and print a data-quality report |
| `python -m app.cycle [--retrain]` | Refresh features, optionally retrain, generate recommendations and drafts |
| `python -m app.pipeline` | Feature refresh and model training only, with holdout metrics |
| `python -m app.nba [ids...]` | Run the engine only and print chosen recommendations |
| `python -m pytest` | Backend tests (195) |
| `ruff check .` | Lint |
| `alembic upgrade head` | Apply database migrations |

Frontend, from `frontend`: `npm run dev` (hot reload on port 5173, proxies the API on 8000), `npm run build`, `npm run typecheck`.

## Configuration

Settings are in `backend/app/core/config.py` and can be overridden with `NBA_`-prefixed environment variables or a `.env` file at the repository root.

| Variable | Default | Notes |
|---|---|---|
| `NBA_DATABASE_URL` | SQLite file in `data/` | Any SQLAlchemy URL, for example `postgresql+psycopg://user:pass@host/db` (install the `postgres` extra) |
| `NBA_JWT_SECRET` | Local-only placeholder | Set a long random value for anything hosted |
| `NBA_DEMO_MODE` | `true` | Turns the passwordless persona switcher on or off |
| `NBA_LLM_PROVIDER` | `template` | `claude` uses the Anthropic SDK (`pip install -e ".[claude]"`) and credentials from the environment |

Engine policy (risk weights, thresholds, frequency caps, channel costs) is editable on the Engine page and stored in the database; defaults are in `backend/app/core/engine_config.py`.

## Layout

```
backend/app/core        config, database, security, access rules, demo clock, engine settings
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
frontend/src            React app: one shell, role-driven navigation
docs/                   demo script
scripts/                setup and run
data/                   SQLite database and model files (not committed)
```

## Design rules

1. Eligibility gates are deterministic code, never a model. They run at generation, at approval and at send.
2. The language model only words an action that already passed every gate. Its output is validated before it is stored.
3. Every recommendation carries a rationale and an audit trail; blocked ones stay visible in audit and are never sendable.
4. Row-level access is enforced in the API. Out-of-scope records return "not found".
5. `sim_latent` holds hidden ground truth for the generator and simulator only. A test fails if engine, feature, scoring or audit code references it.
6. Model features are computed as they stood before each historical touch, by the same code used for live scoring.

## Deployment

The API serves the built frontend, so the whole product is one process and one container.

- **Local:** as above. SQLite, no network needed.
- **Container:** `Dockerfile` and `docker-compose.yml` (app plus Postgres) are included. They have not been run on the development machine, which has no Docker; test before relying on them.
- **Databricks Apps:** the same single process fits the Apps model (a Python web server started by a command). Point `NBA_DATABASE_URL` at a Lakebase Postgres instance and start with `uvicorn app.main:app`. Not yet tried.
- **Vercel:** host `frontend/dist` as a static site with `/api` rewritten to the API running on a container host, and use a managed Postgres. Not yet tried.

Before any hosted use: set `NBA_JWT_SECRET`, turn `NBA_DEMO_MODE` off, replace the seeded password, and put real sign-in (OIDC/SSO) behind `get_current_user` in `backend/app/api/deps.py`.

## Known limits

- Models are trained on synthetic behaviour; holdout AUC is modest (about 0.63 to 0.69) by design of the generator. Real-world performance will differ.
- Delivery adapters only record the hand-off. No message leaves the system.
- The Claude provider is written against the documented API but has not been run here (no key configured). The template provider is what the demo uses.
- No browser end-to-end test suite yet; scenarios are covered at the API level and were checked by hand in the browser.
- Timestamps mix the demo date with wall-clock time of day.
