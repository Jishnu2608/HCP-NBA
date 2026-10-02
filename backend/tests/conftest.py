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
    NBA_SEED_DEMO_ACCOUNTS="true",
)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import pipeline
from app.core.db import Base, get_db, make_engine
from app.datagen.generate import GenConfig, generate
from app.features.population import load_population
from app.main import app
from app.scoring import propensity

ADMIN_LOGIN = ("admin@admin.com", TEST_ADMIN_PASSWORD)
DEMO_PASSWORD = TEST_DEMO_PASSWORD


def sign_in(client, username: str) -> str:
    """Session token for a seeded account, obtained through the real email + password login.

    Tokens are cached on the client so a test module signs each account in once.
    """
    cache = client.__dict__.setdefault("_tokens", {})
    if username not in cache:
        email, password = (
            ADMIN_LOGIN if username == "admin" else (f"{username}@nba.demo", DEMO_PASSWORD)
        )
        response = client.post("/api/auth/login", data={"username": email, "password": password})
        assert response.status_code == 200, response.text
        cache[username] = response.json()["access_token"]
    return cache[username]


def auth(client, username: str) -> dict:
    return {"Authorization": f"Bearer {sign_in(client, username)}"}


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
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
