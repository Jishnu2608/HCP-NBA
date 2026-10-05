"""Transport and request security: cookies, CSRF, origin, headers, limits, error bodies,
session lifetime, and that secrets never reach a response or the log."""

import logging
from datetime import timedelta

import pytest
from conftest import (
    ADMIN_LOGIN,
    PASSWORD,
    PATIENT_AGREEMENTS,
    ApiClient,
    auth,
    challenge_of,
    cookie_header,
    new_session,
    session_of,
)
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import cycle
from app.auth.sessions import sessions
from app.core.config import get_settings
from app.core.db import get_db
from app.core.http_security import RedactInviteTokens, new_csrf_token
from app.datagen.generate import GenConfig, generate
from app.main import app
from app.models import UserSession
from app.models.tables import utcnow


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GenConfig(seed=61, n_patients=120, n_hcps=24, n_reps=3, n_care_managers=2))
    cycle.run(db)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    yield ApiClient(app), db
    app.dependency_overrides.clear()
    db.close()


def raw() -> TestClient:
    """A client that behaves like an attacker's script or another website: no CSRF help."""
    return TestClient(app)


def code_of(response) -> str:
    return response.json()["detail"]["code"]


# --- Cookies ---------------------------------------------------------------------------------


def test_session_cookie_is_httponly_and_samesite(env):
    client, _ = env
    response = client.post(
        "/api/auth/login", json={"email": ADMIN_LOGIN[0], "password": ADMIN_LOGIN[1]}
    )
    header = next(
        h for h in response.headers.get_list("set-cookie") if h.startswith("nba_session=")
    )
    assert "HttpOnly" in header and "SameSite=lax" in header and "Path=/" in header
    assert "access_token" not in response.text


def test_cookies_are_secure_outside_a_local_run(env, monkeypatch):
    client, _ = env
    monkeypatch.setattr(get_settings(), "environment", "production")
    response = client.post(
        "/api/auth/login", json={"email": ADMIN_LOGIN[0], "password": ADMIN_LOGIN[1]}
    )
    for header in response.headers.get_list("set-cookie"):
        assert "Secure" in header
    assert "Strict-Transport-Security" in response.headers


# --- CSRF and origin -------------------------------------------------------------------------


def test_state_changes_need_the_csrf_token(env):
    client, _ = env
    session = auth(client, "admin")["Cookie"]
    plain = raw()
    no_token = plain.post("/api/admin/cycle", headers={"Cookie": session})
    assert no_token.status_code == 403 and code_of(no_token) == "csrf_failed"
    token = new_csrf_token()
    mismatch = plain.post(
        "/api/admin/cycle",
        headers={"Cookie": f"{session}; nba_csrf={token}", "X-CSRF-Token": new_csrf_token()},
    )
    assert mismatch.status_code == 403
    forged = "abc.def"  # not signed by the server
    unsigned = plain.post(
        "/api/admin/cycle",
        headers={"Cookie": f"{session}; nba_csrf={forged}", "X-CSRF-Token": forged},
    )
    assert unsigned.status_code == 403
    # Reads do not need it, and the server hands out a token to use.
    read = plain.get("/api/health")
    assert read.status_code == 200 and "nba_csrf=" in read.headers.get("set-cookie", "")


def test_login_itself_is_csrf_protected(env):
    response = raw().post(
        "/api/auth/login", json={"email": ADMIN_LOGIN[0], "password": ADMIN_LOGIN[1]}
    )
    assert response.status_code == 403 and code_of(response) == "csrf_failed"


def test_requests_from_another_site_are_refused(env):
    client, _ = env
    for header in ({"Origin": "https://evil.example"}, {"Referer": "https://evil.example/x"},
                   {"Origin": "null"}):  # fmt: skip
        response = client.post("/api/admin/cycle", headers={**auth(client, "admin"), **header})
        assert response.status_code == 403 and code_of(response) == "csrf_failed"
    token = session_of(
        client.post("/api/auth/login", json={"email": ADMIN_LOGIN[0], "password": ADMIN_LOGIN[1]})
    )
    same = client.post(
        "/api/auth/logout",
        headers={**cookie_header(nba_session=token), "Origin": "http://testserver"},
    )
    assert same.status_code == 200


# --- Sessions --------------------------------------------------------------------------------


def test_login_rotates_the_session(env):
    client, db = env
    first = session_of(
        client.post("/api/auth/login", json={"email": ADMIN_LOGIN[0], "password": ADMIN_LOGIN[1]})
    )
    second = client.post(
        "/api/auth/login",
        json={"email": ADMIN_LOGIN[0], "password": ADMIN_LOGIN[1]},
        headers=cookie_header(nba_session=first),
    )
    assert session_of(second) != first
    # The session presented at sign-in is ended (no session fixation).
    assert client.get("/api/auth/me", headers=cookie_header(nba_session=first)).status_code == 401


