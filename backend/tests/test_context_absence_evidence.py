"""Absence, evidence and contradiction: the shared checks distinguish "not recorded" from "not in
this context", and reject unsupported facts, statuses, codes and references.

Contexts are real (built by the engine on a fresh fixture world). States the world cannot
produce on demand (unavailable, truncated, stale) are set on a copy of a real context, which is
exactly what the checks read.
"""

import json

import pytest
from context_world import RecordingProvider, build, fresh_world, principal

from app import jobs
from app.ai import orchestrator, retrieval, safety
from app.ai.contracts import CategoryState, CategoryStatus
from app.ai.engine import Consumer
from app.ai.operations import case_brief
from app.core.config import get_settings
from app.models import AiGeneration, Nba
from app.models.enums import Role

CB = case_brief.MODEL_KINDS


@pytest.fixture(scope="module")
def world():
    return fresh_world()


@pytest.fixture
def db(world):
    session = world.session_factory()
    yield session
    session.rollback()
    session.close()


def _ctx(db, world, kinds=CB, pid=None, categories=()):
    cm = principal(db, world, Role.CARE_MANAGER)
    consumer = Consumer(id="t", permission=None, categories=kinds, model_calls=False)
    return build(
        db,
        cm,
        "patient",
        pid or world.subjects["patient"],
        categories=categories,
        consumer=consumer,
    )


def _kinds(ctx, text, cites=()):
    found, miscited = safety.check_text_fields(ctx, {"summary": (text, list(cites))})
    return {f.kind for f in found} | ({"miscited"} if miscited else set())


def _with_state(ctx, category, state):
    cats = [
        c.model_copy(update={"state": state}) if c.category == category else c
        for c in ctx.categories
    ]
    return ctx.model_copy(update={"categories": cats})


def _patient_without(db, world, kind):
    """A patient on the care manager's panel whose context holds no item of this kind."""
    from sqlalchemy import select

    from app.models import CareManagerPatient

    cm = principal(db, world, Role.CARE_MANAGER)
    for pid in db.scalars(
        select(CareManagerPatient.patient_id).where(
            CareManagerPatient.care_manager_user_id == cm.id
        )
    ):
        ctx = _ctx(db, world, pid=pid)
        if ctx.state_of(kind) is CategoryState.VERIFIED_EMPTY:
            return ctx
    pytest.skip(f"no patient without {kind} in this world")


# --- The eleven cases ---------------------------------------------------------------------------


def test_1_fact_present_and_included_cannot_be_called_missing(db, world):
    ctx = _ctx(db, world)
    assert ctx.state_of("touch") is CategoryState.INCLUDED
    assert "unsupported_absence:included" in _kinds(ctx, "No outreach is recorded.")


def test_2_fact_present_but_excluded_by_the_allowlist(db, world):
    full = _ctx(db, world)
    narrow = _ctx(db, world, kinds=("patient", "touch", "nba", "absent"))
    assert full.state_of("rx") is CategoryState.INCLUDED
    assert narrow.state_of("rx") is CategoryState.EXCLUDED_ALLOWLIST
    # The runtime probe's false claim (AI_Artifacts.md section 6.3), now rejected.
    sentence = "No refill status or days covered are recorded in the provided items."
    assert "unsupported_absence:excluded_allowlist" in _kinds(narrow, sentence)


def test_3_source_lacks_the_fact_so_the_claim_is_supported(db, world):
    ctx = _patient_without(db, world, "nba")
    found = _kinds(ctx, "No open recommendation is recorded.", [ctx.facts["absent_item"]])
    assert not {k for k in found if "absence" in k}


def test_4_source_not_queried(db, world):
    ctx = _ctx(db, world)  # care requests are not requested by the Case Brief allowlist
    assert ctx.state_of("care_request") is CategoryState.NOT_REQUESTED
    assert "unsupported_absence:not_requested" in _kinds(ctx, "No care requests are recorded.")


