"""Forgot password (auth/recovery.py): email -> code -> reset authorization -> new password,
for every role except the administrator, who is excluded at every step on the server."""

import re
import secrets
from datetime import timedelta

import pytest
from conftest import (
    ADMIN_LOGIN,
    DEMO_PASSWORD,
    ApiClient,
    cookie_header,
    new_session,
    sign_in,
)
from conftest import login as login_as
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import cycle
from app.auth import recovery
from app.core import ratelimit
from app.core.config import get_settings
from app.core.db import get_db
from app.core.permissions import permissions_for
from app.core.security import verify_password
from app.datagen.generate import GenConfig, generate
from app.main import app
from app.models import AuditLog, PasswordReset, User, UserSession
from app.models.enums import AccountStatus, Role
from app.models.tables import utcnow

BASE = "/api/auth/password-reset"
# Generated per run, like the other test passwords (no credential-like literal in the repo).
NEW_PASSWORD = f"Rp-{secrets.token_urlsafe(10)}7"
WEAK = "s" * 5 + "1"


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GenConfig(seed=73, n_patients=120, n_hcps=24, n_reps=3, n_care_managers=2))
    cycle.run(db)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    client = ApiClient(app)
    # Compliance accounts exist only by invitation: create one through the real flow.
    sign_in(client, "compliance1")
    yield client, db
    app.dependency_overrides.clear()
    db.close()


class Mailbox:
    """Stands in for the SMTP transport: never sends anything."""

    def __init__(self, fail: bool = False):
        self.messages, self.fail = [], fail

    def deliver(self, message):
        if self.fail:
            raise ConnectionError("mail server unreachable")
        self.messages.append(message)

    def codes_for(self, email: str) -> list[str]:
        return [
            re.match(r"(\d{6}) ", m["Subject"]).group(1)
            for m in self.messages
            if m["To"] == email and "reset code" in m["Subject"]
        ]

    def to(self, email: str) -> list:
        return [m for m in self.messages if m["To"] == email]


@pytest.fixture
def mailbox(monkeypatch):
    box = Mailbox()
    monkeypatch.setattr(recovery, "get_mailer", lambda settings=None: box)
    return box


def email_of(db, username: str) -> str:
    if username == "admin":
        return ADMIN_LOGIN[0]
    return db.scalar(
        select(User.email).where(User.username.in_((username, f"{username}@nba.demo")))
    )


def start(client, email: str):
    response = client.post(f"{BASE}/request", json={"email": email})
    return response, response.cookies.get(recovery.TRANSACTION_COOKIE)


def verify_code(client, transaction: str | None, code: str):
    response = client.post(
        f"{BASE}/verify",
        json={"code": code},
        headers=cookie_header(**{recovery.TRANSACTION_COOKIE: transaction}),
    )
    return response, response.cookies.get(recovery.AUTHORIZATION_COOKIE)


def finish(client, transaction, authorization, password=NEW_PASSWORD, confirm=None):
    return client.post(
        f"{BASE}/complete",
        json={"password": password, "confirm_password": confirm or password},
        headers=cookie_header(
            **{
                recovery.TRANSACTION_COOKIE: transaction,
                recovery.AUTHORIZATION_COOKIE: authorization,
            }
        ),
    )


def code_of(response) -> str:
    return response.json()["detail"]["code"]


def fresh_account(db, role: str, suffix: str) -> User:
    """A separate verified account with a known password, so tests do not share state."""
    from app.core.security import hash_password

    user = User(
        username=f"{role}-{suffix}@example.org",
        email=f"{role}-{suffix}@example.org",
        display_name=f"Test {role}",
        password_hash=hash_password(DEMO_PASSWORD),
        role=role,
        verified=True,
        status=AccountStatus.ACTIVE,
        source="invitation",
    )
    db.add(user)
    db.commit()
    return user


# --- Request: validation and no enumeration ------------------------------------------------


