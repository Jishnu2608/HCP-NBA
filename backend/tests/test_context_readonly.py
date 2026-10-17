"""Context retrieval and AI generation change no business record.

Every business table is hashed before and after: building every subject type for every role,
running the diagnostics, and running a generation. A source that tries to write is stopped by
the read-only guard. A real patient's stale adherence figures are reported as stale, not
recomputed, by the context layer."""

from datetime import timedelta

import pytest
from context_world import ROLES, RecordingProvider, build, fresh_world, principal, table_hashes
from sqlalchemy import select

from app import jobs
from app.ai import diagnostics, orchestrator, readonly, sources
from app.ai.contracts import CategoryState, SubjectType
from app.ai.sources import Category, ContextSource
from app.core.config import get_settings
from app.models import AdherenceSnapshot, Patient, User
from app.models.enums import Role
from app.services import patients
from app.services.errors import Forbidden, NoRecord, NotFound


@pytest.fixture(scope="module")
def world():
    return fresh_world()


def test_building_every_context_for_every_role_writes_nothing(world):
    from app.ai.diagnostics import _fixture_subject

    db = world.session_factory()
    before = table_hashes(db)
    built = 0
    for subject_type in SubjectType:
        for role in ROLES:
            try:
                build(
                    db,
                    principal(db, world, role),
                    subject_type,
                    _fixture_subject(world, subject_type, "auto"),
                )
                built += 1
            except (Forbidden, NotFound, NoRecord):
                pass
    db.commit()
    assert built >= 25
    assert table_hashes(db) == before
    db.close()


def test_the_guard_stops_a_source_that_tries_to_write(world):
    db = world.session_factory()
    cm = principal(db, world, Role.CARE_MANAGER)
    original = sources.get_source("care_workflow")

    def writing(db_, user, request, b):
        user.display_name = "changed by a context read"
        db_.flush()

    sources.unregister_source("care_workflow")
    sources.register_source(
        ContextSource(
            id="writer",
            domain="test",
            subject_types=(SubjectType.CARE_WORKFLOW,),
            categories=(Category("x", "care_workflow_metric", "x"),),
            read=writing,
            read_services=(),
        )
    )
    try:
        with pytest.raises(readonly.ReadOnlyViolation):
            build(db, cm, "care_workflow")
    finally:
        sources.unregister_source("writer")
        sources.register_source(original)
        db.rollback()
    assert db.get(User, cm.id).display_name != "changed by a context read"
    db.close()


def test_diagnostics_write_nothing_to_the_fixture_world_or_the_application(world, monkeypatch):
    from app.ai import fixtures

    monkeypatch.setattr(fixtures, "_WORLD", world)
    db = world.session_factory()
    before = table_hashes(db)
    diagnostics.safety_suite()
    diagnostics.aggregate_suite()
    for st in SubjectType:
        diagnostics.context_probe(db, None, diagnostics.ContextProbe(subject_type=st, role="admin"))
    assert table_hashes(db) == before
    db.close()


def test_a_generation_writes_only_its_own_rows(world, monkeypatch):
    import json

    db = world.session_factory()
    cm = principal(db, world, Role.CARE_MANAGER)
    before = table_hashes(db)
    ctx = build(db, cm, "patient", world.subjects["patient"])
    cite = next(i.id for i in ctx.model_items if i.kind == "patient")
    reply = json.dumps(
        {
            "summary": "Outreach continues.",
            "summary_citations": [cite],
            "suggested_focus": "Review outreach.",
            "focus_citations": [cite],
        }
    )
    monkeypatch.setattr(orchestrator, "get_provider", lambda s=None: RecordingProvider(reply))
    monkeypatch.setattr(get_settings(), "ai_case_brief_mode", "prototype")
    orchestrator.submit(db, cm, "case_brief", world.subjects["patient"])
    jobs.run_pending(world.session_factory)
    db.expire_all()
    assert table_hashes(db) == before  # ai_generation, audit_log and job are excluded
    db.close()


def test_stale_real_patient_figures_are_reported_not_recomputed(world):
    db = world.session_factory()
    cm = principal(db, world, Role.CARE_MANAGER)
    pid = world.subjects["patient"]
    patient = db.get(Patient, pid)
    origin = patient.origin
    patient.origin = "self_registered"  # a real patient: figures must be as of today
    snaps = db.scalars(select(AdherenceSnapshot).where(AdherenceSnapshot.patient_id == pid)).all()
    for s in snaps:
        s.as_of_date = s.as_of_date - timedelta(days=3)
    db.flush()
    if not patients.stale_therapies(db, cm, pid):
        db.rollback()
        pytest.skip("this patient has no confirmed therapy with a snapshot")
    before = table_hashes(db)
    ctx = build(db, cm, "patient", pid)
    assert ctx.state_of("rx") is CategoryState.STALE and not ctx.complete
    assert table_hashes(db) == before
    # The screen's explicit refresh (what opening Patient 360 does) brings them up to date.
    assert patients.refresh_if_stale(db, cm, pid)
    assert build(db, cm, "patient", pid).state_of("rx") is not CategoryState.STALE
    db.rollback()
    patient = db.get(Patient, pid)
    assert patient.origin == origin
    db.close()