@pytest.mark.parametrize(
    "state", [CategoryState.UNAVAILABLE, CategoryState.TRUNCATED, CategoryState.STALE]
)
def test_5_6_7_unavailable_partial_or_stale_sources(db, world, state):
    ctx = _with_state(_patient_without(db, world, "nba"), "nba", state)
    assert f"unsupported_absence:{state.value}" in _kinds(
        ctx, "No open recommendation is recorded."
    )


def test_8_evidence_reference_that_does_not_exist(db, world):
    assert "invalid_citation" in _kinds(_ctx(db, world), "Outreach continues.", ["ctx:rx:99"])


def test_9_evidence_from_another_subject_or_version(db, world):
    ctx = _ctx(db, world)
    foreign = f"ctx:touch:{len([i for i in ctx.model_items if i.kind == 'touch']) + 7}"
    assert "invalid_citation" in _kinds(ctx, "Outreach continues.", [foreign])
    passage = retrieval.EvidencePassage(
        id="ev:content:1",
        source="content",
        category="content",
        source_id="C1",
        source_version="1",
        subject_ref="a",
        text="Content C1 version 1.",
        score=1.0,
    )
    old = retrieval.RetrievalResult(turn_id="t1", passages=[passage], insufficient=False)
    newer = retrieval.RetrievalResult(
        turn_id="t2", passages=[passage.model_copy(update={"source_version": "2"})]
    )
    assert [f.kind for f in retrieval.validate_passages(old, ["ev:content:1"], "s", newer)] == [
        "stale_evidence"
    ]
    assert [f.kind for f in retrieval.validate_passages(old, ["ev:content:9"], "s")] == [
        "invalid_citation"
    ]


def test_10_claims_that_contradict_the_recorded_status(db, world):
    from sqlalchemy import select

    ctx = _ctx(db, world)
    nba = db.scalar(
        select(Nba).where(
            Nba.target_id == world.subjects["patient"], Nba.status == "ready_for_review"
        )
    )
    if nba is None:
        pytest.skip("no open recommendation for this patient")
    assert "contradiction:status" in _kinds(ctx, "The recommendation was approved.")
    assert not {"contradiction:status", "fabricated_status"} & _kinds(
        ctx, "The recommendation is ready for review."
    )


@pytest.mark.parametrize(
    "text, kind",
    [
        ("The last refill was on 1999-01-02.", "fabricated_date"),
        ("Days covered fell to 0.07.", "fabricated_number"),
        ("Chosen because of high_risk_override.", "fabricated_code"),
        ("The request was rejected.", "fabricated_status"),
    ],
)
def test_11_invented_dates_metrics_reasons_and_statuses(db, world, text, kind):
    ctx = _patient_without(db, world, "nba")  # no status in the items: any status is invented
    assert kind in _kinds(ctx, text)


# --- What the checks must not reject (Case Brief wording that passed before) ------------------


@pytest.mark.parametrize(
    "text",
    [
        "No response to 4 of the last 5 outreach records.",
        "Recommend no further outreach for now.",
        "Two outreach attempts had no response.",
        # Runtime probe 2026-10-17: rejected by a first version of the status check.
        "Review the approved cost-support offer content to address the open recommendation.",
    ],
)
def test_ordinary_wording_is_not_an_absence_claim(db, world, text):
    found = _kinds(_ctx(db, world), text)
    assert not {k for k in found if "absence" in k or "status" in k}


# --- Conservative fallback in the orchestrator ------------------------------------------------


