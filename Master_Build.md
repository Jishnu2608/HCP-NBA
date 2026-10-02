# Healthcare NBA POC — Master Build

The permanent product and architecture specification: what the system is supposed to be. It is not a progress log.

- What has actually been built: `Workflow_Context.md`.
- What remains to be done: `TODO.md`.
- If this file and the code disagree on a *rule* in sections 7 to 10 or 13, treat it as a defect to raise, not a detail to overwrite.

Update this file only when requirements, architecture, roles or permissions, technology choices, or system rules change.

## 1. Product Overview

**Purpose.** A next-best-action (NBA) engine for healthcare engagement. For each individual it recommends the single most useful next interaction: what to send, on which channel, and when, with the reason attached and the compliance check done.

**Business problem.** Engagement fails on both sides for the same reason: blanket communication that ignores who the person is.

- Healthcare professionals (HCPs) are saturated with generic outreach and tune it out, while in-person access keeps shrinking.
- Roughly half of patients with chronic conditions do not take medication as prescribed. Estimates put avoidable US cost at about $250-300 billion and preventable deaths at about 125,000 a year. Some patients never fill a first prescription.
- Adherence feeds quality ratings (HEDIS, Medicare STAR) and value-based-care payments, so non-adherence is a direct financial cost to payers and providers.
- The data needed to personalise sits in silos.

**Target users.**

| User | Role in the product |
|---|---|
| Care manager / patient-support agent | Works the patient adherence queue; approves and sends outreach |
| Medical representative / commercial engagement | Works the HCP queue; approves and sends content |
| Compliance / MLR reviewer | Approves or rejects content; reviews gate outcomes and audit |
| Administrator | Runs the platform; manages accounts and assignments |
| HCP | Receives relevant, approved content; sees consented patients' adherence |
| Patient | Sees own medications, messages and consent; responds |
| Leadership (via admin or compliance views) | Sees whether engagement and adherence move |

Budget holders: VP Population Health / Quality (payer, provider, patient-support programme) and VP Commercial / Customer Engagement (pharma).

**Business value.** Fewer patients falling off therapy, higher adherence-linked quality scores, more HCP engagement on relevant content, and less wasted outreach, with every recommendation explainable and pre-cleared.

**Vision and positioning.** One engine, both audiences, on one data foundation. An intelligent, explainable, compliant layer **on top of** the client's CRM and marketing automation, never a replacement for them. Lead with patient adherence (clearer return, less crowded market); keep HCP engagement as the second audience. It is a **communication-decision** product, not a clinical-decision one.

## 2. Product Scope

**In the current POC**

- Synthetic patients, HCPs, fills, interactions, consent and content.
- Patient 360 and HCP 360.
- Adherence measurement and risk stratification; HCP value and engagement segmentation.
- Propensity models for response and fill.
- NBA engine: candidates, hard gates, ranking, one recommendation per person per cycle, rationale.
- Message drafting behind a model-agnostic interface.
- Human review, simulated delivery, response capture, a demo clock, retraining.
- Accounts, one-time-code verification, permission-based RBAC, assignments, admin user management.
- Audit log and analytics dashboard.
- Three therapy areas matching the Medicare STAR adherence measures: diabetes, hypertension (RAS antagonists), cholesterol (statins).

**Intentionally excluded from the POC**

- Any real patient or HCP data.
- Real outbound delivery of outreach (email, SMS, telephony, CRM tasks).
- Integration with a CRM, marketing-automation platform, consent-management system, claims or EHR feed.
- Clinical decision support of any kind.
- Enterprise identity (SSO), MFA at sign-in, password recovery.
- Uplift modelling, bandits, true multi-touch attribution.
- Ingestion of public datasets (Synthea, CMS SynPUF, NPPES, Part D); the POC mirrors their shapes instead.

**Belongs to the production version**

Everything in section 12, plus HIPAA / TCPA review, a full MLR workflow (versioning, multi-reviewer sign-off), monitoring and alerting, and multi-tenant client configuration.

