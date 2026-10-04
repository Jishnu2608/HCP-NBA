# Demo script

About 14 minutes. Start from the seeded state (Engine page, "Reset to seeded data", or `python -m app.datagen` then `python -m app.cycle --retrain`). Demo date starts at 30 September 2026.

Every step signs in with a real account: email and password, no role picker. Demo accounts share the password in `NBA_DEMO_PASSWORD`; the administrator is `admin@admin.com` with the password in `NBA_ADMIN_PASSWORD`. Both are in the git-ignored `.env` file, not in this repository.

Tip: the session lives in a browser cookie, so all tabs of one browser share one account. To show two sides of one interaction at once (for example care manager and patient), use a normal window and a private window, or two browser profiles.

## 1. The problem and the idea (1 minute)

Landing page. One engine, two audiences, six roles. Everything is synthetic. The account you sign in with decides what you see.

## 2. Patient adherence: the lead story (4 minutes)

Log in: `cm01@nba.demo` (Care Manager)

1. *Adherence queue.* Patients who need outreach now, ranked. Point at the counts: ready, blocked.
2. Open *My patients*, search **Margaret Doyle** (PAT_00001), open the profile.
   - Coverage timeline: gaps between fills widen month by month; 16 days without supply now.
   - Days covered 75%, below the 80% threshold. High risk.
   - Consent on record; she answered both earlier text messages.
3. Open her recommendation.
   - *Why this recommendation*: who, action, channel, timing, compliance. Every line is a fact from her data.
   - *Options the engine considered*: the alternatives that lost, and the ones a gate stopped (rejected and pending content). "Models rank, gates decide."
   - Edit the draft. Try typing a link or the word "cure": the same wording checks that apply to the model apply to a person.
   - **Approve and send.** Audit trail now shows generated, approved, sent.

Log in (second tab): `pat00001@nba.demo` (Margaret Doyle, Patient)

4. *Messages*: the text is there. Press **I have refilled**.
5. *My medications*: gap closed.

Contrast patients, all assigned to `cm01`:

| Patient | What it shows |
|---|---|
| Daniel Reyes (PAT_00002) | Prefers text but never consented to it. The engine uses another channel and says why. |
| George Whitman (PAT_00004) | High risk, opted out of everything. Recommendation is **blocked**; visible in audit, never sendable. |
| Rosa Delgado (PAT_00005) | $55 copay, three reminders ignored. The engine recommends cost support, not a fourth reminder. |
| Samuel Nguyen (PAT_00006) | Prescription never filled. Education, not a "refill" reminder. |
| Helen Baker (PAT_00003) | Adherent. No recommendation: the engine does not nudge people who do not need it. |

## 3. Consent is a hard gate (1 minute)

Log in: `pat00005@nba.demo` (Rosa Delgado). *Consent & preferences*: switch off phone contact.

Back as `cm01@nba.demo`: open Rosa's recommendation and approve. It is refused and becomes blocked, because consent is re-checked at approval and again at send.

## 4. HCP engagement and MLR (3 minutes)

Log in: `rep01@nba.demo` (Medical Representative)

1. *HCP queue.* Open **Dr. Elena Marsh** (HCP_0001): cardiologist, high value, answers email, declines rep visits. Recommendation: an approved study summary by email. Approve and send.
2. Open **Dr. Rajan Iyer** (HCP_0002): endocrinologist who engaged with the diabetes outcomes summary. The recommendation carries a warning: a better option was held back because the updated summary is still pending MLR.

Log in: `compliance1@nba.demo` (Compliance / MLR Reviewer)

3. *Content & MLR* → *Needs attention*. The pending summary shows how many recommendations are waiting on it. Review and **approve**.
4. *Gate outcomes*: blocked and held-back recommendations with identities hidden. Compliance sees outcomes, not people.

Log in: `admin@admin.com` (Administrator) → *Engine* → **Run cycle now**.

As `rep01@nba.demo`: Dr. Iyer's recommendation is now the newly approved summary.

Log in: `hcp0001@nba.demo` (Dr. Elena Marsh): *Inbox* shows the email; *My patients* shows adherence only for patients who consented to sharing.

## 5. The loop closes (2 minutes)

Log in: `admin@admin.com`

1. *Engine* → **Send top 400** (a team working its queues), then **Advance 7 days**.
2. *Dashboard*:
   - Engine versus earlier outreach, like for like.
   - Response rate by channel, engine against baseline.
   - Adherent share by measure, now with a second date.
   - Why options were held back.
3. *Audit log*: filter by event. Every recommendation, gate result, decision, send, response and settings change, with the actor.

## 6. For technical reviewers (1 minute)

*Under the hood*: the loop, the design rules, model holdout metrics against a no-model baseline and a boosted-tree challenger, live API reference.

## 7. Accounts and access (2 minutes)

1. As `admin@admin.com` → *Invitations*: invite an email you can read as an **HCP**. (Without email configured, the link appears in a development box.)
2. Sign out, open the invitation link. The role is shown and cannot be changed; the email is fixed. Enter name, date of birth and a password; enter the emailed code. You land on the HCP inbox with the blue **Verified** mark next to your name.
3. As that HCP → *Team*: only Medical Representative and Care Manager can be invited. Invite a care manager the same way; the administrator's *Users & assignments* now shows the chain Administrator → HCP → Care Manager.
4. *Patient sign-up* on the landing page creates a patient account with no role choice and no Verified mark.
5. Type `/admin` or `/hcps` in the address bar as the patient: Access Denied. The API refuses the data too.
6. As `admin@admin.com` → *Users & assignments*: role and permissions are locked; assignments and status are editable. Note what the administrator cannot do: approve content. Only Compliance can.

## Role access proof

- `rep02@nba.demo` cannot open HCP_0001 (returns "not found"); no rep can open any patient.
- A patient sees only their own record, without internal risk scores.
- An HCP sees their own profile without commercial fields (segment, value score).
- Compliance cannot approve a recommendation; only Compliance can approve content; the administrator cannot.
- Changing an account's assignments never changes its role or permissions.
- A signed-out or disabled account's session stops working immediately.
- An HCP cannot invite an MLR reviewer or another HCP, even with a hand-made request: the API answers 403.
