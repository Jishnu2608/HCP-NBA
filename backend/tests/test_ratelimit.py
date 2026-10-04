"""Rate limits on sign-in, sign-up, one-time codes and invitations, and sign-up that does
not reveal registered emails. Limits are enforced on the server and stored in the
database; the client cannot reset or skip them."""

from datetime import timedelta

import pytest
from conftest import (
    ADMIN_LOGIN,
    ADULT_DOB,
    PASSWORD,
    PATIENT_AGREEMENTS,
    PRO_AGREEMENTS,
    ApiClient,
    challenge_of,
    cookie_header,
    new_session,
)
from sqlalchemy import func, select

from app.auth import otp as otp_module
from app.auth.otp import otp_service
from app.core import ratelimit
from app.core.config import get_settings
from app.core.db import get_db
from app.datagen.generate import GenConfig, generate
from app.main import app
from app.models import OtpChallenge, RateLimitHit, User
from app.models.tables import utcnow

LIMITS = ratelimit.LIMITS


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GenConfig(seed=71, n_patients=80, n_hcps=16, n_reps=2, n_care_managers=2))
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    yield ApiClient(app), db
    app.dependency_overrides.clear()
    db.close()


@pytest.fixture(autouse=True)
def limits_on(env, monkeypatch):
    """Rate limits on, a clean slate, a movable clock, and the client address taken from a
    test header so one test can play several addresses."""
    _, db = env
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    clock = {"now": utcnow()}
    monkeypatch.setattr(ratelimit, "_now", lambda: clock["now"])
    monkeypatch.setattr(
        ratelimit, "client_ip", lambda request: request.headers.get("x-test-ip", "10.0.0.1")
    )
    ratelimit.reset(db)
    db.commit()
    yield clock
    ratelimit.reset(db)
    db.commit()


def ip(address: str) -> dict:
    return {"X-Test-IP": address}


def code_of(response) -> str:
    return response.json()["detail"]["code"]


def login(client, email, password, address="10.0.0.1"):
    return client.post(
        "/api/auth/login", json={"email": email, "password": password}, headers=ip(address)
    )


def signup(client, email, address="10.0.0.1"):
    body = {
        "name": "Rate Test", "email": email, "date_of_birth": ADULT_DOB,
        "password": PASSWORD, "confirm_password": PASSWORD, **PATIENT_AGREEMENTS,
    }  # fmt: skip
    return client.post("/api/auth/signup", json=body, headers=ip(address))


def assert_limited(response):
    assert response.status_code == 429
    assert code_of(response) == "rate_limited"
    assert response.json()["detail"] == {
        "code": "rate_limited",
        "message": "Too many attempts. Please try again later.",
    }  # nothing about which limit, which key, or the account
    assert int(response.headers["Retry-After"]) >= 1


# --- Sign-in --------------------------------------------------------------------------------


def test_failed_sign_in_is_generic_and_limited_then_expires(env, limits_on):
    client, _ = env
    admin_email, admin_password = ADMIN_LOGIN
    allowed, window = LIMITS["login_fail_account"]
    unknown = login(client, "nobody@example.org", "Wrong-pass-123")
    wrong = login(client, admin_email, "Wrong-pass-123")
    assert unknown.json() == wrong.json()
    assert wrong.json()["detail"] == {
        "code": "invalid_credentials",
        "message": "Incorrect email or password.",
    }
    for _ in range(allowed - 1):
        assert login(client, admin_email, "Wrong-pass-123").status_code == 401
    # Limited now, even with the right password from this address.
    assert_limited(login(client, admin_email, admin_password))
    # The window passes: the right password works again.
    limits_on["now"] += timedelta(seconds=window + 1)
    assert login(client, admin_email, admin_password).status_code == 200


def test_attacker_cannot_lock_the_owner_out(env):
    """Someone who only knows the email exhausts the limit from their own address; the
    owner, from theirs, signs in normally."""
    client, _ = env
    admin_email, admin_password = ADMIN_LOGIN
    allowed, _ = LIMITS["login_fail_account"]
    for _ in range(allowed):
        login(client, admin_email, "Guessing-123", address="203.0.113.9")
    assert_limited(login(client, admin_email, "Guessing-123", address="203.0.113.9"))
    assert login(client, admin_email, admin_password, address="198.51.100.7").status_code == 200


