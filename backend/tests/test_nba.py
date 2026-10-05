from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pytest
from conftest import seeded_session
from sqlalchemy import delete, select

from app import pipeline
from app.core import clock
from app.core.engine_config import DEFAULTS
from app.datagen.generate import GenConfig, generate
from app.features.engagement import EngagementState
from app.models import AuditLog, Consent, Content, ModelVersion, Nba, NbaCandidate
from app.models.enums import MlrStatus, NbaStatus
from app.nba import gates
from app.nba.engine import Candidate, decide, run_cycle

# Synthetic history ends on the day it is generated.
DEFAULT_AS_OF = clock.today()

TODAY = date(2026, 9, 30)
CAPS = DEFAULTS["frequency_caps"]
LIVE = (NbaStatus.READY_FOR_REVIEW, NbaStatus.BLOCKED)


def content(**overrides) -> Content:
    base = dict(
        content_id="C1",
        title="t",
        body="b",
        audience="HCP",
        action_type="share_study",
        topic="diabetes_outcomes",
        measure="diabetes",
        channels=["email", "portal"],
        mlr_status=MlrStatus.APPROVED,
        effective_date=TODAY - timedelta(days=30),
        expiry_date=TODAY + timedelta(days=300),
    )
    return Content(**{**base, **overrides})


def consent(channel="sms", granted=True, start=-100, end=None, purpose="outreach") -> Consent:
    return Consent(
        patient_id="P",
        purpose=purpose,
        channel=channel,
        granted=granted,
        effective_from=TODAY + timedelta(days=start),
        effective_to=TODAY + timedelta(days=end) if end is not None else None,
    )


# --- Gates --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "channel", "expected"),
    [
        ({}, "email", []),
        ({"mlr_status": MlrStatus.PENDING, "effective_date": None}, "email", ["mlr_pending"]),
        ({"mlr_status": MlrStatus.REJECTED}, "email", ["mlr_rejected"]),
        ({"expiry_date": TODAY}, "email", ["mlr_expired"]),
        ({"effective_date": TODAY + timedelta(days=1)}, "email", ["mlr_not_effective"]),
        ({"audience": "PATIENT"}, "email", ["audience_mismatch"]),
        ({}, "sms", ["channel_not_allowed"]),
    ],
)
def test_content_gate(overrides, channel, expected):
    assert gates.content_failures(content(**overrides), "HCP", channel, TODAY) == expected


@pytest.mark.parametrize(
    ("records", "expected"),
    [
        ([consent()], True),
        ([], False),
        ([consent(granted=False)], False),
        ([consent(channel="email")], False),
        ([consent(end=-1)], False),  # withdrawn yesterday
        ([consent(end=0)], False),  # withdrawn today
        ([consent(start=1)], False),  # not yet in effect
        ([consent(purpose="provider_sharing")], False),
        ([consent(end=-10), consent(granted=False, start=-10)], False),
    ],
)
def test_consent_gate(records, expected):
    assert gates.has_consent(records, "sms", TODAY) is expected


def test_frequency_gate():
    cap = {"window_days": 14, "max_touches": 2, "min_gap_days": 3}
    state = EngagementState()
    assert gates.frequency_failures(state, TODAY, cap) == []
    state.record([], TODAY - timedelta(days=10), True)
    assert gates.frequency_failures(state, TODAY, cap) == []
    state.record([], TODAY - timedelta(days=2), False)
    assert gates.frequency_failures(state, TODAY, cap) == ["frequency_cap", "min_gap"]
    assert gates.frequency_failures(state, TODAY + timedelta(days=5), cap) == []


def test_patient_touch_needs_both_content_and_consent():
    item = content(audience="PATIENT", channels=["sms"])
    state = EngagementState()
    assert gates.evaluate("PATIENT", item, "sms", TODAY, [consent()], state, CAPS) == []
    assert gates.evaluate("PATIENT", item, "sms", TODAY, [], state, CAPS) == ["consent_missing"]
    pending = content(audience="PATIENT", channels=["sms"], mlr_status=MlrStatus.PENDING)
    failures = gates.evaluate("PATIENT", pending, "sms", TODAY, [], state, CAPS)
    assert failures == ["mlr_pending", "consent_missing"]
    assert not gates.compliance_ok(failures) and not gates.consent_ok(failures)


# --- Selection ----------------------------------------------------------------


