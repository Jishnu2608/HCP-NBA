"""Multi-subject, collection and aggregate contexts: scoped before combination, bounded, paged,
deterministic, honest about partial results, locked per reader scope, minimised in telemetry."""

import dataclasses
import json
from datetime import timedelta
from statistics import median

import pytest
from context_world import RecordingProvider, build, fresh_world, principal
from sqlalchemy import select

from app import jobs
from app.ai import aggregate, orchestrator, registry
from app.ai.contracts import CategoryState, SubjectType
from app.ai.engine import Consumer
from app.ai.operations import case_brief
from app.core.config import get_settings
from app.models import AiGeneration, CareManagerPatient, CareRequest, User
from app.models.enums import Role
from app.models.tables import utcnow
from app.services import ai_telemetry


@pytest.fixture(scope="module")
def world():
    w = fresh_world()
    db = w.session_factory()
    cm = db.get(User, w.users[Role.CARE_MANAGER])
    panel = db.scalars(
        select(CareManagerPatient.patient_id)
        .where(CareManagerPatient.care_manager_user_id == cm.id)
        .order_by(CareManagerPatient.patient_id)
    ).all()
    now = utcnow()
    for n, pid in enumerate(panel[:6]):
        db.add(
            CareRequest(
                patient_id=pid,
                type="medication_review",
                status="closed" if n % 2 else "open",
                reason=f"typed reason {n}",
                created_at=now - timedelta(days=10 + n),
                updated_at=now - timedelta(days=n),
                closed_at=now - timedelta(days=n) if n % 2 else None,
            )
        )
    db.commit()
    db.close()
    return w


@pytest.fixture
def db(world):
    session = world.session_factory()
    yield session
    session.rollback()
    session.close()


def _panel(db, world, n=3):
    cm = principal(db, world, Role.CARE_MANAGER)
    return db.scalars(
        select(CareManagerPatient.patient_id)
        .where(CareManagerPatient.care_manager_user_id == cm.id)
        .order_by(CareManagerPatient.patient_id)
        .limit(n)
    ).all()


CONSUMER = Consumer(
    id="t", permission=None, categories=("patient", "rx", "touch"), model_calls=False
)


def test_each_member_is_scoped_alone_and_the_result_is_deterministic(db, world):
    cm = principal(db, world, Role.CARE_MANAGER)
    ids = [*_panel(db, world), world.subjects["patient_out_of_scope"]]
    first = aggregate.build_many(db, cm, SubjectType.PATIENT, ids, CONSUMER)
    second = aggregate.build_many(db, cm, SubjectType.PATIENT, ids, CONSUMER)
    assert first.fingerprint == second.fingerprint
    assert [m.ordinal for m in first.members] == [1, 2, 3]
    assert [m.ref for m in first.members] == [m.ref for m in second.members]
    assert first.out_of_scope == 1 and not first.complete
    assert all(pid not in m.ref for m in first.members for pid in ids)


def test_limits_duplicates_and_partial_failures(db, world, monkeypatch):
    cm = principal(db, world, Role.CARE_MANAGER)
    ids = _panel(db, world, 2)
    dup = aggregate.build_many(db, cm, SubjectType.PATIENT, [*ids, *ids], CONSUMER)
    assert dup.requested == 2 and len(dup.members) == 2 and dup.complete
    monkeypatch.setattr(aggregate, "MAX_SUBJECTS", 1)
    capped = aggregate.build_many(db, cm, SubjectType.PATIENT, ids, CONSUMER)
    assert capped.dropped == 1 and not capped.complete
    monkeypatch.setattr(aggregate, "MAX_SUBJECTS", 20)

    from app.ai.sources import SourceUnavailable

    real = aggregate.build_context

    def flaky(db_, user, request, consumer):
        if request.subject_id == ids[0]:
            raise SourceUnavailable("test")
        return real(db_, user, request, consumer)

    monkeypatch.setattr(aggregate, "build_context", flaky)
    partial = aggregate.build_many(db, cm, SubjectType.PATIENT, ids, CONSUMER)
    assert partial.failed == 1 and len(partial.members) == 1 and not partial.complete


def test_collections_are_paged_and_say_when_more_exist(db, world):
    cm = principal(db, world, Role.CARE_MANAGER)
    first = build(db, cm, "care_queue", limit=3)
    assert first.state_of("queue_item") is CategoryState.TRUNCATED and not first.complete
    assert first.facts["next_cursor"] == 3
    rest = build(db, cm, "care_queue", limit=50, cursor=first.facts["next_cursor"])
    seen = [i.text for i in first.model_items if i.kind == "queue_item"]
    seen += [i.text for i in rest.model_items if i.kind == "queue_item"]
    assert len(seen) == first.facts["queue_total"]
    assert rest.state_of("queue_item") is CategoryState.INCLUDED
    # Queue items carry no patient id, name or typed reason.
    blob = " ".join(seen)
    assert "typed reason" not in blob and "PAT_" not in blob


