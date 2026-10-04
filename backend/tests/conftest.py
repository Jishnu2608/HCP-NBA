import os
import secrets

# Test-run secrets: generated here, set before the application is imported, and never read
# from the developer's .env. Mail delivery is forced off so no test can send a real email.
TEST_ADMIN_PASSWORD = f"Adm-{secrets.token_urlsafe(8)}7"
TEST_DEMO_PASSWORD = f"Dem-{secrets.token_urlsafe(8)}7"
os.environ.update(
    NBA_JWT_SECRET=secrets.token_urlsafe(48),
    NBA_ADMIN_PASSWORD=TEST_ADMIN_PASSWORD,
    NBA_DEMO_PASSWORD=TEST_DEMO_PASSWORD,
    NBA_ADMIN_EMAIL="admin@admin.com",
    NBA_SMTP_HOST="",
    NBA_DEMO_MODE="true",
    NBA_ENVIRONMENT="local",
    NBA_SEED_DEMO_ACCOUNTS="true",
    NBA_RATE_LIMIT_ENABLED="false",
)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import pipeline
from app.core.db import Base, get_db, make_engine
from app.core.http_security import new_csrf_token
from app.datagen.generate import GenConfig, generate
from app.features.population import load_population
from app.main import app
from app.scoring import propensity

ADMIN_LOGIN = ("admin@admin.com", TEST_ADMIN_PASSWORD)
DEMO_PASSWORD = TEST_DEMO_PASSWORD


class ApiClient(TestClient):
    """Behaves like the web app: sends the CSRF cookie and header on every request, and
    keeps no cookies between requests, so each request is exactly as authenticated as the
    `Cookie` header it is given (see `auth`). Security tests use a plain TestClient."""

    def __init__(self, application, **kwargs):
        self.csrf = new_csrf_token()
        super().__init__(application, **kwargs)

    def request(self, method, url, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        cookie = headers.pop("Cookie", "")
        headers["Cookie"] = "; ".join(c for c in (cookie, f"nba_csrf={self.csrf}") if c)
        headers.setdefault("X-CSRF-Token", self.csrf)
        response = super().request(method, url, headers=headers, **kwargs)
        self.cookies.clear()
        return response


def cookie_header(**cookies: str | None) -> dict:
    return {"Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items() if v)}


def session_of(response) -> str:
    """The session token a sign-in response set in its HttpOnly cookie."""
    token = response.cookies.get("nba_session")
    assert token, f"no session cookie: {response.status_code} {response.text}"
    return token


def login(client, email: str, password: str):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def sign_in(client, username: str) -> str:
    """Session token for a seeded account, obtained through the real email + password login.

    Tokens are cached on the client so a test module signs each account in once.
    """
    cache = client.__dict__.setdefault("_tokens", {})
    if username not in cache:
        email, password = (
            ADMIN_LOGIN if username == "admin" else (f"{username}@nba.demo", DEMO_PASSWORD)
        )
        response = login(client, email, password)
        assert response.status_code == 200, response.text
        cache[username] = session_of(response)
        # Seeded accounts have no consent records yet: accept the current documents, as the
        # re-acceptance screen does, so the rest of the API answers.
        pending = response.json()["user"]["pending_consents"]
        if pending:
            accepted = client.post(
                "/api/privacy/accept",
                json={"kinds": pending},
                headers=cookie_header(nba_session=cache[username]),
            )
            assert accepted.status_code == 200, accepted.text
    return cache[username]


def auth(client, username: str) -> dict:
    return cookie_header(nba_session=sign_in(client, username))


# What every sign-up and invitation acceptance must include: residence and the actively
# ticked agreements (patients also give the separate health-information consent).
PRO_AGREEMENTS = {"country": "US", "region": "CA", "accept_terms": True}
PATIENT_AGREEMENTS = {**PRO_AGREEMENTS, "consent_health_data": True}

# Generated per run. Satisfies the sign-up policy: 10+ characters, a letter and a digit.
PASSWORD = f"Pw-{secrets.token_urlsafe(10)}7"
ADULT_DOB = "1985-04-12"


def challenge_of(response) -> dict:
    """A sign-up or acceptance response, plus its HttpOnly verification cookie."""
    return {**response.json(), "_verify": response.cookies.get("nba_verify")}


def verify(client, challenge: dict, code: str | None = None):
    return client.post(
        "/api/auth/verify-otp",
        json={"code": code or challenge["dev_otp"]},
        headers=cookie_header(nba_verify=challenge["_verify"]),
    )


def signed_in(response) -> dict:
    assert response.status_code == 200, response.text
    return {**response.json(), "token": session_of(response)}


def as_user(session: dict) -> dict:
    return cookie_header(nba_session=session["token"])


def invite(client, inviter_headers: dict, email: str, role: str):
    return client.post(
        "/api/invitations", json={"email": email, "role": role}, headers=inviter_headers
    )


def token_of(invite_response) -> str:
    """The token from the development link (local run without a mail server)."""
    return invite_response.json()["dev_link"].rsplit("/", 1)[1]


def accept(client, token: str, name="Jordan Lee", dob=ADULT_DOB, password=PASSWORD, **extra):
    body = {
        "token": token, "name": name, "date_of_birth": dob,
        "password": password, "confirm_password": password, **PRO_AGREEMENTS, **extra,
    }  # fmt: skip
    return client.post("/api/invitations/accept", json=body)


def onboard(client, email: str, role: str, name="Jordan Lee", by: str = "admin") -> dict:
    """Invitation -> acceptance -> code: a signed-in professional account."""
    sent = invite(client, auth(client, by), email, role)
    assert sent.status_code == 201, sent.text
    accepted = accept(client, token_of(sent), name=name)
    assert accepted.status_code == 201, accepted.text
    return signed_in(verify(client, challenge_of(accepted)))


MEDIUM = GenConfig(seed=11, n_patients=900, n_hcps=90, n_reps=6, n_care_managers=3)


def new_session() -> Session:
    engine = make_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def seeded_session(model_dir, cfg: GenConfig = MEDIUM) -> Session:
    """Generated data with features refreshed and models trained."""
    db = new_session()
    generate(db, cfg)
    pop = load_population(db)
    pipeline.refresh_features(db, pop)
    pipeline.train_models(db, pop, model_dir)
    db.commit()
    return db


@pytest.fixture(autouse=True, scope="session")
def _models_in_tmp(tmp_path_factory):
    """Keep model files written during tests out of the real data directory."""
    original = propensity.MODEL_DIR
    propensity.MODEL_DIR = tmp_path_factory.mktemp("model_registry")
    yield
    propensity.MODEL_DIR = original


@pytest.fixture
def db():
    """Fresh in-memory SQLite database per test."""
    session = new_session()
    try:
        yield session
    finally:
        session.close()
        session.get_bind().dispose()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield ApiClient(app)
    finally:
        app.dependency_overrides.clear()
