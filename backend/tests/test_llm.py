import json

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.datagen.generate import GenConfig, generate
from app.llm import service
from app.llm.base import DraftOutput, DraftRequest, DraftResult, MessageVariant
from app.llm.template import TemplateProvider
from app.llm.validator import DraftValidationError, validate
from app.models import AuditLog, MessageDraft, Nba
from app.models.enums import NbaStatus
from app.nba.engine import run_cycle


def request(**overrides) -> DraftRequest:
    base = dict(
        nba_id=1,
        target_type="PATIENT",
        action="refill_nudge",
        channel="sms",
        content_id="CNT_023",
        content_title="Refill reminder (text)",
        content_body="Hi {{ first_name }}, your {{ drug_name }} refill is due.",
        first_name="Margaret",
        last_name="Doyle",
        drug_name="metformin",
        reasons=["16 days without medication on hand", "Responded to 2 of 2 texts"],
    )
    return DraftRequest(**{**base, **overrides})


def output(body="Hi Margaret, your metformin refill is due.", subject=None, summary="Why.") -> dict:
    return {"rationale_summary": summary, "variants": [{"subject": subject, "body": body}]}


# --- Template provider ----------------------------------------------------------


@pytest.mark.parametrize(
    ("target_type", "action", "channel"),
    [
        ("PATIENT", "refill_nudge", "sms"),
        ("PATIENT", "refill_nudge", "email"),
        ("PATIENT", "education", "portal"),
        ("PATIENT", "check_in", "phone"),
        ("PATIENT", "cost_support", "phone"),
        ("HCP", "share_study", "email"),
        ("HCP", "hcp_education", "portal"),
        ("HCP", "rep_visit", "rep_visit"),
    ],
)
def test_template_output_always_passes_validation(target_type, action, channel):
    r = request(target_type=target_type, action=action, channel=channel, specialty="Endocrinology")
    result = TemplateProvider().draft(r)
    checked = validate(result.output, r)
    assert len(checked.variants) == 2
    assert all("{{" not in v.body for v in checked.variants)
    first = (checked.variants[0].subject or "") + checked.variants[0].body
    assert ("Margaret" if target_type == "PATIENT" else "Doyle") in first


# --- Validator -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "channel", "problem"),
    [
        ("not json", "sms", "malformed output"),
        ({"variants": []}, "sms", "malformed output"),
        (output(body=""), "sms", "empty body"),
        (output(body="Hi {{ first_name }}"), "sms", "unresolved placeholder"),
        (output(body="x" * 321), "sms", "text message over"),
        (output(subject="Hello"), "sms", "cannot have a subject"),
        (output(), "email", "subject required"),
        (output(body="See CNT_099 instead"), "sms", "references other content"),
        (output(body="Details at https://example.com"), "sms", "contains a link"),
        (output(body="This will cure your diabetes"), "sms", "disallowed wording 'cure'"),
        (output(body="You can stop taking it"), "sms", "disallowed wording 'stop taking'"),
        (output(summary=" "), "sms", "empty rationale summary"),
    ],
)
def test_validator_rejects(raw, channel, problem):
    with pytest.raises(DraftValidationError) as exc:
        validate(raw, request(channel=channel))
    assert problem in str(exc.value)


def test_validator_accepts_good_output_in_any_input_form():
    r = request()
    # "secure" contains "cure" but is not the banned word; the approved content id is allowed.
    good = output(body="Hi Margaret, a secure reminder about CNT_023: refill is due.")
    assert validate(good, r).variants[0].body.startswith("Hi Margaret")
    assert validate(json.dumps(good), r) == validate(DraftOutput.model_validate(good), r)


# --- Service: storage, fallback, audit ------------------------------------------


class FakeProvider:
    name = "fake"

    def __init__(self, body=None, error=None):
        self.body, self.error = body, error

    def draft(self, r: DraftRequest) -> DraftResult:
        if self.error:
            raise self.error
        return DraftResult(
            output=DraftOutput(
                rationale_summary="Model summary.",
                variants=[MessageVariant(subject=None, body=self.body)],
            ),
            provider=self.name,
            model="fake-1",
            usage={"input_tokens": 10, "output_tokens": 5},
        )


