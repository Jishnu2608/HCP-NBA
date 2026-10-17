"""Canary leakage tests: planted free text, consent detail, identifiers and a typed medicine name
must never enter a prohibited destination.

Synthetic canaries sit in a care request reason, a care note carrying an injection attempt, an
HCP answer, an interaction note and outcome reason, a condition typed as "other", and a
medicine name typed outside the vocabulary. Every check looks at the destination itself (the
provider payload, items, aggregates, errors, logs, audit rows, generation rows, job parameters)
rather than at a final output that might merely omit the value.
"""

import json
import logging
from datetime import timedelta

import pytest
from context_world import RecordingProvider, build, fresh_world, principal
from sqlalchemy import select

from app import jobs
from app.ai import fixtures, orchestrator, registry
from app.ai.contracts import SubjectType
from app.ai.operations import case_brief
from app.ai.policy.engine import UNLISTED_MEDICATION as UNLISTED
from app.core.config import get_settings
from app.models import AiGeneration, AuditLog, Interaction, Job, PatientCondition
from app.models.enums import Role
from app.models.tables import utcnow
from app.services.errors import NotFound

NOTE_CANARY = "CANARY-INTERACTION-NOTE-HK19 call me at home please"
REASON_CANARY = "CANARY-OUTCOME-REASON-PL07 patient said leave me alone"
CONDITION_CANARY = "CANARY-CONDITION-OTHER-WB55 rare thing"
ALL = (*fixtures.CANARIES, NOTE_CANARY, REASON_CANARY, CONDITION_CANARY)
HEALTH_KINDS = ("patient", "rx", "touch", "care_request", "condition", "medication_record")


