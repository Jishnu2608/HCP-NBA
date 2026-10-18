"""F-14: policy boundaries come from one approvals register (`app.ai.policy.approvals`), empty
today (owner decisions 2026-10-19), and can open later without reworking the layer.

The future path is exercised with approvals set inside a test only: an approved patient-facing
audience still needs a consent purpose and an evaluation reference at registration, and the
patient's consent in effect when the generation runs. Consent alone never opens an audience.
"""

import dataclasses
import json
from datetime import date, timedelta

import pytest
from context_world import fresh_world, principal

from app.ai import orchestrator, registry, sources
from app.ai.contracts import FailureCategory, GenerationStatus
from app.ai.engine import Consumer, PolicyBlocked, build_context
from app.ai.evaluation import probes
from app.ai.policy import approvals, project
from app.ai.sources import SourceStatus
from app.core.config import get_settings
from app.models import Consent
from app.models.enums import Role

APPROVAL = approvals.Approval(
    decision="test-only approval (never in the register)",
    approved_on=date(2026, 10, 19),
    approved_by="test",
    scope="patient-facing synthetic test operation",
    evaluation="tests/test_ai_policy_approvals.py",
)


@pytest.fixture(scope="module")
def world():
    return fresh_world()


@pytest.fixture
def db(world):
    session = world.session_factory()
    yield session
    session.rollback()
    session.close()


def test_the_register_is_empty_and_everything_stays_blocked():
    assert set(approvals.AUDIENCES.values()) == {None}
    assert set(approvals.FIELD_GROUPS.values()) == {None}
    approvals.validate()  # every pending field belongs to a group
    for source_id, audience in approvals.SOURCE_AUDIENCE.items():
        source = sources.get_source(source_id)
        assert source.status is SourceStatus.POLICY_BLOCKED
        assert source.status_reason == f"{audience}_ai_not_approved"
    assert not any(approvals.field_approved(r, n) for r, n in approvals.PENDING_GROUP)


def test_the_register_refuses_incomplete_approvals_and_unmapped_pending_fields(monkeypatch):
    monkeypatch.setitem(approvals.FIELD_GROUPS, "group_adherence",
                        dataclasses.replace(APPROVAL, evaluation=""))  # fmt: skip
    with pytest.raises(approvals.ApprovalError, match="evaluation"):
        approvals.validate()
    monkeypatch.setitem(approvals.FIELD_GROUPS, "group_adherence", None)
    monkeypatch.delitem(approvals.PENDING_GROUP, ("content", "body"))
    with pytest.raises(approvals.ApprovalError, match="differ"):
        approvals.validate()


def test_an_approved_data_group_opens_only_its_fields(monkeypatch):
    row = {
        "measure": "pdc_statin", "patients": 40, "adherent_share": 0.7, "persistence_share": 0.6,
        "courses": 44, "scope": "panel", "suppressed": False,
    }  # fmt: skip
    from app.ai.policy import FIELD_POLICY

    closed = project(
        "quality_measure", {k: v for k, v in row.items() if k in FIELD_POLICY["quality_measure"]}
    )
    assert "adherent_share" not in closed.model
    monkeypatch.setitem(approvals.FIELD_GROUPS, "group_adherence", APPROVAL)
    opened = project("quality_measure", {k: v for k, v in row.items() if k in closed.decisions})
    assert opened.model["adherent_share"] == 0.7
    # MLR wording: "body" is globally prohibited free text, so approving the group is not
    # enough; the approval must name it explicitly.
    monkeypatch.setitem(approvals.FIELD_GROUPS, "mlr_wording", APPROVAL)
    assert approvals.field_approved("content", "title")
    assert not approvals.field_approved("content", "body")
    monkeypatch.setitem(approvals.FIELD_GROUPS, "mlr_wording",
                        dataclasses.replace(APPROVAL, overrides_prohibited=("body",)))  # fmt: skip
    assert approvals.field_approved("content", "body")