## 3. Core Product Workflow

```
Unify → Segment → Predict NBA → Personalize → Orchestrate → Engage → Capture → Learn
   ↑                                                                          │
   └──────────────────────────────────────────────────────────────────────────┘
```

| Step | Intent |
|---|---|
| **Unify** | One profile per HCP and per patient from scattered signals: interactions, fills, consent, content exposure. Nothing intelligent is possible before this exists. |
| **Segment** | Group by what matters: patient adherence risk; HCP specialty, value and engagement. |
| **Predict NBA** | For each person, the single most valuable next interaction: action, channel, timing, with a rationale. |
| **Personalize** | Wording tailored to the person, built only from approved content modules. |
| **Orchestrate** | One coherent journey: one recommendation per person per cycle, contact limits respected, no conflicting touches. |
| **Engage** | A person reviews, then the message is delivered on the chosen channel, consent and preference respected. |
| **Capture** | What happened: opened, clicked, replied, call completed, prescription filled. |
| **Learn** | Outcomes become features and training data; leadership sees the metrics move. |

## 4. System Architecture

One deployable unit: an API that also serves the web application. Each external dependency (database, model provider, delivery channel, identity) sits behind an interface.

| Component | Responsibility |
|---|---|
| **Frontend** | Role-driven web application. Shows what the server permits; decides nothing about access. |
| **Backend API** | The only path to data. Authenticates, authorises by permission, applies data scope, runs engine operations. |
| **Database** | System of record for profiles, events, content, recommendations, accounts, audit. Relational, portable between SQLite and PostgreSQL. |
| **Data / analytics layer** | Loads observable data for the engine; computes aggregates for dashboards. |
| **Patient 360** | Demographics, therapies, fill timeline, adherence, consent, outreach history, open recommendation. |
| **HCP 360** | Profile, segment and value, channel and topic engagement, interaction history, open recommendation. |
| **Feature engineering** | Adherence (PDC, MPR, gap days, lateness trend) and engagement features, computed point-in-time so training cannot see the future. |
| **NBA decision engine** | Candidate generation, gates, scoring, ranking, selection, rationale. |
| **Compliance / consent engine** | Deterministic gates: MLR status and validity, consent per channel, contact frequency. Re-run at approval and at send. |
| **LLM layer** | Drafts message wording and a short rationale summary for an already-eligible action. Output is validated; failure falls back to templates. |
| **Authentication** | Accounts, one-time-code verification, sessions. |
| **RBAC** | Role → permission set → data scope. Enforced in the API. |
| **Engagement simulation** | Stands in for the real world in the POC: delivery adapters record the hand-off; a simulator produces responses and refills from hidden behaviour traits. |
| **Analytics** | Recommendation mix, gate reasons, response and fill rates, adherence over time, engine versus baseline on equal terms. |
| **Audit system** | Append-only record of every recommendation, gate result, review decision, send, response, content decision, account and configuration change, with the actor. |

**Hidden ground truth.** The simulator's behaviour traits are stored apart from everything the engine can read. The engine, features and models may only use data a real deployment could observe.

## 5. Technology Stack

| Layer | POC choice | Production evolution |
|---|---|---|
| Language / runtime | Python 3.12+, Node 20+ | Same |
| API | FastAPI, Pydantic | Same, behind a gateway |
| ORM / migrations | SQLAlchemy, Alembic | Same |
| Database | SQLite file | PostgreSQL (managed, or Databricks Lakebase) |
| Features / ML | pandas, scikit-learn (logistic regression champion, gradient-boosting challenger) | Feature store, model registry (MLflow), uplift models |
| LLM | Provider interface; offline template provider by default | Private enterprise model (Bedrock, Vertex, Azure, Databricks Model Serving) |
| Authentication | Local accounts, scrypt hashes, signed session token | Enterprise SSO / OIDC, MFA |
| One-time code delivery | Email via SMTP, with a development fallback | Managed email service; SMS or authenticator when required |
| Frontend | React, TypeScript, Vite, Tailwind, TanStack Query, React Router, Recharts | Same |
| Packaging | One process: API serves the built frontend | Container; Databricks App, or static frontend plus container API |
| Tests | pytest; frontend type-check and build | Add browser end-to-end tests and CI |

