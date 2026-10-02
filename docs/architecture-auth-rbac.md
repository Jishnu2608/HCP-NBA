# Authentication and access architecture

How a request gets from "someone opened the site" to "this row of data", and where each decision is made.

```
Authentication            who is this?            backend/app/auth/
      ↓
Authenticated account     a row in `user`         api/deps.py: get_current_user
      ↓
Role                      stored on the account   user.role (never from the client)
      ↓
Permission set            what may they do?       core/permissions.py: ROLE_PERMISSIONS
      ↓
Data scope                which rows?             core/rbac.py + assignment tables
      ↓
Existing NBA functionality                        api/*, nba/*, engagement/*
      ↓
UI scope                  what to show            frontend/src/routes.tsx (mirrors the server)
```

The principle: **the persona is a property of the authenticated account**, not something chosen after signing in. The old "choose a demo persona" screen and its passwordless endpoint are gone.

## 1. Authentication

| Step | Endpoint | Result |
|---|---|---|
| Register | `POST /api/auth/signup` | Account created with `status=pending`, `verified=false`. A one-time code is issued. Returns a short-lived *verification token*, not a session. |
| Verify | `POST /api/auth/verify-otp` | Code checked. Account becomes `active` and `verified`, starting data is assigned, a session is issued. |
| Resend | `POST /api/auth/resend-otp` | New code, after a 30-second cool-down. The earlier code stops working. |
| Sign in | `POST /api/auth/login` | Email and password. Returns a session and the account (role, permissions, home route). |
| Sign out | `POST /api/auth/logout` | Session revoked on the server. |
| Who am I | `GET /api/auth/me` | The account as the server sees it. |

Sign-in outcomes:

| Situation | Response |
|---|---|
| Unknown email or wrong password | 401 `invalid_credentials` (identical for both, so emails cannot be probed) |
| Correct password, email not verified | 403 `verification_required` with a verification token; no session |
| Disabled account | 403 `account_disabled` |

### Components (`backend/app/auth/`)

| File | Interface | Local implementation | Replace with |
|---|---|---|---|
| `repository.py` | `UserRepository` | `SqlUserRepository` (SQLAlchemy; SQLite today) | PostgreSQL by connection string; a directory service by a new class |
| `otp.py` | `OTPService` rules + `OtpSender` delivery | `EmailOtpSender` (SMTP) and `LocalDemoOtpSender` (development fallback) | SMS, authenticator app, an MFA provider: a new `OtpSender` |
| `sessions.py` | `SessionService` | Signed token with user id and token version | Server-side session store or an identity provider's tokens |
| `service.py` | `signup`, `verify_otp`, `resend_otp`, `login`, `logout` | Orchestrates the above | Unchanged |
| `provisioning.py` | `AssignmentService` | Auto-assignment, admin edits, survive-reset | Unchanged |

### One-time code rules

- 6 random digits. Only an HMAC hash is stored (`otp_challenge` table).
- Expires after 10 minutes. Five wrong attempts invalidate it.
- Single use: the row is deleted on success, on expiry and on lock-out.
- Delivery is email. With SMTP configured the code is sent and never returned by the API. Without it, and only while demo mode is on, the code is returned to the verify page and shown in a box that says no email was sent.

### Sessions

The token carries the user id and the account's `token_version`. It does **not** carry authority: on every request the account is loaded from the database and the role is read from there. Sign-out and disable increment `token_version`, which invalidates every token issued before. A token with an added `role` claim, or signed with another key, gains nothing (covered by tests).

The browser keeps the token in `sessionStorage` through one module, `frontend/src/session.ts`. It survives a refresh and is separate per tab.

### The administrator

One fixed account (`admin@admin.com`), created by the data generator and ensured at application start-up. The sign-up endpoint rejects the administrator role, and `GET /api/auth/roles` does not list it.

## 2. RBAC by permission

`backend/app/core/permissions.py` holds the `Permission` enum and one map, `ROLE_PERMISSIONS`. Every protected endpoint declares the permission it needs with `require_permission(...)`. A test fails if a router or the scoping code compares role names, and another fails if any `/api` route outside a short public list answers an anonymous caller.

