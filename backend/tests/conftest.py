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
