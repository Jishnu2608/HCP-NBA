# Authentication, onboarding and access architecture

How a request gets from "someone opened the site" to "this row of data", and where each decision is made. The browser is treated as untrusted: every decision below is made on the server, and the web app only mirrors it.

```
Authentication            who is this?            backend/app/auth/
      ↓
Authenticated account     a row in `user`         api/deps.py: get_current_user (session cookie)
      ↓
Role                      stored on the account   set by patient sign-up or by an invitation
      ↓
Permission set            what may they do?       core/permissions.py: ROLE_PERMISSIONS
      ↓
Data scope                which rows?             core/rbac.py + assignment tables
      ↓
Existing NBA functionality                        api/*, nba/*, engagement/*
      ↓
UI scope                  what to show            frontend/src/routes.tsx (mirrors the server)
```

## 1. How accounts come into existence

| Who | How | Role comes from |
|---|---|---|
| Patient | Public sign-up at `/signup` | The server (always `patient`; the request has no role field) |
| HCP, Medical Representative, Care Manager, Compliance / MLR | Invitation link `/invite/<token>` | The stored invitation |
| Administrator | Created by the system from configuration | Fixed |
| Seeded demo staff and patients | The data generator | Seed data |

### Onboarding authority (who may invite whom)

| Inviter | May invite |
|---|---|
| Admin | HCP, Medical Representative, Care Manager, Compliance / MLR |
| HCP | Medical Representative, Care Manager |
| Medical Representative, Care Manager, Compliance, Patient | Nobody |

This is an onboarding authority, not an organisational hierarchy, and it grants no data access. It is expressed as four permissions (`invite:hcp`, `invite:medical_rep`, `invite:care_manager`, `invite:compliance`) in the one `ROLE_PERMISSIONS` map; `can_invite(user, role)` is the only check, and `InvitationService` (`auth/invitations.py`) calls it on every operation. An HCP asking the API to invite an MLR gets 403 `invite_forbidden` whatever the UI shows.

### Invitation lifecycle

| Step | Endpoint | What happens |
|---|---|---|
| Invite | `POST /api/invitations` `{email, role}` | Authority checked. An invitation row stores a SHA-256 hash of a 256-bit random token, the email, the role, the inviter and the inviter's role, and a 72-hour expiry. The email is sent (HTML + plain text). An earlier pending invitation for the same email is revoked if the caller sent it (or is the administrator); otherwise 409. |
| Open link | `POST /api/invitations/lookup` `{token}` | Public, rate-limited. Returns who invited whom as what, until when. Expired / used / revoked / invalid each have their own code and message; the token is never echoed. |
| Accept | `POST /api/invitations/accept` `{token, name, date_of_birth, password, confirm_password}` | Public, rate-limited. There is no email or role field: both come from the invitation. Checks: still pending and in date, the inviter still active and still authorised, no existing account, age at least `NBA_MINOR_AGE`. Creates a *pending* account and emails a code to the invited address. The invitation is **not** accepted yet. |
| Verify | `POST /api/auth/verify-otp` | The code proves control of the invited inbox. In one transaction the invitation is re-checked, marked accepted, and the account is activated and marked professionally verified, with `invited_by_user_id` recorded. |
| Reissue | `POST /api/invitations/{id}/reissue` | New token and deadline; the old token stops working. Administrator for any invitation, others for their own. |
| Revoke | `POST /api/invitations/{id}/revoke` | Pending invitations only. Disabling an account also revokes the invitations it sent. |
| List | `GET /api/invitations` | Administrator: all (`invitation:read:all`). Others: only the ones they sent. Someone else's invitation id returns 404. |

Lineage: `user.invited_by_user_id` links each invited account to its inviter, so the administrator sees chains such as Administrator → HCP → Medical Representative (`GET /api/admin/users/{id}` → `lineage`).

### Verification: three separate facts

| Fact | Column | Set by |
|---|---|---|
| Email verified | `user.verified` (API: `email_verified`) | Entering the one-time code |
| Professionally verified | `user.professionally_verified`, `verification_source` | A completed invitation (`invitation`), or platform-provisioned seed staff (`system`). Never patients, never the administrator. No endpoint accepts it. |
| Minor | not stored | Derived from `date_of_birth` in `core/age.py` |