def test_success_clears_the_failure_count(env):
    client, _ = env
    admin_email, admin_password = ADMIN_LOGIN
    allowed, _ = LIMITS["login_fail_account"]
    for _ in range(allowed - 1):
        login(client, admin_email, "Wrong-pass-123")
    assert login(client, admin_email, admin_password).status_code == 200
    # The count started again: another full set of failures is allowed before the limit.
    for _ in range(allowed):
        assert login(client, admin_email, "Wrong-pass-123").status_code == 401
    assert_limited(login(client, admin_email, "Wrong-pass-123"))


def test_password_spraying_from_one_address_is_limited(env):
    client, _ = env
    allowed, _ = LIMITS["login_fail_ip"]
    for n in range(allowed):
        assert login(client, f"user{n}@example.org", "Spray-pass-123").status_code == 401
    assert_limited(login(client, "another@example.org", "Spray-pass-123"))
    # Another address is unaffected.
    assert login(client, "another@example.org", "Spray-pass-123", "192.0.2.4").status_code == 401


# --- Sign-up ----------------------------------------------------------------------------------


def test_signup_answer_is_the_same_for_new_and_registered_emails(env, monkeypatch):
    client, db = env
    sent = []

    class Mailbox:
        def deliver(self, message):
            sent.append(message)

    monkeypatch.setattr("app.auth.service.get_mailer", lambda: Mailbox())
    new = signup(client, "brand.new.person@example.org", "10.1.0.1")
    existing = signup(client, "cm01@nba.demo", "10.1.0.2")
    assert new.status_code == existing.status_code == 201
    assert set(new.json()) == set(existing.json())
    assert new.json()["message"] == existing.json()["message"] == "Check your email to continue."
    assert existing.cookies.get("nba_verify")
    # No duplicate account, no code for the existing one, and the owner is told by email.
    owners = select(func.count()).select_from(User).where(User.email == "cm01@nba.demo")
    assert db.scalar(owners) == 1
    cm01 = db.scalar(select(User).where(User.email == "cm01@nba.demo"))
    assert db.scalar(select(OtpChallenge).where(OtpChallenge.user_id == cm01.id)) is None
    (notice,) = sent
    assert notice["To"] == "cm01@nba.demo"
    assert "Someone tried to create" in notice.get_body(("plain",)).get_content()
    # Its code never works, whatever is typed.
    attempt = client.post(
        "/api/auth/verify-otp",
        json={"code": challenge_of(existing)["dev_otp"]},
        headers={**cookie_header(nba_verify=existing.cookies.get("nba_verify")), **ip("10.1.0.2")},
    )
    assert code_of(attempt) == "otp_incorrect"


def test_signup_is_limited_per_address(env):
    client, _ = env
    allowed, _ = LIMITS["signup_ip"]
    for n in range(allowed):
        assert signup(client, f"limit{n}@example.org", "10.2.0.1").status_code == 201
    assert_limited(signup(client, "one.more@example.org", "10.2.0.1"))
    assert signup(client, "one.more@example.org", "10.2.0.2").status_code == 201


# --- One-time codes ---------------------------------------------------------------------------


def test_code_emails_are_limited_and_work_with_the_cooldown(env, monkeypatch):
    client, _ = env
    allowed, _ = LIMITS["otp_send"]
    first = signup(client, "resend.limit@example.org", "10.3.0.1")  # code email 1
    pending = {**cookie_header(nba_verify=first.cookies.get("nba_verify")), **ip("10.3.0.1")}
    # Inside the 30-second cool-down: refused, and not counted against the limit.
    early = client.post("/api/auth/resend-otp", headers=pending)
    assert early.status_code == 429 and code_of(early) == "otp_resend_wait"
    later = utcnow()
    for _ in range(allowed - 1):  # code emails 2 .. allowed
        later += timedelta(seconds=get_settings().otp_resend_seconds + 1)
        monkeypatch.setattr(otp_service, "_now", lambda moment=later: moment)
        assert client.post("/api/auth/resend-otp", headers=pending).status_code == 200
    later += timedelta(seconds=get_settings().otp_resend_seconds + 1)
    monkeypatch.setattr(otp_service, "_now", lambda moment=later: moment)
    assert_limited(client.post("/api/auth/resend-otp", headers=pending))