def test_an_incomplete_context_never_reaches_a_model(world, monkeypatch):
    """A context marked incomplete (truncated, stale or partly unavailable) makes the
    generation fall back to the template with `context_incomplete`; the provider is not called."""
    provider = RecordingProvider(reply="{}")
    monkeypatch.setattr(orchestrator, "get_provider", lambda settings=None: provider)
    monkeypatch.setattr(get_settings(), "ai_case_brief_mode", "shadow")
    real = orchestrator.build_context

    def incomplete(*a, **kw):
        ctx = real(*a, **kw)
        cats = [CategoryStatus(category="rx", state=CategoryState.STALE)]
        return ctx.model_copy(update={"complete": False, "categories": cats})

    db = world.session_factory()
    cm = principal(db, world, Role.CARE_MANAGER)
    gen, _ = orchestrator.submit(db, cm, "case_brief", world.subjects["patient"])
    monkeypatch.setattr(orchestrator, "build_context", incomplete)
    jobs.run_pending(world.session_factory)
    db.expire_all()
    gen = db.get(AiGeneration, gen.id)
    assert (gen.status, gen.failure) == ("fallback", "context_incomplete")
    assert provider.requests == []
    view = orchestrator.current(db, cm, "case_brief", world.subjects["patient"])
    assert view["source"] == "template"
    assert json.dumps(view["context"])  # the reader can see why
    db.close()


# --- Precision: the three replayed Case Brief outputs (2026-10-17) and their unsafe twins ------


def _with_items(ctx, items):
    """A copy of a real context whose model view is exactly these (id, kind, text) items."""
    first = ctx.model_items[0]
    built = [first.model_copy(update={"id": i, "kind": k, "text": t}) for i, k, t in items]
    return ctx.model_copy(update={"model_items": built, "vocabulary": ["metformin", "losartan"]})


OVERDUE = [
    (
        "ctx:rx:1",
        "rx",
        "Therapy: metformin, 90-day supply. Last refill 2026-07-08. Next refill "
        "was due 2026-10-06; not yet filled.",
    ),
    (
        "ctx:rx:2",
        "rx",
        "Therapy: losartan, 90-day supply. Last refill 2026-10-03. Next refill due "
        "2027-01-01 (not yet due).",
    ),
]


def test_replay_5_an_absence_the_cited_item_states_is_supported(db, world):
    ctx = _with_items(_ctx(db, world), OVERDUE)
    text = "Metformin refill is overdue since 2026-10-06 with no fill recorded."
    assert not {k for k in _kinds(ctx, text, ["ctx:rx:1"]) if "absence" in k}


@pytest.mark.parametrize(
    "text, cites",
    [
        # The same claim without citing the item that states it.
        ("Metformin refill is overdue since 2026-10-06 with no fill recorded.", ["ctx:rx:2"]),
        # A negation about another medicine does not support a claim about this one.
        ("Losartan has no fill recorded.", ["ctx:rx:1", "ctx:rx:2"]),
        # A present fact called missing, with no item stating its absence.
        ("No refill record is on file.", ["ctx:rx:1", "ctx:rx:2"]),
    ],
)
def test_replay_5_twins_without_evidence_are_still_rejected(db, world, text, cites):
    ctx = _with_items(_ctx(db, world), OVERDUE)
    assert "unsupported_absence:included" in _kinds(ctx, text, cites)


def test_replay_8_the_absence_phrase_targets_its_own_noun(db, world):
    ctx = _patient_without(db, world, "nba")
    text = (
        "Review the refill status and verify if any open recommendations exist despite current "
        "records indicating none are recorded."
    )
    assert not {k for k in _kinds(ctx, text) if "absence" in k}
    # Pointed at a category that is present, the same construction is rejected.
    present = "Review the open recommendation and the refill status, though none is recorded."
    assert "unsupported_absence:included" in _kinds(ctx, present)


def test_replay_27_a_category_word_elsewhere_in_the_sentence_is_not_the_target(db, world):
    ctx = _patient_without(db, world, "nba")
    text = (
        "Review the patient's medication list to identify any open recommendations, noting that "
        "no such recommendation is currently recorded for this patient."
    )
    assert not {k for k in _kinds(ctx, text) if "absence" in k}
    assert "unsupported_absence:not_requested" in _kinds(
        ctx, "No medication record is on file for this patient."
    )