The web app shows the blue Verified mark (`VerifiedBadge`) only when the server says `professionally_verified` is true.

### Date of birth and age

Required for patient sign-up and invitation acceptance. Validated on the server (real calendar date, not in the future, not older than 120). Stored on the account; never returned to a browser. `core/age.py` is the single place age and minor status are computed (threshold `NBA_MINOR_AGE`, default 18, against the real date, not the demo clock). Professionals under the threshold are refused; minor patients may register and are shown to administrators as "Minor". The synthetic patient record keeps its own birth date (a model feature).

## 2. Authentication and sessions

| Step | Endpoint | Result |
|---|---|---|
| Patient sign-up | `POST /api/auth/signup` | Pending account, code emailed, HttpOnly verification cookie. If the email already has an account the response is identical, the owner is emailed a notice, and no code ever works (no enumeration). |
| Verify | `POST /api/auth/verify-otp` `{code}` | Account active, starting data assigned, session cookie set. |
| Resend | `POST /api/auth/resend-otp` | New code after a 30-second cool-down. |
| Sign in | `POST /api/auth/login` `{email, password}` | Session cookie and the account. No role choice. |
| Sign out | `POST /api/auth/logout` | This browser's session deleted on the server. |
| Who am I | `GET /api/auth/me` | The account as the server sees it. |

### Sessions (`auth/sessions.py`)

- A session is a row in `user_session`; the browser holds a random 256-bit token in the `nba_session` cookie (HttpOnly, SameSite=Lax, Secure outside a local run). Only the token's SHA-256 hash is stored.
- Idle timeout 60 minutes, absolute lifetime 8 hours.
- Signing in always creates a new session and deletes any session the browser presented (no session fixation). Signing out ends this browser only. Disabling an account, rotating passwords and a demo reset end all sessions.
- Every request loads the account behind the session; the role is read from the database, never from the cookie or the request.
- The verification step uses a separate short-lived signed token in the `nba_verify` cookie (HttpOnly, SameSite=Strict, path `/api/auth`). It can only be used to enter or resend the code.

### Request protection (`core/http_security.py`)

- **CSRF:** signed double-submit token. The server sets a readable `nba_csrf` cookie; the web app echoes it in `X-CSRF-Token` on every POST/PUT/PATCH/DELETE, including sign-in and sign-up. Missing or mismatched: 403 `csrf_failed`.
- **Origin:** when the browser sends `Origin` (or `Referer`), it must be this host or one in `NBA_ALLOWED_ORIGINS`.
- **Rate limits** (`core/ratelimit.py`, stored in the `rate_limit_hit` table, keys only as keyed hashes): 429 `rate_limited`, "Too many attempts. Please try again later.", `Retry-After`. No permanent lockout.

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

- **Generic sign-in failure:** unknown email, wrong password and disabled account all return 401 "Incorrect email or password."
- **Headers:** Content-Security-Policy (scripts only from this site plus the hashed theme bootstrap; no framing), `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` (invitation tokens never leak in a Referer), `Permissions-Policy`, HSTS outside a local run, `Cache-Control: no-store` on the API.
- **Errors:** unhandled errors return a generic 500 body and are logged server-side; validation errors list field names but never echo submitted values.
- **Input:** every request body extends `StrictBody` (`extra="forbid"`): fields such as `role`, `user_id`, `professionally_verified` or `is_minor` are rejected with 422, never silently ignored. Strings have length limits; engine settings are checked against the shape and bounds of their defaults.
- **Logs:** no passwords, codes, session or invitation tokens. The access log rewrites `/invite/<token>` to `/invite/[redacted]`.
- **API reference** (`/api/docs`) is served only in a local run.

### One-time code rules

- 6 random digits; only an HMAC hash (with a key derived for this purpose) is stored.
- Expires after 10 minutes; five wrong attempts invalidate it; single use.
- Delivered by email. Without a mail server, and only in a local run with demo mode on, the code is shown in a box that says no email was sent. Invitation links follow the same rule (`dev_link` shown to the inviter).

## 3. RBAC by permission