@pytest.mark.parametrize("bad", ["", "not-an-email", "a@b", "two@@example.org"])
def test_invalid_email_format_is_refused(env, bad):
    client, _ = env
    response = client.post(f"{BASE}/request", json={"email": bad})
    assert response.status_code == 422


def test_every_address_gets_the_same_public_answer(env, mailbox):
    client, db = env
    disabled = fresh_account(db, Role.CARE_MANAGER, "disabled")
    disabled.status = AccountStatus.DISABLED
    unverified = fresh_account(db, Role.PATIENT, "unverified")
    unverified.verified = False
    db.commit()
    eligible = email_of(db, "cm01")
    answers = {}
    for email in (eligible, "nobody@example.org", ADMIN_LOGIN[0], disabled.email, unverified.email):
        response, transaction = start(client, email)
        answers[email] = (response.status_code, response.json(), bool(transaction))
    assert len({str(a) for a in answers.values()}) == 1
    status, body, has_cookie = answers[eligible]
    assert status == 202 and has_cookie and body["message"] == recovery.GENERIC
    # Only the eligible account was emailed.
    assert [m["To"] for m in mailbox.messages] == [eligible]


def test_response_never_carries_a_code_or_token_when_mail_is_configured(env, mailbox):
    client, db = env
    response, _ = start(client, email_of(db, "rep01"))
    assert "dev_otp" not in response.json() and response.json()["delivery"] == "email"
    assert "nba_reset" not in response.text


# --- Full recovery for every non-admin role ----------------------------------------------


@pytest.mark.parametrize(
    "role", [Role.PATIENT, Role.CARE_MANAGER, Role.HCP, Role.MEDICAL_REP, Role.COMPLIANCE]
)
def test_every_non_admin_role_can_recover(env, mailbox, role):
    client, db = env
    user = fresh_account(db, role, "recover")
    before_role, before_permissions = user.role, permissions_for(user.role)
    session = login_as(client, user.email, DEMO_PASSWORD).cookies.get("nba_session")
    assert session

    _, transaction = start(client, user.email)
    (code,) = mailbox.codes_for(user.email)
    verified, authorization = verify_code(client, transaction, code)
    assert verified.status_code == 200 and authorization
    done = finish(client, transaction, authorization)
    assert done.status_code == 200, done.text
    assert done.json()["reset"] is True and "nba_session" not in done.json()

    db.refresh(user)
    assert verify_password(NEW_PASSWORD, user.password_hash)
    assert NEW_PASSWORD not in user.password_hash
    assert login_as(client, user.email, DEMO_PASSWORD).status_code == 401
    assert login_as(client, user.email, NEW_PASSWORD).status_code == 200
    # Role and permissions untouched; every earlier session ended.
    assert user.role == before_role and permissions_for(user.role) == before_permissions
    assert client.get("/api/auth/me", headers=cookie_header(nba_session=session)).status_code == 401
    # A confirmation was emailed; no secret reached the audit trail.
    assert any("password was changed" in m["Subject"] for m in mailbox.to(user.email))
    rows = db.scalars(select(AuditLog).where(AuditLog.action.like("password_reset%"))).all()
    text = " ".join(f"{r.reason} {r.detail}" for r in rows)
    assert code not in text and NEW_PASSWORD not in text and transaction not in text


# --- Codes ---------------------------------------------------------------------------------


def test_wrong_codes_lock_the_transaction(env, mailbox):
    client, db = env
    user = fresh_account(db, Role.PATIENT, "wrong")
    _, transaction = start(client, user.email)
    (code,) = mailbox.codes_for(user.email)
    wrong = "000000" if code != "000000" else "111111"
    for left in (4, 3, 2, 1):
        response, _ = verify_code(client, transaction, wrong)
        assert code_of(response) == "otp_incorrect" and f"{left} attempts" in response.text
    response, _ = verify_code(client, transaction, wrong)
    assert response.status_code == 429 and code_of(response) == "otp_locked"
    # The right code no longer works either.
    response, authorization = verify_code(client, transaction, code)
    assert response.status_code == 429 and authorization is None


