"""Two local databases chosen explicitly (traditional, ai); the traditional one has no AI tables
and everything except AI features works on it; nothing creates, switches or migrates a
database on its own."""

import hashlib
import sqlite3
from argparse import Namespace
from pathlib import Path

import pytest
from alembic.config import Config
from conftest import (
    ADULT_DOB,
    PASSWORD,
    PATIENT_AGREEMENTS,
    ApiClient,
    auth,
    challenge_of,
    new_session,
    signed_in,
    verify,
)
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from alembic import command
from app import cycle
from app.ai import orchestrator
from app.core import config as config_module
from app.core.config import DATABASE_FILES, Settings, get_settings
from app.core.db import MissingDatabase, get_db, has_table, present_tables, require_existing
from app.datagen.generate import GenConfig, generate
from app.main import app
from app.models import AiGeneration, AuditLog, CareManagerPatient, Patient, User

BACKEND = Path(__file__).resolve().parents[1]


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


# --- Selection -------------------------------------------------------------------------------


def test_profile_selects_one_of_two_files_and_defaults_to_traditional():
    assert _settings().database_label == "traditional"
    assert _settings().database_url.endswith("/data/nba_demo.db")
    ai = _settings(database_profile="ai")
    assert ai.database_label == "ai" and ai.database_url.endswith("/data/nba_demodb_ai.db")
    assert _settings(database_url="sqlite://").database_label == "custom"


@pytest.mark.parametrize(
    "kw", [{"database_profile": "ai", "database_url": "sqlite://"}, {"database_profile": "x"}]
)
def test_ambiguous_or_unknown_selection_is_refused(kw):
    with pytest.raises(ValueError):
        _settings(**kw)


def test_a_missing_database_file_is_refused_and_never_created(tmp_path):
    missing = tmp_path / "nowhere.db"
    with pytest.raises(MissingDatabase):
        require_existing(f"sqlite:///{missing.as_posix()}")
    assert not missing.exists()
    require_existing("sqlite://")  # in-memory: nothing to check
    existing = tmp_path / "there.db"
    sqlite3.connect(existing).close()
    require_existing(f"sqlite:///{existing.as_posix()}")


def test_the_app_refuses_to_start_on_a_missing_file(tmp_path, monkeypatch):
    missing = tmp_path / "gone" / "nba_demodb_ai.db"
    monkeypatch.setattr(get_settings(), "database_url", f"sqlite:///{missing.as_posix()}")
    with pytest.raises(MissingDatabase), TestClient(app):
        pass
    assert not missing.exists()


# --- Migration targeting ---------------------------------------------------------------------