def test_cycle_times_are_computed_in_code_and_match_an_independent_calculation(db, world):
    cm = principal(db, world, Role.CARE_MANAGER)
    ctx = build(db, cm, "care_workflow")
    metrics = {(m["type"], m["metric"]): m for m in ctx.facts["metrics"]}
    rows = db.scalars(
        select(CareRequest).where(
            CareRequest.patient_id.in_(_panel(db, world, 100)),
            CareRequest.type == "medication_review",
            CareRequest.closed_at.is_not(None),
        )
    ).all()
    expected = median(round((r.closed_at - r.created_at).total_seconds() / 86400, 1) for r in rows)
    got = metrics[("medication_review", "opened_to_closed")]
    assert got["count"] == len(rows) and got["median_days"] == round(expected, 1)
    assert build(db, cm, "care_workflow").fingerprint == ctx.fingerprint


def test_another_reader_never_shares_a_principal_dependent_fingerprint(db, world):
    cm = principal(db, world, Role.CARE_MANAGER)
    admin = principal(db, world, Role.ADMIN)
    a = build(db, cm, "care_workflow")
    b = build(db, admin, "care_workflow")
    assert a.scope_key != b.scope_key and a.fingerprint != b.fingerprint


# --- Orchestration over a collection: scope locks, duplicates, telemetry ----------------------


@pytest.fixture
def queue_operation(monkeypatch):
    spec = registry.register(
        dataclasses.replace(
            case_brief.SPEC,
            id="queue_probe",
            subject_type="care_workflow",
            context_kinds=("cycle_time",),
            mode_setting=None,
            allow_partial=True,
        )
    )
    monkeypatch.setattr(get_settings(), "ai_operation_modes", {"queue_probe": "shadow"})
    yield spec
    registry._REGISTRY.pop(spec.id, None)


def test_scope_locks_duplicates_and_reader_isolation(world, queue_operation, monkeypatch):
    provider = RecordingProvider(reply="not json")
    monkeypatch.setattr(orchestrator, "get_provider", lambda settings=None: provider)
    db = world.session_factory()
    cm = principal(db, world, Role.CARE_MANAGER)
    admin = principal(db, world, Role.ADMIN)
    g1, created1 = orchestrator.submit(db, cm, "queue_probe", None)
    g2, created2 = orchestrator.submit(db, cm, "queue_probe", None)
    assert created1 and not created2 and g1.id == g2.id  # duplicate: the active one
    assert g1.subject_id.startswith("s") and len(g1.subject_id) == 17  # opaque scope key
    # Admin is refused by the operation's permission; the scope key is per reader anyway.
    assert orchestrator.subject_key(queue_operation, admin, None) != g1.subject_id
    jobs.run_pending(world.session_factory)
    db.expire_all()
    done = db.get(AiGeneration, g1.id)
    assert done.status == "fallback" and done.failure == "schema_invalid"
    # A generation filed under one reader's scope cannot be run as another reader.
    done.status, done.requested_by_user_id = "queued", admin.id
    db.commit()
    assert orchestrator.run(db, done.id) == "failed"
    db.expire_all()
    assert db.get(AiGeneration, done.id).failure == "access_lost"
    db.close()


def test_telemetry_counts_subjects_but_never_lists_them(world, queue_operation, monkeypatch):
    db = world.session_factory()
    admin = principal(db, world, Role.ADMIN)
    t = ai_telemetry.ai_telemetry(db, admin)
    assert t.available
    blob = json.dumps(t.model_dump(), default=str)
    for gen in db.scalars(select(AiGeneration)):
        assert gen.subject_id not in blob
    assert all(r.subjects >= 1 for r in t.rows)
    db.close()


def test_refused_requests_are_counted_by_reason_code_only(world, queue_operation):
    from app.ai import orchestrator as orch
    from app.services.errors import Forbidden

    db = world.session_factory()
    rep = principal(db, world, Role.MEDICAL_REP)
    with pytest.raises(Forbidden):
        orch.submit(db, rep, "queue_probe", None)
    t = ai_telemetry.ai_telemetry(db, principal(db, world, Role.ADMIN))
    reasons = {r.reason: r.count for r in t.rejections}
    assert reasons.get("forbidden", 0) >= 1
    assert all(set(r.model_dump()) == {"reason", "count"} for r in t.rejections)
    db.close()


def test_a_documented_window_says_the_source_holds_more(db, world):
    """The Case Brief reads the latest five outreach records: complete for what it promises,
    but the state says the source holds more."""
    cm = principal(db, world, Role.CARE_MANAGER)
    windows = []
    for pid in _panel(db, world, 100):
        touch = next(c for c in build(db, cm, "patient", pid).categories if c.category == "touch")
        if touch.total and touch.total > touch.count:
            windows.append(touch)
        else:
            assert touch.reason is None
    assert windows, "the synthetic world has patients with more than five outreach records"
    assert all(t.state is CategoryState.INCLUDED and t.reason == "window" for t in windows)