def test_code_entry_is_limited_per_account_even_with_a_new_code(env, limits_on, monkeypatch):
    client, db = env
    allowed, window = LIMITS["otp_verify"]
    first = signup(client, "guesser@example.org", "10.4.0.1")
    headers = {**cookie_header(nba_verify=first.cookies.get("nba_verify")), **ip("10.4.0.1")}
    real = first.json()["dev_otp"]
    wrong = "000000" if real != "000000" else "111111"
    for _ in range(allowed - 1):
        response = client.post("/api/auth/verify-otp", json={"code": wrong}, headers=headers)
        assert code_of(response) == "otp_incorrect"
    # A fresh code (after the cool-down) does not give fresh guesses.
    later = utcnow() + timedelta(seconds=get_settings().otp_resend_seconds + 1)
    monkeypatch.setattr(otp_service, "_now", lambda: later)
    resent = client.post("/api/auth/resend-otp", headers=headers)
    assert resent.status_code == 200
    headers = {**cookie_header(nba_verify=resent.cookies.get("nba_verify")), **ip("10.4.0.1")}
    assert code_of(client.post("/api/auth/verify-otp", json={"code": wrong}, headers=headers)) == (
        "otp_incorrect"
    )
    assert_limited(
        client.post(
            "/api/auth/verify-otp", json={"code": resent.json()["dev_otp"]}, headers=headers
        )
    )
    # After the window, the right code works.
    limits_on["now"] += timedelta(seconds=window + 1)
    done = client.post(
        "/api/auth/verify-otp", json={"code": resent.json()["dev_otp"]}, headers=headers
    )
    assert done.status_code == 200
    assert db.scalar(select(User).where(User.email == "guesser@example.org")).verified


def test_codes_are_never_logged_or_returned_with_mail_configured(env, monkeypatch, caplog):
    client, _ = env

    class Mail:
        channel = "email"
        codes: list[str] = []

        def send(self, user, code, minutes):
            self.codes.append(code)

    mail = Mail()
    monkeypatch.setattr(otp_module, "get_sender", lambda settings=None: mail)
    response = signup(client, "quiet@example.org", "10.5.0.1")
    assert "dev_otp" not in response.json()
    (code,) = mail.codes
    assert code not in response.text and code not in caplog.text


# --- Invitations --------------------------------------------------------------------------------


def accept_from(client, token: str, address: str):
    body = {
        "token": token, "name": "X Y", "date_of_birth": ADULT_DOB,
        "password": PASSWORD, "confirm_password": PASSWORD, **PRO_AGREEMENTS,
    }  # fmt: skip
    return client.post("/api/invitations/accept", json=body, headers=ip(address))


def test_invitation_acceptance_is_limited_per_address(env):
    client, _ = env
    allowed, _ = LIMITS["invite_accept_ip"]
    for n in range(allowed):
        # The existing rules still answer first: an unknown link is "invalid".
        assert code_of(accept_from(client, f"not-a-token-{n}", "10.6.0.1")) == (
            "invitation_invalid"
        )
    assert_limited(accept_from(client, "not-a-token-x", "10.6.0.1"))
    assert code_of(accept_from(client, "not-a-token-y", "10.6.0.2")) == "invitation_invalid"


# --- Storage ------------------------------------------------------------------------------------


def test_limits_live_in_the_database_without_raw_keys(env):
    client, db = env
    login(client, "stored.key@example.org", "Wrong-pass-123", "10.7.0.1")
    rows = db.scalars(select(RateLimitHit)).all()
    assert rows and {r.bucket for r in rows} >= {"login_fail_account", "login_fail_ip"}
    assert all("@" not in r.key_hash and "10.7.0.1" not in r.key_hash for r in rows)
    # A brand-new client (another browser, cleared cookies, restarted server process) sees
    # the same counts: nothing about the limit is held by the client.
    fresh = ApiClient(app)
    allowed, _ = LIMITS["login_fail_account"]
    for _ in range(allowed - 1):
        login(fresh, "stored.key@example.org", "Wrong-pass-123", "10.7.0.1")
    assert_limited(login(fresh, "stored.key@example.org", "Wrong-pass-123", "10.7.0.1"))