`core/permissions.py` holds the `Permission` enum and one map, `ROLE_PERMISSIONS`. Every protected endpoint declares the permission it needs with `require_permission(...)`. Tests fail if a router or the scoping code compares role names, or if any `/api` route outside a short public list answers an anonymous caller.

| Role | Permissions |
|---|---|
| admin | `patient:read:all`, `hcp:read:all`, `nba:read:all`, `nba:review:patient`, `nba:review:hcp`, `content:read:all`, `audit:read`, `audit:read:identified`, `analytics:read`, `models:read`, `engine:operate`, `config:manage`, `user:manage`, `invite:hcp`, `invite:medical_rep`, `invite:care_manager`, `invite:compliance`, `invitation:read:all` |
| compliance | `content:read:all`, `content:approve`, `nba:read:gated`, `audit:read`, `analytics:read`, `models:read` |
| medical_rep | `hcp:read:assigned`, `nba:read:hcp_assigned`, `nba:review:hcp`, `content:read:approved_hcp` |
| care_manager | `patient:read:assigned`, `nba:read:patient_assigned`, `nba:review:patient`, `content:read:approved_patient` |
| hcp | `self:profile:read`, `self:inbox`, `self:patients:read`, `invite:medical_rep`, `invite:care_manager` |
| patient | `self:profile:read`, `self:inbox`, `self:consent:manage` |

The administrator deliberately lacks `content:approve`: MLR approval stays with Compliance.

## 4. Data scope

```
User → Role → Assigned entities

care_manager  → care_manager_patient rows   (patient ids)
medical_rep   → rep_hcp rows                (HCP ids)
hcp           → user.hcp_id                 (own record)
patient       → user.patient_id             (own record)
```

| Permission form | Rows |
|---|---|
| `*:read:all` | every row |
| `*:read:assigned` | rows linked to the account in the assignment tables |
| `nba:read:gated` | blocked or held-back recommendations with the person removed: no name, no segment, no target id, no drafts (they address the person by name), masked audit detail |
| `self:*` | the single record linked to the account; `/api/me` endpoints take no id from the request |

Out-of-scope ids return 404. The audit log masks account emails and patient / HCP ids for readers without `audit:read:identified`. Opening a Patient 360 or HCP 360 is itself audited (`profile_viewed`).

### Assignments are not permissions

- New accounts get starting assignments on verification (unchanged by invitations: the inviting HCP is not assigned to the people they invite).
- The administrator can replace assignments: `PUT /api/admin/users/{id}/assignments`.
- Neither path writes `user.role`. No endpoint changes a role.

## 5. What survives a demo reset

`POST /api/admin/reset` rebuilds the synthetic data. Carried across: registered and invited accounts with their assignments, invitations with lineage (references re-linked by email, because account ids can change), and the security audit rows (accounts, sign-ins, invitations). All sessions end; the caller gets a new session cookie.

## 6. UI

`frontend/src/routes.tsx` is one table: each route names the permissions that allow it. Menu and guards derive from it using the permission list from `GET /api/auth/me`. The session cookie is invisible to JavaScript, so the app asks `/auth/me` on load. `session.ts` stores only display data (the pending sign-up's email and timers) and theme preferences.

| Situation | Behaviour |
|---|---|
| Signed out, any application address | Redirect to `/login` |
| Signed in, `/`, `/login`, `/signup` | Redirect to the account's home |
| `/invite/<token>` | Own page whether signed in or not; signed in as another account shows "Sign out to accept" |
| Signed in, address the role may not open | Access Denied |
| Unknown address | Page not found |

UI checks are presentation only. Removing or bypassing them shows nothing new: the API refuses the data.

## 7. Tests

`test_auth.py`, `test_invitations.py`, `test_security.py` and `test_permissions.py` cover, among others: patient-only sign-up and DOB validation; forged `role` / verification / minor / status fields rejected; the full 6 × 6 inviter-by-target matrix through the API; HCP → MLR refused; invitation email content; email binding; expiry, single use, reissue, revoke, inviter disabled, revoked between acceptance and code; minor professional refused; lineage; badge only after invitation; cookie flags; CSRF and origin; session fixation, logout, idle and absolute expiry; reset ending sessions; rate limits; headers; generic 500s; no secrets in logs; compliance redaction.