| Role | Permissions |
|---|---|
| admin | `patient:read:all`, `hcp:read:all`, `nba:read:all`, `nba:review:patient`, `nba:review:hcp`, `content:read:all`, `audit:read`, `analytics:read`, `models:read`, `engine:operate`, `config:manage`, `user:manage` |
| compliance | `content:read:all`, `content:approve`, `nba:read:gated`, `audit:read`, `analytics:read`, `models:read` |
| medical_rep | `hcp:read:assigned`, `nba:read:hcp_assigned`, `nba:review:hcp`, `content:read:approved_hcp` |
| care_manager | `patient:read:assigned`, `nba:read:patient_assigned`, `nba:review:patient`, `content:read:approved_patient` |
| hcp | `self:profile:read`, `self:inbox`, `self:patients:read` |
| patient | `self:profile:read`, `self:inbox`, `self:consent:manage` |

The administrator deliberately lacks `content:approve`: MLR approval stays with Compliance, so whoever runs the engine cannot clear their own content.

## 3. Data scope

Permissions say what kind of thing an account may do. Assignments say to which records.

```
User → Role → Assigned entities

care_manager  → care_manager_patient rows   (patient ids)
medical_rep   → rep_hcp rows                (HCP ids)
hcp           → user.hcp_id                 (own record)
patient       → user.patient_id             (own record)
```

`backend/app/core/rbac.py` turns a permission plus assignments into a SQL filter:

| Permission form | Rows |
|---|---|
| `*:read:all` | every row |
| `*:read:assigned` | rows linked to the account in the assignment tables |
| `nba:read:gated` | blocked or held-back recommendations, with the person's identity removed |
| `self:*` | the single record linked to the account; endpoints under `/api/me` take no id from the request |

Out-of-scope ids return 404, so they cannot be probed. An account with no linked record gets `no_assignment` and the UI says so.

### Assignments are not permissions

- A new account is given assignments automatically on verification (see README).
- The administrator can replace them at any time: `PUT /api/admin/users/{id}/assignments`.
- Neither path reads or writes `user.role`. There is no endpoint that changes a role.
- Seeded demo assignments are never altered by a sign-up.
- A demo reset rebuilds the synthetic data and restores registered accounts with their assignments.

## 4. UI

`frontend/src/routes.tsx` is one table: each route names the permissions that allow it. The navigation menu and the route guards are both derived from it, using the permission list returned by `GET /api/auth/me`.

| Situation | Behaviour |
|---|---|
| Signed out, any application address | Redirect to `/login` |
| Signed in, `/`, `/login`, `/signup` | Redirect to the account's home route |
| Signed in, address the role may not open | Access Denied page |
| Signed in, unknown address | Page not found |
| Sign-in after being redirected | Returns to the requested page only if the account may open it |

The UI check is for presentation. Removing or bypassing it shows empty pages, because the API refuses the data.

## 5. Where the existing NBA functionality plugs in

Nothing in the engine, gates, drafting, simulator or analytics changed. The routers that expose them now ask for permissions:

| Area | Permission |
|---|---|
| Work queue, recommendation detail | any `nba:read:*`; acting needs `nba:review:patient` or `nba:review:hcp` plus the row being in scope |
| Patient 360 / HCP 360 | `patient:read:*` / `hcp:read:*` |
| Content library | `content:read:*`; review needs `content:approve` |
| Audit log, metrics, models | `audit:read`, `analytics:read`, `models:read` |
| Engine operations, settings | `engine:operate`, `config:manage` |
| Users and assignments | `user:manage` |
| Portal | `self:*` |

## 6. Tests

`backend/tests/test_auth.py` and `backend/tests/test_permissions.py` cover: sign-up validation, duplicate email, code success / wrong / expired / reused / locked / resend, email delivery and fallback, sign-in success and failure, unverified and disabled accounts, sign-out revoking the token, forged tokens, automatic assignment per role, cross-role and cross-entity denial, administrator account management, assignment changes leaving role and permissions untouched, reset preserving accounts, and the full flow sign-up → code → dashboard → sign-out → sign-in.