Vite single-page app rather than Next.js on purpose: one deployable unit for a laptop, a container or Databricks Apps.

## 6. Data Architecture

**Synthetic-first.** A seeded, deterministic generator produces the whole dataset. Shapes mirror real sources (Synthea-style patient timelines, Medicare PDE-style fills, NPPES-style provider identity with real specialty taxonomy and drug vocabulary), but every person and event is invented. Synthetic provider identifiers are constructed so they cannot match a real one.

Each synthetic person has hidden behaviour traits (patient adherence archetype, channel responsiveness, what kind of outreach works; HCP topic interest, channel responsiveness, fatigue). History and post-send outcomes are drawn from the same traits, so models have real signal to learn and the simulated future is consistent with the past.

Hand-pinned "hero" records guarantee the demo scenarios regardless of seed.

| Entity group | Entities | Notes |
|---|---|---|
| **HCP** | HCP profile | Specialty, organisation, geography, prescribing volume; derived segment, value score, channel affinity |
| **Patient** | Patient profile | Demographics, plan type, stated channel preference; derived risk segment |
| **Relationships** | Patient ↔ HCP attribution; rep ↔ HCP assignment; care manager ↔ patient assignment | Assignments drive data scope |
| **Adherence** | Therapy, medication fill, adherence snapshot | Fills are the source of truth; PDC, MPR and gap days are always derived, never stored independently of fills |
| **Consent** | Consent per patient, purpose and channel, with effective dates | History kept; a hard gate |
| **Content** | Content module, content review | Audience, topic, measure, allowed channels, MLR status, effective and expiry dates |
| **Interaction** | Every touch and its outcome | Stands in for CRM activity; distinguishes earlier baseline outreach from engine-sent |
| **NBA** | Engine cycle, recommendation, candidates considered, message drafts | Losing and gated candidates are kept |
| **Accounts** | User, one-time-code challenge | Role fixed at creation; assignments separate |
| **Governance** | Audit log, model versions, engine settings, feature snapshots | Audit is append-only |
| **Simulation** | Hidden traits | Readable only by the generator and simulator |

Relationship rules: stable ids across all tables; fills chronological and within therapy dates; historical outreach respects the consent and content validity in force at the time; every patient has a therapy, a primary HCP and a care manager; every HCP has a rep.

## 7. NBA Decision Architecture

```
candidates → hard gates → scoring → ranking → selection → rationale → draft → human review → send
```

| Stage | Rule |
|---|---|
| **Candidate generation** | Patient: only if a therapy needs action (elevated risk, a gap, or a refill due soon with a lateness history). Candidates are action × channel × content for the therapy most at risk. HCP: content × channel, excluding content already engaged with or for another specialty. |
| **Eligibility** | The gates in section 8. A gated candidate can never be chosen, whatever its score. |
| **Scoring** | Propensity models estimate response and (patients) fill. Channels that consume staff time carry a cost. Recently sent content is down-weighted. |
| **Ranking** | Within a person: by score. Across people (the work queue): risk × chance of a fill for patients; value × chance of engagement for HCPs. |
| **Channel selection** | Falls out of ranking over eligible candidates. If the stated preferred channel lacks consent, another is used and the recommendation says so. |
| **Timing** | Act now when supply has run out or a prescription is unfilled; ahead of run-out when a refill is due. |
| **Content selection** | Only content matching audience, topic and channel that is MLR-approved and in date. |
| **Rationale** | Built from facts already computed: risk drivers, response counts, model estimates, gate results. Ordered who, action, channel, timing, compliance, and any option held back. |
| **Outcome per person** | Ready for review; blocked (nothing eligible, kept for audit); deferred (contact limit); or no recommendation (none needed). |
| **Personalization** | Wording drafted from the approved content module for the chosen action and channel. |