def test_expired_code_is_refused(env, mailbox, monkeypatch):
    client, db = env
    user = fresh_account(db, Role.PATIENT, "expired")
    _, transaction = start(client, user.email)
    (code,) = mailbox.codes_for(user.email)
    later = utcnow() + timedelta(minutes=get_settings().otp_ttl_minutes, seconds=1)
    monkeypatch.setattr(recovery, "utcnow", lambda: later)
    response, _ = verify_code(client, transaction, code)
    assert code_of(response) == "otp_expired"


def test_code_is_single_use(env, mailbox):
    client, db = env
    user = fresh_account(db, Role.PATIENT, "reuse")
    _, transaction = start(client, user.email)
    (code,) = mailbox.codes_for(user.email)
    assert verify_code(client, transaction, code)[0].status_code == 200
    again, authorization = verify_code(client, transaction, code)
    assert again.status_code == 400 and authorization is None


def test_new_request_supersedes_the_earlier_transaction(env, mailbox):
    client, db = env
    user = fresh_account(db, Role.HCP, "supersede")
    _, first = start(client, user.email)
    _, second = start(client, user.email)
    first_code, second_code = mailbox.codes_for(user.email)
    response, _ = verify_code(client, first, first_code)
    assert code_of(response) == "reset_expired"
    # A code is bound to its own transaction.
    response, _ = verify_code(client, second, first_code)
    assert code_of(response) == "otp_incorrect"
    assert verify_code(client, second, second_code)[0].status_code == 200


def test_resend_has_a_cooldown_and_replaces_the_code(env, mailbox, monkeypatch):
    client, db = env
    user = fresh_account(db, Role.MEDICAL_REP, "resend")
    _, transaction = start(client, user.email)
    cookie = cookie_header(**{recovery.TRANSACTION_COOKIE: transaction})
    early = client.post(f"{BASE}/resend", headers=cookie)
    assert early.status_code == 429 and code_of(early) == "otp_resend_wait"
    later = utcnow() + timedelta(seconds=get_settings().otp_resend_seconds + 1)
    monkeypatch.setattr(recovery, "utcnow", lambda: later)
    again = client.post(f"{BASE}/resend", headers=cookie)
    assert again.status_code == 202 and again.json()["message"] == recovery.GENERIC
    old, new = mailbox.codes_for(user.email)
    if old != new:
        assert code_of(verify_code(client, transaction, old)[0]) == "otp_incorrect"
    assert verify_code(client, transaction, new)[0].status_code == 200


def test_resend_without_a_transaction_is_refused(env):
    client, _ = env
    response = client.post(f"{BASE}/resend")
    assert response.status_code == 400 and code_of(response) == "reset_expired"


# --- Reset authorization -------------------------------------------------------------------


def test_reset_needs_a_valid_authorization_of_the_same_transaction(env, mailbox):
    client, db = env
    a = fresh_account(db, Role.PATIENT, "auth-a")
    b = fresh_account(db, Role.PATIENT, "auth-b")
    _, ta = start(client, a.email)
    _, tb = start(client, b.email)
    (code_a,) = mailbox.codes_for(a.email)
    (code_b,) = mailbox.codes_for(b.email)

    # Without verifying first, or with no authorization at all.
    assert code_of(finish(client, ta, None)) == "reset_unauthorized"
    assert code_of(finish(client, ta, "forged-token")) == "reset_unauthorized"
    _, auth_a = verify_code(client, ta, code_a)
    _, auth_b = verify_code(client, tb, code_b)
    # B's authorization cannot be used for A's transaction, nor the other way round.
    assert code_of(finish(client, ta, auth_b)) == "reset_unauthorized"
    assert code_of(finish(client, tb, auth_a)) == "reset_unauthorized"
    hash_before = db.get(User, a.id).password_hash
    # The OTP alone (no authorization) is never enough.
    assert finish(client, ta, code_a).status_code == 401
    db.refresh(a)
    assert a.password_hash == hash_before

    assert finish(client, ta, auth_a).status_code == 200
    # Spent: the same pair cannot be used again.
    assert code_of(finish(client, ta, auth_a)) == "reset_expired"