def _alembic(url: str, *extra: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.cmd_opts = Namespace(x=[f"url={url}", *extra])
    return cfg


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_migrations_never_touch_the_traditional_file_unless_explicitly_allowed(
    tmp_path, monkeypatch
):
    traditional = tmp_path / "nba_demo.db"
    sqlite3.connect(traditional).close()  # an existing traditional database
    monkeypatch.setitem(DATABASE_FILES, "traditional", traditional)
    url = f"sqlite:///{traditional.as_posix()}"
    before = _sha(traditional)
    with pytest.raises(SystemExit, match="traditional database"):
        command.upgrade(_alembic(url), "head")
    assert _sha(traditional) == before
    assert inspect_tables(traditional) == set()

    # The AI database (any other file) migrates normally.
    ai = tmp_path / "nba_demodb_ai.db"
    sqlite3.connect(ai).close()
    command.upgrade(_alembic(f"sqlite:///{ai.as_posix()}"), "head")
    assert "ai_generation" in inspect_tables(ai)

    # Only an explicit override changes the traditional file.
    command.upgrade(_alembic(url, "allow_traditional=yes"), "head")
    assert "ai_generation" in inspect_tables(traditional)


def inspect_tables(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}


def test_the_profile_setting_is_what_alembic_targets():
    assert config_module.DATABASE_FILES["ai"].name == "nba_demodb_ai.db"
    # alembic/env.py reads get_settings().database_url, which the profile resolves.
    assert "nba_demodb_ai.db" in _settings(database_profile="ai").database_url


# --- A traditional database: no AI tables, everything else works ------------------------------


@pytest.fixture(scope="module")
def traditional():
    db = new_session()
    AiGeneration.__table__.drop(db.get_bind())  # the traditional database has no AI tables
    generate(db, GenConfig(seed=31, n_patients=60, n_hcps=12, n_reps=2, n_care_managers=2))
    cycle.run(db)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    yield ApiClient(app), db
    app.dependency_overrides.clear()
    db.close()


def _cm_patient(db) -> tuple[User, str]:
    cm = db.scalar(select(User).where(User.username.in_(("cm01", "cm01@nba.demo"))))
    pid = db.scalar(
        select(CareManagerPatient.patient_id).where(
            CareManagerPatient.care_manager_user_id == cm.id
        )
    )
    return cm, pid


def test_traditional_database_lacks_ai_tables_and_loops_skip_them(traditional):
    _, db = traditional
    assert not has_table(db, "ai_generation")
    names = {t.name for t in present_tables(db)}
    assert "ai_generation" not in names and {"patient", "audit_log", "job"} <= names
    assert "ai_generation" not in set(inspect(db.get_bind()).get_table_names())


def test_existing_features_work_without_ai_tables(traditional):
    client, db = traditional
    _, pid = _cm_patient(db)
    assert client.get(f"/api/patients/{pid}", headers=auth(client, "cm01")).status_code == 200
    assert client.get("/api/nba", headers=auth(client, "cm01")).status_code == 200
    meta = client.get("/api/meta", headers=auth(client, "admin"))
    assert meta.status_code == 200, meta.text
    assert meta.json()["ai_tables"] is False and "ai_generation" not in meta.json()["row_counts"]


def test_ai_routes_answer_503_after_the_access_check(traditional):
    client, db = traditional
    _, pid = _cm_patient(db)
    got = client.get(f"/api/ai/case_brief/patients/{pid}", headers=auth(client, "cm01"))
    assert got.status_code == 503 and got.json()["detail"]["code"] == "ai_unavailable"
    posted = client.post(
        f"/api/ai/case_brief/patients/{pid}/generations", headers=auth(client, "cm01")
    )
    assert posted.status_code == 503
    rejected = db.scalar(
        select(AuditLog)
        .where(AuditLog.action == "ai_generation_rejected", AuditLog.entity_id == pid)
        .order_by(AuditLog.id.desc())
    )
    assert rejected.reason == "ai_unavailable"
    # Access is still decided first: no permission is 403, another panel 404.
    assert (
        client.get(f"/api/ai/case_brief/patients/{pid}", headers=auth(client, "rep01")).status_code
        == 403
    )
    assert orchestrator.run(db, 1) == "failed"  # a job can never write to a missing table


def test_patient_deletion_and_demo_rebuild_work_without_ai_tables(traditional):
    client, db = traditional
    # A self-registered patient, deleted by the administrator: the erasure skips the AI table.
    body = {
        "name": "Dara Walsh", "email": "erase.trad@example.org", "date_of_birth": ADULT_DOB,
        "password": PASSWORD, "confirm_password": PASSWORD, **PATIENT_AGREEMENTS,
    }  # fmt: skip
    signed = client.post("/api/auth/signup", json=body)
    assert signed.status_code == 201, signed.text
    session = signed_in(verify(client, challenge_of(signed)))
    uid, pid = session["user"]["id"], session["user"]["patient_id"]
    deleted = client.request(
        "DELETE", f"/api/admin/users/{uid}", json={"confirm_email": "erase.trad@example.org"},
        headers=auth(client, "admin"),
    )  # fmt: skip
    assert deleted.status_code == 200, deleted.text
    db.expire_all()
    assert db.get(User, uid) is None and db.get(Patient, pid) is None
    # The generator's wipe and counts (used by the demo reset) cover only existing tables.
    result = generate(db, GenConfig(seed=32, n_patients=40, n_hcps=12, n_reps=2, n_care_managers=2))
    db.commit()
    assert "ai_generation" not in result and result["patient"] >= 40
    assert not has_table(db, "ai_generation")  # nothing created the AI table