def test_session_reuse_after_logout_is_refused(env):
    client, _ = env
    token = session_of(
        client.post("/api/auth/login", json={"email": ADMIN_LOGIN[0], "password": ADMIN_LOGIN[1]})
    )
    assert (
        client.post("/api/auth/logout", headers=cookie_header(nba_session=token)).status_code == 200
    )
    assert client.get("/api/auth/me", headers=cookie_header(nba_session=token)).status_code == 401


def test_idle_and_absolute_session_expiry(env, monkeypatch):
    client, db = env
    settings = get_settings()
    token = session_of(
        client.post("/api/auth/login", json={"email": ADMIN_LOGIN[0], "password": ADMIN_LOGIN[1]})
    )
    idle = utcnow() + timedelta(minutes=settings.session_idle_minutes, seconds=5)
    monkeypatch.setattr(sessions, "_now", lambda: idle)
    assert client.get("/api/auth/me", headers=cookie_header(nba_session=token)).status_code == 401
    monkeypatch.undo()

    token = session_of(
        client.post("/api/auth/login", json={"email": ADMIN_LOGIN[0], "password": ADMIN_LOGIN[1]})
    )
    row = db.scalar(select(UserSession).order_by(UserSession.id.desc()))
    row.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert client.get("/api/auth/me", headers=cookie_header(nba_session=token)).status_code == 401


def test_only_a_hash_of_the_session_token_is_stored(env):
    client, db = env
    token = session_of(
        client.post("/api/auth/login", json={"email": ADMIN_LOGIN[0], "password": ADMIN_LOGIN[1]})
    )
    hashes = set(db.scalars(select(UserSession.token_hash)))
    assert token not in hashes and all(len(h) == 64 for h in hashes)


# --- Limits ---------------------------------------------------------------------------------


# --- Headers, docs, errors -------------------------------------------------------------------


def test_security_headers(env):
    response = raw().get("/api/health")
    headers = response.headers
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Cache-Control"] == "no-store"


def test_meta_is_not_public(env):
    client, _ = env
    assert raw().get("/api/meta").status_code == 401
    assert client.get("/api/meta", headers=auth(client, "rep01")).status_code == 403


def test_validation_errors_do_not_echo_input(env):
    client, _ = env
    secret = f"Val-{new_csrf_token()[:12]}9"  # any value; it must not come back
    response = client.post(
        "/api/auth/login", json={"email": "a@example.org", "password": secret, "role": "admin"}
    )
    assert response.status_code == 422 and code_of(response) == "invalid_request"
    assert secret not in response.text and "role" in response.json()["detail"]["message"]


def test_unhandled_errors_are_generic(env, monkeypatch):
    client, _ = env

    def boom(*args, **kwargs):
        raise RuntimeError("database password is hunter2 at C:\\secret\\path")

    monkeypatch.setattr("app.api.system.get_today", boom)
    safe = TestClient(app, raise_server_exceptions=False)
    response = safe.get("/api/meta", headers=auth(client, "admin"))
    assert response.status_code == 500
    assert code_of(response) == "server_error"
    assert "hunter2" not in response.text and "Traceback" not in response.text


# --- Secrets stay out of logs ------------------------------------------------------------------


def test_codes_tokens_and_passwords_never_reach_the_log(env, caplog):
    client, _ = env
    caplog.set_level(logging.DEBUG)
    signup = client.post(
        "/api/auth/signup",
        json={
            "name": "Log Check", "email": "log.check@example.org", "date_of_birth": "1980-01-01",
            "password": PASSWORD, "confirm_password": PASSWORD, **PATIENT_AGREEMENTS,
        },
    )  # fmt: skip
    code = challenge_of(signup)["dev_otp"]
    invited = client.post(
        "/api/invitations", json={"email": "log.invite@example.org", "role": "hcp"},
        headers=auth(client, "admin"),
    )  # fmt: skip
    token = invited.json()["dev_link"].rsplit("/", 1)[1]
    text = caplog.text
    assert code not in text and token not in text and PASSWORD not in text


def test_access_log_filter_redacts_invitation_links():
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4", "GET", "/invite/abcDEF123-_secret", "1.1", 200), None,
    )  # fmt: skip
    RedactInviteTokens().filter(record)
    assert "abcDEF123" not in record.getMessage() and "/invite/[redacted]" in record.getMessage()