def test_expired_authorization_is_refused(env, mailbox, monkeypatch):
    client, db = env
    user = fresh_account(db, Role.CARE_MANAGER, "auth-expired")
    _, transaction = start(client, user.email)
    (code,) = mailbox.codes_for(user.email)
    _, authorization = verify_code(client, transaction, code)
    later = utcnow() + timedelta(minutes=recovery.AUTHORIZATION_MINUTES, seconds=1)
    monkeypatch.setattr(recovery, "utcnow", lambda: later)
    assert code_of(finish(client, transaction, authorization)) == "reset_unauthorized"


def test_password_policy_and_mismatch_keep_the_transaction_usable(env, mailbox):
    client, db = env
    user = fresh_account(db, Role.PATIENT, "policy")
    _, transaction = start(client, user.email)
    (code,) = mailbox.codes_for(user.email)
    _, authorization = verify_code(client, transaction, code)
    assert code_of(finish(client, transaction, authorization, confirm="Different-pass-99")) == (
        "password_mismatch"
    )
    assert code_of(finish(client, transaction, authorization, password=WEAK)) == "weak_password"
    assert code_of(finish(client, transaction, authorization, password=DEMO_PASSWORD)) == (
        "password_reused"
    )
    assert finish(client, transaction, authorization).status_code == 200


# --- Administrator exclusion ---------------------------------------------------------------


def test_admin_never_gets_a_code_and_cannot_verify_or_complete(env, mailbox):
    client, db = env
    admin = db.scalar(select(User).where(User.email == ADMIN_LOGIN[0]))
    hash_before = admin.password_hash
    response, transaction = start(client, admin.email)
    assert response.status_code == 202 and response.json()["message"] == recovery.GENERIC
    assert mailbox.to(admin.email) == []
    row = db.scalar(
        select(PasswordReset).where(
            PasswordReset.transaction_hash == recovery.token_hash(transaction)
        )
    )
    assert row.user_id is None and row.code_hash is None
    # No code can open it, whatever is guessed.
    response, authorization = verify_code(client, transaction, "123456")
    assert code_of(response) == "otp_incorrect" and authorization is None
    assert finish(client, transaction, "anything").status_code == 401
    db.refresh(admin)
    assert admin.password_hash == hash_before
    # Normal administrator sign-in is unaffected.
    assert login_as(client, *ADMIN_LOGIN).status_code == 200
    assert "self:account:recover" not in permissions_for(Role.ADMIN)


def test_role_changed_to_admin_before_verify_stops_the_reset(env, mailbox):
    client, db = env
    user = fresh_account(db, Role.CARE_MANAGER, "promoted-early")
    _, transaction = start(client, user.email)
    (code,) = mailbox.codes_for(user.email)
    user.role = Role.ADMIN
    db.commit()
    response, authorization = verify_code(client, transaction, code)
    assert response.status_code == 409 and code_of(response) == "reset_unavailable"
    assert authorization is None
    # The transaction is ended: a later attempt cannot reuse it.
    assert code_of(verify_code(client, transaction, code)[0]) == "reset_expired"