@pytest.fixture
def cycled(db, monkeypatch):
    """A cycle, with external providers allowed to receive identifiable data (the operator
    setting the fake providers below stand in for)."""
    monkeypatch.setattr(get_settings(), "allow_external_identifiable_data", True)
    generate(db, GenConfig(seed=5, n_patients=120, n_hcps=24, n_reps=3, n_care_managers=2))
    result = run_cycle(db)
    return db, result


def test_external_provider_gets_no_identifiable_data_unless_allowed(cycled, monkeypatch):
    db, _ = cycled
    monkeypatch.setattr(get_settings(), "allow_external_identifiable_data", False)
    called = []

    class Recording(FakeProvider):
        def draft(self, request):
            called.append(request)
            return super().draft(request)

    rows = service.draft_for_nba(db, sms_nba(db), Recording(body="Hi."))
    assert called == [] and {r.provider for r in rows} == {"template"}


def sms_nba(db) -> Nba:
    return db.scalar(
        select(Nba).where(Nba.status == NbaStatus.READY_FOR_REVIEW, Nba.channel == "sms")
    )


def test_cycle_drafts_every_ready_recommendation_and_no_blocked_one(cycled):
    db, result = cycled
    count = service.draft_cycle(db, result.cycle.id)
    ready = db.scalars(select(Nba).where(Nba.status == NbaStatus.READY_FOR_REVIEW)).all()
    assert count == len(ready) > 20
    assert set(db.scalars(select(MessageDraft.nba_id))) == {n.id for n in ready}
    assert all(n.rationale_summary for n in ready)
    selected = db.scalars(select(MessageDraft).where(MessageDraft.is_selected)).all()
    assert len(selected) == len(ready)
    blocked = db.scalar(select(Nba).where(Nba.status == NbaStatus.BLOCKED))
    with pytest.raises(ValueError, match="blocked"):
        service.draft_for_nba(db, blocked)


def test_valid_model_output_is_stored_and_audited(cycled):
    db, _ = cycled
    nba = sms_nba(db)
    rows = service.draft_for_nba(db, nba, FakeProvider(body="Hi, your refill is due."))
    assert [(r.provider, r.model, r.body) for r in rows] == [
        ("fake", "fake-1", "Hi, your refill is due.")
    ]
    assert nba.rationale_summary == "Model summary."
    log = db.scalar(select(AuditLog).where(AuditLog.action == "draft_generated"))
    assert log.nba_id == nba.id and log.detail["usage"]["output_tokens"] == 5


@pytest.mark.parametrize(
    ("provider", "reason"),
    [
        (FakeProvider(body="Visit https://bad.example now"), "output rejected"),
        (FakeProvider(body="This will cure you"), "output rejected"),
        (FakeProvider(error=TimeoutError("slow")), "provider error: TimeoutError"),
    ],
)
def test_bad_model_output_falls_back_to_template(cycled, provider, reason):
    db, _ = cycled
    nba = sms_nba(db)
    rows = service.draft_for_nba(db, nba, provider)
    assert {r.provider for r in rows} == {"template"}
    assert all("https://" not in r.body and "cure" not in r.body for r in rows)
    log = db.scalar(select(AuditLog).where(AuditLog.action == "draft_fallback"))
    assert log.nba_id == nba.id and reason in log.reason


def test_redrafting_keeps_human_edits(cycled):
    db, _ = cycled
    nba = sms_nba(db)
    first = service.draft_for_nba(db, nba)
    first[0].body, first[0].edited_by_user_id = "Edited by the care manager.", 1
    db.flush()
    service.draft_for_nba(db, nba, FakeProvider(body="Fresh model wording."))
    drafts = db.scalars(
        select(MessageDraft).where(MessageDraft.nba_id == nba.id).order_by(MessageDraft.variant_no)
    ).all()
    assert [d.body for d in drafts] == ["Edited by the care manager.", "Fresh model wording."]
    assert [d.is_selected for d in drafts] == [True, False]


def test_claude_prompt_and_schema_match_the_contract():
    from app.llm.claude import SCHEMA, build_prompt

    prompt = build_prompt(request())
    assert "CNT_023" in prompt and "16 days without medication" in prompt
    assert SCHEMA["additionalProperties"] is False
    assert set(SCHEMA["required"]) == set(DraftOutput.model_fields)