**CRITICAL: deterministic business rules stay separate from LLM generation.**

The LLM may assist with:

- message generation
- message variants
- a plain-language summary of the rationale

The LLM must **not** determine, influence or override:

- consent eligibility
- MLR eligibility
- frequency-cap compliance
- authorization, role permissions or data access
- which person, action, channel or content was chosen
- any other hard business rule

The LLM receives an action that has already passed every gate. Its output is treated as untrusted: schema-checked, scanned (no unresolved placeholders, no links, no reference to other content, channel length limits, banned patient wording) and discarded if it fails, falling back to templates. A reviewer's own edits go through the same checks. Bulk drafting uses templates so a cycle never depends on a model call.

Models rank; gates decide.

## 8. Compliance & Governance

| Rule | Requirement |
|---|---|
| **MLR approval** | Content may appear in a sendable recommendation only if approved and inside its effective-to-expiry window. Pending, rejected, not-yet-effective and expired content is gated. Applies to HCP and patient content. |
| **Patient consent** | Outreach to a patient on a channel requires a granted consent for that channel in effect on the day. Consent is the patient's to change; history is kept. |
| **Channel permissions** | Content may only be used on the channels it is cleared for, and only for its intended audience. |
| **Frequency caps** | A maximum number of touches per window and a minimum gap between touches, per person. A capped person is deferred, not recorded as blocked. |
| **Re-validation** | Gates run when a recommendation is generated, again at approval, and again at send. A consent withdrawn or an approval lapsed in between stops the message. |
| **Human in the loop** | Nothing is sent without a person approving it. |
| **Auditability** | Every recommendation, gate result, decision, send, response, content decision, account change and settings change is recorded with actor and time. Blocked recommendations remain visible in audit and are never sendable. |
| **Identity minimisation** | Compliance sees gate outcomes without the identity of the person concerned. Patients do not see internal risk scores. HCPs do not see commercial fields about themselves. An HCP sees a patient's adherence only if that patient consents to sharing. |

**Separation of duties (critical).**

ADMIN has broad visibility and administrative access, but ADMIN must **not** approve or reject MLR content.

Only the COMPLIANCE / MLR role may approve or reject MLR content.

This is enforced at the backend authorization layer by a dedicated permission that only the compliance role holds. Hiding a button is not enforcement.

## 9. Authentication & RBAC

**Principle**

```
AUTHENTICATED ACCOUNT → ROLE → PERMISSIONS → DATA SCOPE → UI SCOPE
```

The role is a property of the authenticated account. A persona selector must never be the mechanism that grants access.

**Roles**

| Role (spec name) | Code value |
|---|---|
| ADMIN | `admin` |
| CARE_MANAGER | `care_manager` |
| MEDICAL_REPRESENTATIVE | `medical_rep` |
| COMPLIANCE | `compliance` |
| PATIENT | `patient` |
| HEALTHCARE_PROFESSIONAL | `hcp` |

**Entry flow**

```
Landing page → Log in or Sign up → (first sign-up only) one-time code → role dashboard
```

