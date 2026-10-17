"""Database isolation for the context layer: tests never reach a persistent file, and on a
database without the AI tables the AI telemetry source and diagnostics say "unavailable"
instead of reporting zero generations as healthy, while every other source still works and
nothing is written. (Migration targets and the traditional-file guard are covered by
test_database_profiles.py.)"""

import pytest
from conftest import new_session
from context_world import build, table_hashes
from sqlalchemy import select

from app import cycle
from app.ai import diagnostics
from app.ai.contracts import CategoryState
from app.core import db as core_db
from app.core.db import sqlite_file
from app.datagen.generate import GenConfig, generate
from app.models import AiGeneration, CareManagerPatient, User
from app.services import ai_telemetry


def test_the_default_engine_points_at_memory_during_tests():
    assert sqlite_file(str(core_db.engine.url)) is None
    assert sqlite_file(str(core_db.SessionLocal.kw["bind"].url)) is None


@pytest.fixture(scope="module")
def traditional():
    db = new_session()
    AiGeneration.__table__.drop(db.get_bind())
    generate(db, GenConfig(seed=53, n_patients=60, n_hcps=12, n_reps=2, n_care_managers=2))
    cycle.run(db)
    db.commit()
    yield db
    db.close()


def _user(db, role):
    return db.scalar(select(User).where(User.role == role).order_by(User.id))


def test_telemetry_is_unavailable_not_healthy_without_ai_tables(traditional):
    admin = _user(traditional, "admin")
    t = ai_telemetry.ai_telemetry(traditional, admin)
    assert t.available is False and t.rows == []
    # A source that cannot be read gives an explicit, incomplete context, never an empty one.
    ctx = build(traditional, admin, "ai_telemetry")
    assert ctx.state_of("generation_outcome") is CategoryState.UNAVAILABLE and not ctx.complete
    assert ctx.model_items == []
    view = diagnostics.sources_view(traditional)
    by_id = {s["id"]: s for s in view["sources"]}
    assert by_id["ai_telemetry"]["availability"] == "unavailable"
    assert by_id["patient_360"]["availability"] == "ok"


def test_every_other_source_works_and_nothing_is_written(traditional):
    cm = _user(traditional, "care_manager")
    admin = _user(traditional, "admin")
    pid = traditional.scalar(
        select(CareManagerPatient.patient_id).where(
            CareManagerPatient.care_manager_user_id == cm.id
        )
    )
    before = table_hashes(traditional)
    assert build(traditional, cm, "patient", pid).model_items
    assert build(traditional, cm, "care_workflow").categories
    assert build(traditional, admin, "audit_activity").categories
    assert build(traditional, admin, "job_queue").categories
    health = diagnostics.health_view(traditional, admin)
    assert health["ai_tables"] is False and health["telemetry"]["state"] == "unavailable"
    assert table_hashes(traditional) == before