@pytest.fixture(scope="module")
def world():
    w = fresh_world()
    db = w.session_factory()
    pid = w.subjects["patient"]
    db.add(
        Interaction(
            target_type="PATIENT",
            target_id=pid,
            channel="phone",
            int_ts=utcnow() - timedelta(days=1),
            type="call",
            outcome="no_response",
            source="manual",
            note=NOTE_CANARY,
            outcome_reason=REASON_CANARY,
        )
    )
    db.add(
        PatientCondition(
            patient_id=pid,
            condition="other",
            other_text=CONDITION_CANARY,
            origin="patient",
            status="reported",
            reported_at=utcnow(),
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


def _no_canary(text: str, where: str) -> None:
    for c in ALL:
        assert c not in text, f"canary {c[:20]} found in {where}"


def test_canaries_were_planted_and_reach_the_reader_only(db, world):
    """The reader still sees the free text verbatim (as reader notes), so the absence from model
    views below is a real separation, not a missing record."""
    ctx = build(
        db,
        principal(db, world, Role.CARE_MANAGER),
        "patient",
        world.subjects["patient"],
        categories=HEALTH_KINDS,
    )
    notes = " ".join(n.text for n in ctx.reader_notes)
    assert fixtures.CANARY_NOTE in notes or fixtures.CANARY_ANSWER in notes
    assert NOTE_CANARY in notes and REASON_CANARY in notes
    for c in (fixtures.CANARY_REASON, fixtures.CANARY_NOTE, NOTE_CANARY, CONDITION_CANARY):
        assert any(c in w for w in ctx.withheld_texts)


@pytest.mark.parametrize(
    "subject_type, role, categories",
    [
        ("patient", Role.CARE_MANAGER, HEALTH_KINDS),
        ("care_request", Role.CARE_MANAGER, ()),
        ("care_queue", Role.CARE_MANAGER, ()),
        ("care_workflow", Role.CARE_MANAGER, ()),
        ("consultation_queue", Role.HCP, ()),
        ("patient_self", Role.PATIENT, ()),
    ],
)
def test_no_canary_in_any_item_direct_nested_derived_or_aggregate(
    db, world, subject_type, role, categories
):
    from app.ai.diagnostics import _fixture_subject

    ctx = build(
        db,
        principal(db, world, role),
        subject_type,
        _fixture_subject(world, SubjectType(subject_type), "auto"),
        categories=categories,
    )
    _no_canary(json.dumps([i.model_dump(mode="json") for i in ctx.items]), f"{subject_type} items")
    _no_canary(json.dumps(ctx.facts, default=str), f"{subject_type} facts")
    # Serialised, the context carries free text only inside reader notes, each under the fixed
    # untrusted label (for the person, never a model); everything else is canary-free.
    _no_canary(ctx.model_dump_json(exclude={"reader_notes"}), f"{subject_type} serialised context")
    assert all(n.label == "Untrusted; review for clinical content." for n in ctx.reader_notes)


def test_a_typed_medicine_name_is_replaced_in_model_text(db, world):
    ctx = build(
        db,
        principal(db, world, Role.CARE_MANAGER),
        "patient",
        world.subjects["patient"],
        categories=("medication_record",),
    )
    texts = [i.text for i in ctx.model_items if i.kind == "medication_record"]
    assert any(UNLISTED in t for t in texts)
    assert fixtures.CANARY_DRUG not in " ".join(texts)
    assert fixtures.CANARY_DRUG.lower() not in [v.lower() for v in ctx.vocabulary]


def test_asking_for_excluded_fields_gets_nothing(db, world):
    from app.ai.engine import Consumer

    consumer = Consumer(id="t", permission=None, categories=("consent",), model_calls=False)
    ctx = build(
        db,
        principal(db, world, Role.CARE_MANAGER),
        "patient",
        world.subjects["patient"],
        categories=("consent",),
        consumer=consumer,
    )
    assert ctx.model_items == []


def test_errors_carry_no_record_content(db, world):
    with pytest.raises(NotFound) as err:
        build(
            db,
            principal(db, world, Role.CARE_MANAGER),
            "patient",
            world.subjects["patient_out_of_scope"],
        )
    assert world.subjects["patient_out_of_scope"] not in str(err.value)
    _no_canary(str(err.value), "error message")


# --- A generation over health categories: payload, rows, audit, jobs and logs --------------------


def _reply(request) -> str:
    ids = request.json_schema["properties"]["summary_citations"]["items"]["enum"]
    return json.dumps(
        {
            "summary": "Outreach continues.",
            "summary_citations": [ids[0]],
            "suggested_focus": "Review the outreach.",
            "focus_citations": [ids[0]],
        }
    )


class Replying(RecordingProvider):
    def generate(self, request):
        self.reply = _reply(request)
        return super().generate(request)


@pytest.fixture
def probe_operation(monkeypatch):
    spec = registry.register(
        orchestrator_spec := __import__("dataclasses").replace(
            case_brief.SPEC, id="leak_probe", context_kinds=HEALTH_KINDS, mode_setting=None
        )
    )
    monkeypatch.setattr(get_settings(), "ai_operation_modes", {"leak_probe": "shadow"})
    monkeypatch.setattr(get_settings(), "ai_provider", "ollama")
    yield orchestrator_spec
    registry._REGISTRY.pop(spec.id, None)


def test_no_canary_reaches_the_provider_or_any_record_of_the_generation(
    world, probe_operation, monkeypatch, caplog
):
    provider = Replying()
    monkeypatch.setattr(orchestrator, "get_provider", lambda settings=None: provider)
    db = world.session_factory()
    cm = principal(db, world, Role.CARE_MANAGER)
    caplog.set_level(logging.DEBUG)
    gen, created = orchestrator.submit(db, cm, "leak_probe", world.subjects["patient"])
    assert created
    jobs.run_pending(world.session_factory)
    db.expire_all()
    gen = db.get(AiGeneration, gen.id)
    assert gen.status == "completed", (gen.status, gen.failure, gen.gate_reasons)
    assert provider.requests, "the provider was called"
    _no_canary(provider.payload(), "provider payload")
    row = {c.name: getattr(gen, c.name) for c in AiGeneration.__table__.columns}
    _no_canary(json.dumps(row, default=str), "ai_generation row")
    audits = db.scalars(select(AuditLog).where(AuditLog.action.like("ai_generation%"))).all()
    blob = json.dumps([[a.entity_id, a.reason, a.detail] for a in audits], default=str)
    _no_canary(blob, "audit rows")
    assert world.subjects["patient"] not in json.dumps([a.detail for a in audits], default=str)
    job = db.get(Job, gen.job_id)
    _no_canary(json.dumps(job.params), "job params")
    assert set(job.params) == {"generation_id"}
    _no_canary(caplog.text, "logs")
    db.close()