| Element | Requirement |
|---|---|
| **Landing page** | Public root. Product name, tagline, short explanation, Log in and Sign up. |
| **Login** | Email and password only. The stored role decides the dashboard. No role or persona choice after signing in. A wrong password and an unknown email are indistinguishable. |
| **Signup** | Name, email, password, confirm password, role. Five selectable roles; ADMIN is never selectable or self-creatable. |
| **OTP verification** | A new account is unverified until a one-time code is entered. Codes expire, are single use, are attempt-limited, are stored only as a hash and are removed once used. Delivery is by email; without a mail server a clearly labelled development fallback shows the code and states that nothing was sent. SMS is deferred. |
| **Administrator** | One fixed system account, created by the system, never by sign-up. Its password is supplied through configuration, never hardcoded. |
| **Local POC authentication** | Accounts live server-side in the application database with hashed passwords. The browser holds only a session token. |
| **Session management** | Survives a page refresh. Sign-out ends the session on the server. Disabling an account ends its sessions. The role is read from the account on every request, never from the token or the client. |
| **Role-based routing** | After sign-in each role lands on its own dashboard. Signed-in users are redirected away from login and sign-up. |
| **Protected routes** | Signed out: application addresses lead to login. Signed in without permission: Access Denied. |
| **Permissions** | One role-to-permission map. Endpoints require permissions, never role names. |
| **Assignment model** | Section 10. |

Authentication concerns sit behind interfaces (account repository, one-time-code service and sender, session service, assignment service) so each can be replaced independently.

## 10. Role & Data Scope

| Role | May access | May do | May not |
|---|---|---|---|
| **PATIENT** | Own profile, medications, adherence, consent, messages | Change own consent, respond, confirm refill | Any other patient, any HCP queue, compliance, admin |
| **CARE_MANAGER** | Assigned patients, their 360 and recommendations, approved patient content | Approve, edit, reject, send; log call outcome | Unassigned patients, HCP data, admin functions |
| **MEDICAL_REPRESENTATIVE** | Assigned HCPs, their 360 and recommendations, approved HCP content | Approve, edit, reject, send; log visit outcome | Any patient data, unassigned HCPs, compliance-only or admin functions |
| **HEALTHCARE_PROFESSIONAL** | Own profile, own content inbox, adherence summary of own patients who consent to sharing | Read and respond to content | Other HCPs, patients without sharing consent, commercial fields about themselves |
| **COMPLIANCE** | Content library, blocked and held-back recommendations with identities hidden, audit log, metrics, model metrics | **Approve and reject MLR content** | Review or send recommendations, open patient or HCP profiles, admin functions |
| **ADMIN** | Every module and all data; accounts and assignments; engine settings | Run the engine, change settings, manage accounts and assignments, review recommendations | **Approve or reject MLR content**; change any account's role |

**Assignment model**

```
User → Role → Assigned entities

CARE_MANAGER             → assigned patient ids
MEDICAL_REPRESENTATIVE   → assigned HCP ids
HEALTHCARE_PROFESSIONAL  → own HCP id
PATIENT                  → own patient id
```

- Role decides what kind of thing an account may do. Assignment decides which records.
- **Assignment and role are separate concepts. Changing an assignment must never change the role or its permissions.** No endpoint changes a role.
- A newly verified account receives starting assignments from the synthetic pool; the administrator may change them afterwards. Assignments of existing accounts are not altered by someone else's sign-up.
- Data scope is applied in the query on the server. Out-of-scope records are indistinguishable from missing ones.
- Endpoints for "my own record" take no id from the request.

## 11. UI / Product Principles

- **One platform, not six demos.** One shell and one visual language; the menu and pages follow from the account's permissions.
- **The UI mirrors the server.** It never decides access; removing a UI check must expose nothing.
- **Explain every recommendation.** Who, why this action, why this channel, why now, compliance status, and what was held back, in plain language.
- **Show what lost.** The options considered and why each was gated or outranked.
- **Make compliance visible.** Gate results, blocked items and audit are first-class, not hidden.
- **Honest numbers.** Comparisons are like-for-like; small samples are not charted; model quality is shown as measured.
- **Enterprise tone.** Calm, dense where useful, no decoration that does not carry information.

