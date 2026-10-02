# Healthcare NBA POC — TODO

The execution backlog: what remains to be built, fixed, tested or improved.

- What the system is supposed to be: `Master_Build.md`.
- What is already implemented: `Workflow_Context.md` (completed work is recorded there, not here).

Status: `[ ]` not started · `[~]` in progress · `[x]` completed · `[!]` blocked

Rules: mark `[x]` only when implemented **and** verified. Add newly discovered work when it is found. Completed items stay one cycle for visibility, then are removed (they live on in `Workflow_Context.md`).

## Critical / Blocking

Nothing currently prevents correct operation.

## Current Sprint

Nothing in progress.

## Upcoming

- [!] Run one live sign-up with real email delivery of the verification code. Blocked: needs the user to put `NBA_SMTP_*` values in `.env` (see `.env.example`). Then confirm the email arrives, the code is not shown on screen or returned by the API, and a failed send behaves as designed.
- [ ] Add Playwright end-to-end tests against a freshly seeded database: sign-up → code → dashboard → sign-out → sign-in; the six NBA scenarios in `docs/demo-script.md`; Access Denied cases.
- [ ] Choose the LLM provider (Claude, Gemini, or an org cloud model) and run it live once. The Claude provider (`backend/app/llm/claude.py`) has never been executed; no Gemini provider exists.
- [ ] Have GitHub purge the pre-rewrite commits: they are off `main` but still fetchable by hash. Either contact GitHub Support or delete and recreate the repository. User action. The passwords they contained have been rotated and no longer work.
- [ ] Decide product name and branding for the client-facing version.
- [ ] Decide the first hosted target (Databricks Apps, or static frontend plus container API) and deploy.
- [ ] Decide whether to remove the seeded demo accounts (`NBA_SEED_DEMO_ACCOUNTS=false`). The user said they will say when.

## Enhancements

- [ ] Forgot-password and change-password flows.
- [ ] Admin: delete an account (today: disable only).
- [ ] Admin: resend or cancel a pending verification from the Users page.
- [ ] Add a mail check command (for example `python -m app.auth.mailcheck you@example.org`) that sends a test message and prints the failure type, so SMTP can be verified without a sign-up.
- [ ] Show on the verify page, in development delivery only, why email was not used (not configured, or send failed), instead of one generic message.
- [ ] Exclude hero patients and HCPs from automatic sign-up panels, so a newly registered care manager or rep cannot act on demo-scenario records.
- [ ] When an admin re-links a patient or HCP account to another record, restore the previous record's original synthetic name.
- [ ] Bulk approve-and-send for care managers and reps on their own queue (today: admin-only demo shortcut).
- [ ] Recommendations per therapy for patients with several therapies (today: one per patient, for the highest-risk therapy).
- [ ] Opt-out / contact-preference model for HCPs (today: none).
- [ ] Show model feature contributions on the recommendation detail page (`propensity.contributions` exists; the UI shows only rule-based reasons and the model estimate).
- [ ] Code-split the frontend bundle (single chunk of about 700 kB).
- [ ] Auto-reload in development for the combined server, or document `npm run dev` + `uvicorn --reload` as the dev loop.

## Technical Debt

- [ ] `POST /api/admin/reset` and `POST /api/admin/advance` run synchronously for 15 to 30 seconds at full scale. Move to a background job with progress.
- [ ] API responses are plain dicts from `api/serializers.py`. Add Pydantic response models so the OpenAPI reference documents response shapes.
- [ ] Interaction timestamps combine the demo date with wall-clock time of day. Give the demo clock a time component or store dates only.
- [ ] Permission names are duplicated in `frontend/src/permissions.ts` and `backend/app/core/permissions.py`. Generate one from the other, or add a test that compares them.
- [ ] `ROLE_PERMISSIONS` is code. Move to tables when roles need to be administered.
- [ ] Session token is stateless with a per-account version counter: sign-out ends every session of the account, not just one device. Move to server-side sessions if per-device sign-out is needed.
- [ ] Pytest emits `ResourceWarning` for unclosed SQLite connections in module-scoped fixtures. Dispose engines in fixture teardown.
- [ ] Line endings: Git warns LF will be replaced by CRLF on Windows. Add `.gitattributes`.
- [ ] Add a pre-commit secret scanner (for example gitleaks) so a credential cannot be committed even if the test is skipped.

## Testing

Covered today (see `Workflow_Context.md` → Testing): unit, API, RBAC, authentication and secret-hygiene tests, 265 passing.

- [!] Live SMTP delivery of the verification code: only a fake sender is tested. Blocked on SMTP settings (see Upcoming).
- [ ] End-to-end browser tests: none (see Upcoming).
- [ ] Frontend unit tests: none. Add Vitest for `session.ts`, `auth.tsx`, `mayOpen` and the route guards.
- [ ] Run the suite against PostgreSQL. Never done; SQLite only.
- [ ] Execute `Dockerfile` and `docker-compose.yml`. Written, never run (no Docker on the dev machine).
- [ ] Execute `scripts/setup.ps1` on a clean checkout. Written, never run end to end.
- [ ] Add CI (GitHub Actions): ruff, pytest, `alembic check`, frontend type-check and build.

## Production Readiness

Security and identity

- [x] No default secrets in code: `NBA_JWT_SECRET`, `NBA_ADMIN_PASSWORD`, `NBA_DEMO_PASSWORD` come only from the environment or `.env`; start-up fails without the signing secret; history rewritten to remove earlier literals. Verified by `tests/test_secrets.py` and a scan of every commit.
- [x] Rotated the administrator and demo passwords (the earlier values were in public history). New values are only in `.env`; database hashes replaced and old sessions revoked. Verified: new passwords accepted, old ones rejected.
- [ ] Supply secrets from the host's secret store in hosted deployments, not from a file in the image.
- [ ] Turn `NBA_DEMO_MODE` off for hosted use (no on-screen codes) and set `NBA_SEED_DEMO_ACCOUNTS=false`.
- [ ] Login lockout and rate limiting; rate limiting on sign-up and code resend.
- [ ] Enterprise SSO / OIDC behind `sessions.resolve` (`backend/app/auth/sessions.py`).
- [ ] MFA at sign-in; SMS or authenticator codes via a new `OtpSender`.
- [ ] Move the session token from `sessionStorage` to a secure cookie or provider SDK (`frontend/src/session.ts`).
- [ ] Security headers, CORS policy, HTTPS-only deployment.
- [ ] Security event monitoring: failed logins, permission denials, account changes.

Data and platform

- [ ] PostgreSQL as the database; connection pooling; backups.
- [ ] Real governed data ingestion (claims / pharmacy fills, CRM activity, consent management) replacing the generator.
- [ ] Cloud data platform and feature store for features and snapshots.
- [ ] Containerised deployment with CI/CD.

Engagement and models

- [ ] Real delivery adapters (email service, SMS gateway, CRM task, telephony) implementing `ChannelAdapter` in `backend/app/engagement/delivery.py`.
- [ ] Private enterprise LLM provider; prompt and output logging policy.
- [ ] Model registry, drift monitoring, scheduled retraining; evaluate uplift modelling and timing optimisation.
- [ ] Real attribution of outcomes to actions.

Governance

- [ ] Full MLR workflow: content versioning, multi-reviewer sign-off, resubmission of rejected content.
- [ ] HIPAA and TCPA review; data-retention policy for audit and interactions.
- [ ] Audit log export and tamper evidence.