def test_role_changed_to_admin_after_verify_stops_the_password_change(env, mailbox):
    client, db = env
    user = fresh_account(db, Role.HCP, "promoted-late")
    hash_before = user.password_hash
    _, transaction = start(client, user.email)
    (code,) = mailbox.codes_for(user.email)
    _, authorization = verify_code(client, transaction, code)
    assert authorization
    user.role = Role.ADMIN
    db.commit()
    response = finish(client, transaction, authorization)
    assert response.status_code == 409 and code_of(response) == "reset_unavailable"
    db.refresh(user)
    assert user.password_hash == hash_before and user.role == Role.ADMIN
    # Stale authorization stays dead even if the role is changed back.
    user.role = Role.HCP
    db.commit()
    assert code_of(finish(client, transaction, authorization)) == "reset_expired"
    refused = db.scalar(
        select(AuditLog).where(
            AuditLog.action == "password_reset_refused", AuditLog.entity_id == str(user.id)
        )
    )
    assert refused is not None


def test_role_changed_to_admin_blocks_resend(env, mailbox, monkeypatch):
    client, db = env
    user = fresh_account(db, Role.PATIENT, "promoted-resend")
    _, transaction = start(client, user.email)
    user.role = Role.ADMIN
    db.commit()
    later = utcnow() + timedelta(seconds=get_settings().otp_resend_seconds + 1)
    monkeypatch.setattr(recovery, "utcnow", lambda: later)
    response = client.post(
        f"{BASE}/resend", headers=cookie_header(**{recovery.TRANSACTION_COOKIE: transaction})
    )
    assert code_of(response) == "reset_unavailable"
    assert len(mailbox.codes_for(user.email)) == 1


# --- Delivery, sessions, protections --------------------------------------------------------


def test_delivery_failure_never_claims_success_or_leaves_a_code(env, monkeypatch):
    client, db = env
    failing = Mailbox(fail=True)
    monkeypatch.setattr(recovery, "get_mailer", lambda settings=None: failing)
    user = fresh_account(db, Role.PATIENT, "mail-down")
    response, transaction = start(client, user.email)
    assert response.status_code == 202 and response.json()["message"] == recovery.GENERIC
    row = db.scalar(select(PasswordReset).where(PasswordReset.user_id == user.id))
    assert row.code_hash is None
    log = db.scalar(
        select(AuditLog)
        .where(AuditLog.action == "password_reset_requested")
        .order_by(AuditLog.id.desc())
    )
    assert log.detail["outcome"] == "delivery_failed"


def test_development_mode_shows_a_code_for_every_address(env):
    """Local run without a mail server: a code is shown on screen for every address (a random
    one when nothing can succeed), so the screens still look the same."""
    client, db = env
    eligible, _ = start(client, email_of(db, "cm01"))
    unknown, _ = start(client, "ghost@example.org")
    for response in (eligible, unknown):
        assert response.json()["delivery"] == "development"
        assert re.fullmatch(r"\d{6}", response.json()["dev_otp"])


def test_completion_ends_every_session(env, mailbox):
    client, db = env
    user = fresh_account(db, Role.COMPLIANCE, "sessions")
    for _ in range(2):
        login_as(client, user.email, DEMO_PASSWORD)
    assert db.scalars(select(UserSession).where(UserSession.user_id == user.id)).all()
    _, transaction = start(client, user.email)
    (code,) = mailbox.codes_for(user.email)
    _, authorization = verify_code(client, transaction, code)
    assert finish(client, transaction, authorization).status_code == 200
    assert not db.scalars(select(UserSession).where(UserSession.user_id == user.id)).all()


def test_csrf_is_required(env):
    plain = TestClient(app)
    response = plain.post(f"{BASE}/request", json={"email": "someone@example.org"})
    assert response.status_code == 403


def test_request_body_rejects_extra_fields(env):
    client, _ = env
    response = client.post(f"{BASE}/request", json={"email": "a@example.org", "role": "patient"})
    assert response.status_code == 422


def test_requests_are_rate_limited(env, mailbox, monkeypatch):
    client, db = env
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    ratelimit.reset(db)
    db.commit()
    try:
        email = fresh_account(db, Role.PATIENT, "limited").email
        statuses = [start(client, email)[0].status_code for _ in range(4)]
        assert statuses == [202, 202, 202, 429]
    finally:
        ratelimit.reset(db)
        db.commit()