def cand(score, failures=(), cid="C1", channel="email") -> Candidate:
    c = Candidate("HCP", "H1", "share_study", channel, content(content_id=cid), {}, list(failures))
    c.score = score
    return c


def test_decide_picks_best_eligible():
    d = decide([cand(10, cid="A"), cand(30, cid="B"), cand(20, cid="C")], DEFAULTS)
    assert (d.outcome, d.chosen.content.content_id, d.withheld) == ("ready", "B", None)


def test_decide_reports_withheld_better_option():
    d = decide([cand(40, ["mlr_pending"], cid="A"), cand(30, cid="B")], DEFAULTS)
    assert d.outcome == "ready" and d.chosen.content.content_id == "B"
    assert d.withheld["content_id"] == "A" and d.withheld["failures"] == ["mlr_pending"]
    # A gated option that is not clearly better is not worth mentioning.
    d = decide([cand(31, ["mlr_pending"], cid="A"), cand(30, cid="B")], DEFAULTS)
    assert d.withheld is None


def test_decide_blocks_when_nothing_is_eligible():
    d = decide([cand(40, ["mlr_pending"]), cand(30, ["mlr_expired"], cid="B")], DEFAULTS)
    assert (d.outcome, d.status) == ("blocked", NbaStatus.BLOCKED)
    assert d.chosen.score == 40


def test_decide_defers_under_frequency_cap_instead_of_blocking():
    d = decide([cand(40, ["frequency_cap"]), cand(30, ["mlr_pending", "min_gap"])], DEFAULTS)
    assert (d.outcome, d.chosen) == ("deferred", None)


def test_decide_skips_low_value_touches():
    assert decide([cand(5)], DEFAULTS, min_score=8).outcome == "none"
    assert decide([], DEFAULTS).outcome == "none"


# --- Full cycle on seeded data --------------------------------------------------


@pytest.fixture(scope="module")
def cycled(tmp_path_factory):
    """The real demo dataset (default seed and scale), so these double as acceptance tests."""
    db = seeded_session(tmp_path_factory.mktemp("models"), GenConfig())
    result = run_cycle(db)
    db.commit()
    yield db, result
    db.close()


def latest(db, target_id) -> Nba | None:
    return db.scalar(
        select(Nba).where(Nba.target_id == target_id, Nba.status.in_(LIVE)).order_by(Nba.id.desc())
    )


def test_ready_recommendations_never_violate_a_gate(cycled):
    db, _ = cycled
    contents = {c.content_id: c for c in db.scalars(select(Content))}
    ready = db.scalars(select(Nba).where(Nba.status == NbaStatus.READY_FOR_REVIEW)).all()
    assert len(ready) > 200
    for nba in ready:
        item = contents[nba.content_id]
        assert gates.content_failures(item, nba.target_type, nba.channel, TODAY) == [], nba.id
        if nba.target_type == "PATIENT":
            records = db.scalars(select(Consent).where(Consent.patient_id == nba.target_id)).all()
            assert gates.has_consent(records, nba.channel, TODAY), nba.id


def test_every_recommendation_is_explained_and_audited(cycled):
    db, _ = cycled
    audited = Counter(db.scalars(select(AuditLog.nba_id).where(AuditLog.nba_id.is_not(None))))
    for nba in db.scalars(select(Nba)):
        assert nba.rationale and nba.reason_codes, nba.id
        assert {r["kind"] for r in nba.reason_codes} >= {"who", "action", "channel", "timing"}
        assert audited[nba.id] >= 1, nba.id
        if nba.status == NbaStatus.BLOCKED:
            assert nba.block_reason
        else:
            assert {"mlr_approved"} <= {r["code"] for r in nba.reason_codes}


def test_blocked_recommendations_carry_the_failed_gate_in_audit(cycled):
    db, _ = cycled
    blocked = db.scalars(select(Nba).where(Nba.status == NbaStatus.BLOCKED)).all()
    assert blocked
    for nba in blocked:
        log = db.scalar(select(AuditLog).where(AuditLog.nba_id == nba.id))
        assert log.action == "nba_blocked"
        assert log.compliance_ok is False or log.consent_ok is False
        assert log.detail["gate_failures"]