@pytest.fixture
def approved_patient_audience(monkeypatch):
    """Patient-facing AI approved for one test only; the source is re-registered with the
    status the register now gives it, and restored afterwards."""
    from app.ai.sources import care

    monkeypatch.setitem(approvals.AUDIENCES, "patient_facing", APPROVAL)
    original = sources.get_source("patient_self")
    sources.unregister_source("patient_self")
    sources.register_source(sources._audience_gate(care.PATIENT_SELF))
    yield
    sources.unregister_source("patient_self")
    sources.register_source(original)


def _patient_spec(**kw):
    base = probes._spec("probe_patient_facing", "patient_self", ("own_request",),
                        probes.Summary, permission="self:health:manage",
                        cited_fields=(("summary", "summary_citations"),))  # fmt: skip
    return dataclasses.replace(base, **kw)


def test_without_approval_consent_alone_opens_nothing(db, world):
    patient = principal(db, world, Role.PATIENT)
    db.add(Consent(patient_id=patient.patient_id, purpose="provider_sharing", granted=True,
                   effective_from=date.today() - timedelta(days=1)))  # fmt: skip
    db.flush()
    spec = _patient_spec(consent_purpose="provider_sharing", evaluation_ref="x")
    with pytest.raises(ValueError, match="policy_blocked"):
        registry.validate(spec)
    from app.ai.contracts import ContextRequest

    with pytest.raises(PolicyBlocked):
        build_context(db, patient, ContextRequest(operation="t", subject_type="patient_self"),
                      Consumer(id="t", permission="self:health:manage",
                               categories=("own_request",)))  # fmt: skip


def test_an_approved_patient_audience_still_needs_consent_purpose_evaluation_and_consent(
    db, world, approved_patient_audience
):
    with pytest.raises(ValueError, match="evaluation"):
        registry.validate(_patient_spec(consent_purpose="provider_sharing"))
    with pytest.raises(ValueError, match="consent purpose"):
        registry.validate(_patient_spec(evaluation_ref="report.md"))
    spec = _patient_spec(consent_purpose="provider_sharing", evaluation_ref="report.md")
    registry.validate(spec)
    with pytest.raises(ValueError, match="test-only"):
        registry.register(spec)  # still never registrable from a test

    patient = principal(db, world, Role.PATIENT)
    settings = get_settings().model_copy(update={"ai_operation_modes": {spec.id: "shadow"}})
    ctx = orchestrator._context(db, patient, spec, None)
    if not ctx.model_items:
        pytest.skip("the fixture patient has no visible request")
    item = ctx.model_items[0]
    answer = json.dumps({"summary": item.text, "summary_citations": [item.id]})

    def run():
        provider = probes.ScriptedProvider(text=answer)
        r = orchestrator.execute(db, patient, spec, None, settings=settings,
                                 provider_factory=lambda s: provider)  # fmt: skip
        return r, provider

    db.query(Consent).filter_by(patient_id=patient.patient_id,
                                purpose="provider_sharing").delete()  # fmt: skip
    db.flush()
    r, p = run()  # approved, but this patient has not consented: refused before the model
    assert r.failure is FailureCategory.DATA_POLICY and not p.requests
    assert any(s["step"] == "consent" and s["outcome"] == "missing" for s in r.trace)
    db.add(Consent(patient_id=patient.patient_id, purpose="provider_sharing", granted=True,
                   effective_from=date.today() - timedelta(days=1)))  # fmt: skip
    db.flush()
    r, p = run()  # approval and consent: the generation runs, still through every check
    assert r.status is GenerationStatus.COMPLETED and len(p.requests) == 1


def test_hcp_facing_audience_stays_blocked_whatever_the_operation_declares():
    spec = probes._spec("probe_hcp_facing", "consultation_queue", ("consultation",),
                        probes.Summary, permission="self:consultations:manage",
                        cited_fields=(("summary", "summary_citations"),),
                        consent_purpose="provider_sharing", evaluation_ref="x")  # fmt: skip
    with pytest.raises(ValueError, match="policy_blocked"):
        registry.validate(spec)
