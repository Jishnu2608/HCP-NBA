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
    NBA_WORKER_MODE="off",
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


REVIEWERS = {"compliance1": "Priya Nair", "compliance2": "Jon Becker"}


def sign_in(client, username: str) -> str:
    """Session token for a seeded account, obtained through the real email + password login.

    Tokens are cached on the client so a test module signs each account in once.
    """
    cache = client.__dict__.setdefault("_tokens", {})
    if username not in cache and username in REVIEWERS:
        # Compliance / MLR reviewers are never seeded: onboard them through the invitation,
        # as the product does. The email keeps the familiar test name.
        cache[username] = onboard(
            client, f"{username}@nba.demo", "compliance", name=REVIEWERS[username]
        )["token"]
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


# Set NBA_TEST_DATABASE_URL to run the suite on another database (for example a disposable
# local PostgreSQL database). Each new session then gets its own freshly created schema in
# that database, so modules stay isolated exactly as with in-memory SQLite. The application's
# own database (NBA_DATABASE_URL) is never used by the tests.
TEST_DATABASE_URL = os.environ.get("NBA_TEST_DATABASE_URL", "")
_SCHEMA_PREFIX = "t_"
_schema_counter = iter(range(1, 1_000_000))


def _postgres_engine():
    from sqlalchemy import text
    from sqlalchemy.pool import NullPool

    schema = f"{_SCHEMA_PREFIX}{os.getpid()}_{next(_schema_counter)}"
    admin = make_engine(TEST_DATABASE_URL, poolclass=NullPool)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    admin.dispose()
    return make_engine(
        TEST_DATABASE_URL,
        poolclass=NullPool,
        connect_args={"options": f"-csearch_path={schema}"},
    )


def new_session() -> Session:
    if TEST_DATABASE_URL.startswith("postgresql"):
        engine = _postgres_engine()
    else:
        engine = make_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


@pytest.fixture(autouse=True, scope="session")
def _clean_test_schemas():
    """PostgreSQL runs: drop the schemas earlier runs created (never anything else)."""
    if not TEST_DATABASE_URL.startswith("postgresql"):
        yield
        return
    from sqlalchemy import text
    from sqlalchemy.pool import NullPool

    def drop_all_test_schemas():
        admin = make_engine(TEST_DATABASE_URL, poolclass=NullPool)
        with admin.connect() as conn:
            names = list(
                conn.execute(
                    text("SELECT nspname FROM pg_namespace WHERE nspname LIKE :p"),
                    {"p": _SCHEMA_PREFIX.replace("_", r"\_") + "%"},
                ).scalars()
            )
        # Connections a failed test left open in a transaction would block the drop forever:
        # end them (this disposable test database only, and only this test user's sessions).
        with admin.begin() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND usename = current_user "
                    "AND pid <> pg_backend_pid() AND state LIKE 'idle in transaction%'"
                )
            )
        # One schema per transaction: dropping them all at once exhausts the lock table.
        for name in names:
            with admin.begin() as conn:
                conn.execute(text(f'DROP SCHEMA "{name}" CASCADE'))
        admin.dispose()

    drop_all_test_schemas()
    yield
    drop_all_test_schemas()


def seeded_session(model_dir, cfg: GenConfig = MEDIUM) -> Session:
    """Generated data with features refreshed and models trained."""
    db = new_session()
    generate(db, cfg)
    pop = load_population(db)
    pipeline.refresh_features(db, pop)
    pipeline.train_models(db, pop, model_dir)
    db.commit()
    return db


def _file_digest(path) -> str | None:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


@pytest.fixture(autouse=True, scope="session")
def _persistent_databases_untouched():
    """No test may open or change the persistent databases (data/nba_demo.db, the traditional
    one, and data/nba_demodb_ai.db, the AI one). The application's default engine and session
    factory are rebound to an in-memory database for the whole run, so even code that falls
    back to `SessionLocal` cannot reach a file; both files are hashed before and after.
    (Keep the application itself stopped during a test run: a running server may legitimately
    write the AI database.)"""
    from app.core import db as core_db
    from app.core.config import DATABASE_FILES

    before = {name: _file_digest(path) for name, path in DATABASE_FILES.items()}
    memory = make_engine("sqlite://", poolclass=StaticPool)
    original_engine = core_db.engine
    core_db.engine = memory
    core_db.SessionLocal.configure(bind=memory)
    yield
    core_db.SessionLocal.configure(bind=original_engine)
    core_db.engine = original_engine
    after = {name: _file_digest(path) for name, path in DATABASE_FILES.items()}
    assert after == before, f"a persistent database file changed during the tests: {after}"


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


def run_jobs(db: Session) -> int:
    """Runs every queued background job against the test database, as the worker would,
    then expires the test session so it sees what the jobs wrote."""
    from app import jobs

    factory = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    ran = jobs.run_pending(factory, worker_id="test-worker")
    db.expire_all()
    return ran


def engine_job(env, response) -> dict:
    """A queued engine job (202 from /api/admin/cycle or /play-out) run to completion; returns
    the finished job. `env` is a test module's fixture tuple holding the session."""
    assert response.status_code == 202, response.text
    db = next(x for x in env if isinstance(x, Session))
    run_jobs(db)
    from app.models import Job

    job = db.get(Job, response.json()["job"]["id"])
    assert job.status == "succeeded", job.error
    return {"id": job.id, "status": job.status, "result": job.result}