def test_one_recommendation_per_target_per_cycle(cycled):
    db, result = cycled
    rows = db.execute(
        select(Nba.target_type, Nba.target_id).where(Nba.cycle_id == result.cycle.id)
    ).all()
    assert len(rows) == len(set(rows))
    ranks = db.execute(
        select(NbaCandidate.nba_id, NbaCandidate.rank).where(NbaCandidate.nba_id.is_not(None))
    ).all()
    assert len(ranks) == len(set(ranks))


def test_scenario_1_cardiologist_gets_approved_study_by_email(cycled):
    db, _ = cycled
    nba = latest(db, "HCP_0001")
    item = db.get(Content, nba.content_id)
    assert (nba.status, nba.channel, nba.action) == ("ready_for_review", "email", "share_study")
    assert item.measure in ("hypertension", "cholesterol") and item.mlr_status == "approved"


def test_scenario_3_forgetful_patient_gets_text_refill_reminder(cycled):
    db, _ = cycled
    nba = latest(db, "PAT_00001")
    assert (nba.status, nba.action, nba.channel) == ("ready_for_review", "refill_nudge", "sms")
    assert "16 days without medication" in nba.rationale


def test_scenario_4_unconsented_preferred_channel_is_avoided_and_explained(cycled):
    db, _ = cycled
    nba = latest(db, "PAT_00002")
    assert nba.status == "ready_for_review" and nba.channel != "sms"
    assert nba.withheld == {
        "kind": "preferred_channel",
        "channel": "sms",
        "failures": ["consent_missing"],
    }
    assert "Stated preferred channel (text message) was not used" in nba.rationale


def test_opted_out_patient_is_blocked_and_adherent_patient_is_left_alone(cycled):
    db, _ = cycled
    blocked = latest(db, "PAT_00004")
    assert blocked.status == "blocked" and "not consented" in blocked.block_reason
    log = db.scalar(select(AuditLog).where(AuditLog.nba_id == blocked.id))
    assert log.consent_ok is False
    assert latest(db, "PAT_00003") is None


def test_cost_barrier_and_never_starter_get_fitting_actions(cycled):
    db, _ = cycled
    assert latest(db, "PAT_00005").action == "cost_support"
    never_started = latest(db, "PAT_00006")
    assert never_started.action != "refill_nudge"
    assert "never been filled" in never_started.rationale


def test_scenario_2_mlr_approval_unlocks_the_withheld_content(cycled):
    db, first = cycled
    before = latest(db, "HCP_0002")
    assert before.status == "ready_for_review"
    held = db.get(Content, before.withheld["content_id"])
    assert held.topic == "diabetes_outcomes" and held.mlr_status == MlrStatus.PENDING
    assert before.withheld["failures"] == ["mlr_pending"]

    held.mlr_status = MlrStatus.APPROVED
    held.effective_date = TODAY
    held.expiry_date = TODAY + timedelta(days=730)
    second = run_cycle(db)
    db.commit()

    after = latest(db, "HCP_0002")
    assert after.cycle_id == second.cycle.id and after.content_id == held.content_id
    assert after.withheld is None
    # The earlier cycle's open recommendations were superseded, not left to pile up.
    db.refresh(before)
    assert before.status == NbaStatus.EXPIRED
    assert second.stats["expired_superseded"] == (
        first.stats["patient_ready"]
        + first.stats["patient_blocked"]
        + first.stats["hcp_ready"]
        + first.stats.get("hcp_blocked", 0)
    )


def test_engine_works_without_trained_models(db):
    generate(db, GenConfig(seed=3, n_patients=150, n_hcps=30, n_reps=3, n_care_managers=2))
    pipeline.refresh_features(db)
    db.execute(delete(ModelVersion))
    result = run_cycle(db)
    assert result.cycle.as_of_date == DEFAULT_AS_OF
    assert result.stats["patient_ready"] > 10 and result.stats["hcp_ready"] > 5


def test_decisioning_code_never_reads_hidden_traits():
    """sim_latent is simulator-only ground truth; the engine must not be able to cheat."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    offenders = [
        str(path.relative_to(app_dir))
        for package in ("nba", "features", "scoring", "audit")
        for path in (app_dir / package).rglob("*.py")
        if "SimLatent" in path.read_text(encoding="utf-8")
        or "sim_latent" in path.read_text(encoding="utf-8").replace("excludes sim_latent", "")
    ]
    offenders += ["pipeline.py"] * ("SimLatent" in (app_dir / "pipeline.py").read_text("utf-8"))
    assert offenders == []