| Surface | Intent |
|---|---|
| **Navigation** | Derived from one permission-keyed route table. Signed-in name, role and sign-out always visible. |
| **Dashboard** | Recommendations ready and blocked, adherence by measure over time, response by channel, engine versus earlier outreach, why options were held back. Aggregates only. |
| **NBA queue** | Work list ranked by priority, filterable by status. Titled for the role's scope. |
| **Review workflow** | Recommendation detail: rationale, options considered, approved content, editable draft, approve / reject / send, audit trail. Human channels record an outcome afterwards. |
| **Patient 360** | Therapies with days covered and gaps, a fill-coverage timeline, consent, response by channel, outreach history, the open recommendation. |
| **HCP 360** | Segment and value, what topics and channels the HCP engages with, history, the open recommendation. |
| **Compliance experience** | Content needing attention first, with how many recommendations wait on each item; gate outcomes without identities; audit log. |
| **Admin experience** | Everything, plus users and assignments, engine operations, settings, the demo clock. |
| **Patient and HCP portals** | Own record only, in plain language, with their own messages and preferences. |
| **Analytics for technical reviewers** | The loop, the design rules, model metrics against a baseline, live API reference. |

## 12. Production Evolution

| From (POC) | To (production) |
|---|---|
| Local persistence | Managed production database |
| SQLite | PostgreSQL or equivalent |
| Local authentication | Enterprise authentication / SSO (OIDC) |
| Local one-time code | Managed email service, SMS or authenticator, MFA at sign-in |
| Role-to-permission map in code | Role and permission administration backed by the database |
| Local data | Cloud data platform (lakehouse), feature store |
| Synthetic data | Real, governed, de-identified or consented data: claims, pharmacy, CRM, consent management |
| Simulated engagement | Real orchestration: CRM, marketing automation, patient-support hub, telephony |
| Rule score plus simple models | Uplift modelling, timing optimisation, proper attribution, monitored retraining |
| Offline template / public LLM | Secure private enterprise LLM inside the client's environment |
| Local deployment | Cloud infrastructure, containers, CI/CD |
| Basic audit log | Enterprise governance: monitoring, alerting, retention, HIPAA / TCPA controls, full MLR workflow |

Every row above is a swap behind an existing interface, not a redesign. Keeping that true is a standing requirement.

## 13. Critical Architectural Decisions

Future work must preserve these.

1. **Synthetic-first POC.** No real patient or HCP data, ever, in this codebase.
2. **Communication decisions, not clinical decisions.**
3. **An intelligent layer on top of the CRM**, never a replacement.
4. **Patient adherence leads**; HCP engagement is the second audience on the same engine.
5. **Deterministic compliance, consent and frequency gates**, evaluated at generation, at approval and at send.
6. **The LLM only words an already-eligible action.** It never decides eligibility, authorization or selection; its output is validated and discarded on failure.
7. **Model-agnostic LLM abstraction** with an offline default.
8. **Human-in-the-loop review** before anything is sent.
9. **Every recommendation is explained and audited**; blocked ones stay visible and unsendable.
10. **Account-based authentication.** No persona picker as an access mechanism.
11. **RBAC by permission, enforced in the API**, not only in the UI. No authorization by role name outside the permission map.
12. **Assignment is separate from role.** Changing assignments never changes role or permissions. No role-change endpoint.
13. **Admin has broad visibility and control, but cannot approve or reject MLR content.**
14. **The Compliance role alone owns MLR approval and rejection.**
15. **Patient and HCP data scoping on the server**; out-of-scope looks like not-found; identity minimised per role.
16. **Hidden simulation traits are never readable by the engine, features or models.**
17. **Point-in-time features**: training and live scoring share one code path; no look-ahead.
18. **Explainable champion model**; more complex models are challengers until explanation is solved.
19. **Like-for-like measurement** of engine versus baseline.
20. **One recommendation per person per cycle.**
21. **One deployable unit**, and every external dependency behind an interface, so the POC stays production-migratable.
22. **One-time codes by email only** until SMS is explicitly requested; never claim a message was sent when it was not.
23. **No secret in the repository.** Passwords, signing secrets and API keys have no default in code and never appear in source, tests, documentation or commit history. They come from the environment or a git-ignored `.env`; documentation names the variable, not the value.
