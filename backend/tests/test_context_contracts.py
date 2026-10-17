"""Contracts, registration and architecture of the shared context layer.

Every subject type has exactly one registered source; malformed requests are refused; an
operation cannot register around the field policy or the global caps; a new source can be
added without touching any existing consumer; the shared engine carries no Case Brief
assumptions; sources read only through the typed read services."""

import ast
import dataclasses
from pathlib import Path

import pytest
from context_world import build, fresh_world, principal
from pydantic import ValidationError

from app.ai import registry, sources
from app.ai.contracts import (
    ID_SUBJECTS,
    SELF_SUBJECTS,
    CategoryState,
    ContextRequest,
    SubjectType,
)
from app.ai.engine import GLOBAL_MAX_ITEMS, Consumer, UnknownCategory, build_context
from app.ai.operations import case_brief
from app.ai.policy import FIELD_POLICY, GLOBAL_PROHIBITED, Disposition, PolicyError, validate_policy
from app.ai.sources import Category, ContextSource, SourceStatus, UnknownSource
from app.models.enums import Role
from app.services.errors import Forbidden

AI = Path(__file__).resolve().parents[1] / "app" / "ai"
SHARED = [
    AI / "engine.py",
    AI / "contracts.py",
    AI / "safety.py",
    AI / "retrieval.py",
    AI / "aggregate.py",
    AI / "readonly.py",
    AI / "context_policy.py",
    *sorted((AI / "sources").glob("*.py")),
    *sorted((AI / "policy").glob("*.py")),
]


@pytest.fixture(scope="module")
def world():
    return fresh_world()


@pytest.fixture
def db(world):
    session = world.session_factory()
    yield session
    session.rollback()
    session.close()


# --- Contracts ----------------------------------------------------------------------------------


@pytest.mark.parametrize("subject_type", list(SubjectType), ids=str)
def test_every_subject_type_has_exactly_one_registered_source(subject_type):
    source = sources.source_for(subject_type)
    assert subject_type in source.subject_types
    assert source.status in (SourceStatus.IMPLEMENTED, SourceStatus.POLICY_BLOCKED)
    for c in source.categories:
        assert c.record in FIELD_POLICY


@pytest.mark.parametrize(
    "kw",
    [
        {"subject_type": "patient"},  # a record subject needs an id
        {"subject_type": "patient_self", "subject_id": "PAT_00001"},  # self takes no id
        {"subject_type": "patient", "subject_id": "PAT 1; drop"},
        {"subject_type": "patient", "subject_id": "PAT_1,PAT_2"},
        {"subject_type": "database", "subject_id": "X"},
        {"subject_type": "patient", "subject_id": "P1", "categories": ("Rx Items",)},
        {"subject_type": "care_queue", "limit": 51},
        {"subject_type": "care_queue", "cursor": -1},
        {"subject_type": "patient", "subject_id": "P1", "question": "why?"},
        {"subject_type": "patient", "subject_id": "P1", "correlation_id": "not-hex"},
        {"subject_type": "patient", "subject_id": "P1", "operation": "Case Brief"},
    ],
)
def test_malformed_requests_are_refused(kw):
    with pytest.raises(ValidationError):
        ContextRequest(**({"operation": "test"} | kw))


def test_well_formed_requests_cover_single_self_collection_and_aggregate():
    for t in SubjectType:
        kw = {"subject_id": "X_1"} if t in ID_SUBJECTS else {}
        assert ContextRequest(operation="test", subject_type=t, **kw).scope
    assert SELF_SUBJECTS <= set(SubjectType) - ID_SUBJECTS


def test_unknown_categories_and_subjects_are_refused(db, world):
    cm = principal(db, world, Role.CARE_MANAGER)
    with pytest.raises(UnknownCategory):
        build(db, cm, "patient", world.subjects["patient"], categories=("salary",))
    with pytest.raises(UnknownSource):
        sources.get_source("no_such_source")


def test_a_consumer_cannot_get_more_than_the_policy_or_its_allowlist(db, world):
    cm = principal(db, world, Role.CARE_MANAGER)
    pid = world.subjects["patient"]
    # Consent is server text only: asking for it, even with it in the allowlist, includes nothing.
    consumer = Consumer(id="t", permission=None, categories=("consent", "rx"), model_calls=False)
    ctx = build(db, cm, "patient", pid, categories=("consent", "rx"), consumer=consumer)
    assert ctx.state_of("consent") is CategoryState.EXCLUDED_POLICY
    assert not [i for i in ctx.model_items if i.kind == "consent"]
    # A request beyond the allowlist is reported, never served.
    narrow = Consumer(id="t", permission=None, categories=("patient",), model_calls=False)
    ctx = build(db, cm, "patient", pid, categories=("patient", "touch"), consumer=narrow)
    assert ctx.state_of("touch") is CategoryState.EXCLUDED_ALLOWLIST
    assert {i.kind for i in ctx.model_items} == {"patient"}


def test_a_model_consumer_must_name_a_permission_and_may_not_read_blocked_sources(db, world):
    pat = principal(db, world, Role.PATIENT)
    req = ContextRequest(operation="t", subject_type="patient_self")
    with pytest.raises(Forbidden):
        build_context(db, pat, req, Consumer(id="t", permission=None, categories=("own_request",)))
    with pytest.raises(Forbidden):  # policy blocked: patient-facing AI is not decided
        build_context(
            db,
            pat,
            req,
            Consumer(id="t", permission="self:health:manage", categories=("own_request",)),
        )


# --- Registration ---------------------------------------------------------------------------------


def _spec(**kw):
    return dataclasses.replace(case_brief.SPEC, id="test_only_op", **kw)


@pytest.mark.parametrize(
    "kw, message",
    [
        ({"allow_cloud": True}, "cloud"),
        ({"context_kinds": ()}, "allowlist"),
        ({"context_kinds": ("patient", "salary")}, "no category"),
        ({"context_kinds": ("patient", "consent")}, "server text only"),
        ({"subject_type": "patient_self", "context_kinds": ("own_request",)}, "policy_blocked"),
        ({"max_context_items": GLOBAL_MAX_ITEMS + 1}, "global caps"),
        ({"max_output_tokens": 9000}, "global caps"),
        ({"subject_type": "nothing"}, "no context source"),
    ],
)
def test_registration_refuses_operations_that_would_bypass_policy(kw, message):
    with pytest.raises(ValueError, match=message):
        registry.register(_spec(**kw))
    assert "test_only_op" not in registry.registered()


def test_unregistered_operations_cannot_run():
    with pytest.raises(registry.UnknownOperation):
        registry.resolve("queue_brief")
    assert registry.registered() == ("case_brief",)


def test_a_new_source_plugs_in_without_touching_existing_consumers(db, world):
    """Replace the cycle-time source with a test source for one test: the Case Brief context
    is unchanged and the new source is served through the same engine."""
    cm = principal(db, world, Role.CARE_MANAGER)
    pid = world.subjects["patient"]
    before = build(db, cm, "patient", pid, consumer=None).fingerprint
    original = sources.get_source("care_workflow")

    def read(db_, user, request, b):
        b.add("probe", "Probe fact.", "test.probe", "1")
        b.mark("probe", 1)

    test_source = ContextSource(
        id="test_probe",
        domain="test",
        subject_types=(SubjectType.CARE_WORKFLOW,),
        categories=(Category("probe", "care_workflow_metric", "probe"),),
        read=read,
        read_services=(),
    )
    sources.unregister_source("care_workflow")
    try:
        sources.register_source(test_source)
        ctx = build(db, cm, "care_workflow")
        assert ctx.source == "test_probe" and [i.text for i in ctx.model_items] == ["Probe fact."]
        assert build(db, cm, "patient", pid).fingerprint == before
    finally:
        sources.unregister_source("test_probe")
        sources.register_source(original)
    assert sources.source_for(SubjectType.CARE_WORKFLOW) is original


def test_a_source_cannot_claim_a_subject_type_already_served():
    clash = dataclasses.replace(sources.get_source("hcp_360"), id="hcp_clone")
    with pytest.raises(ValueError, match="already served"):
        sources.register_source(clash)


# --- Field policy -------------------------------------------------------------------------------


def test_the_policy_is_consistent_and_prohibited_fields_are_never_model_visible():
    validate_policy()
    for record, fields in FIELD_POLICY.items():
        for name, rule in fields.items():
            if name in GLOBAL_PROHIBITED and not str(rule).startswith("@"):
                assert Disposition(rule) not in (
                    Disposition.MODEL,
                    Disposition.MODEL_IF_VOCABULARY,
                ), (record, name)


@pytest.mark.parametrize("field", ["note", "reason", "name", "email", "body", "patient_id"])
def test_marking_a_prohibited_field_model_visible_is_refused(field):
    broken = {"test_record": {field: Disposition.MODEL}}
    with pytest.raises(PolicyError, match="globally prohibited"):
        validate_policy(broken)


def test_an_unclassified_field_is_refused():
    from app.ai.context_policy import UntrustedContentError
    from app.ai.policy import project

    with pytest.raises(UntrustedContentError, match="not classified"):
        project("content", {"content_id": "C1", "new_field": "x"})


# --- Architecture ---------------------------------------------------------------------------------


def _code_strings(path: Path) -> set[str]:
    """Identifiers and string constants in code, docstrings and comments excluded."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.ClassDef | ast.Module | ast.AsyncFunctionDef)
        and n.body
        and isinstance(n.body[0], ast.Expr)
        and isinstance(n.body[0].value, ast.Constant)
    }
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings:
            out.add(n.value)
        elif isinstance(n, ast.ImportFrom | ast.Import):
            out |= {a.name for a in n.names} | {getattr(n, "module", "") or ""}
    return out


@pytest.mark.parametrize("path", SHARED, ids=lambda p: p.name)
def test_the_shared_engine_has_no_case_brief_dependency(path):
    code = " ".join(_code_strings(path)).lower()
    for marker in ("case_brief", "casebrief", "app.ai.operations", "modelnarrative"):
        assert marker not in code, f"{path.name} refers to {marker}"


@pytest.mark.parametrize("path", sorted((AI / "sources").glob("*.py")), ids=lambda p: p.name)
def test_sources_read_only_through_typed_services(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith(
                ("app.api", "app.clinical", "app.content", "app.nba", "app.commercial")
            ), node.module
            if node.module == "app.models":
                assert {a.name for a in node.names} <= {"User"}, "sources never query ORM models"
            assert node.module != "sqlalchemy" or {a.name for a in node.names} <= set(), node.module
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in (
            "execute",
            "scalars",
            "scalar",
            "query",
        ):
            raise AssertionError(f"{path.name} queries the database directly")


# --- Owner decisions 2026-10-17: blocked audiences and pending data stay out of model views ----


@pytest.mark.parametrize(
    "subject_type, kinds, message",
    [
        ("patient_self", ("own_request",), "policy_blocked"),  # patient-facing AI not decided
        ("consultation_queue", ("consultation",), "policy_blocked"),  # HCP-facing AI not decided
        ("quality_measures", ("measure",), "server text only"),  # group adherence pending review
    ],
)
def test_no_consumer_can_register_for_blocked_audiences_or_pending_data(
    subject_type, kinds, message
):
    with pytest.raises(ValueError, match=message):
        registry.register(_spec(subject_type=subject_type, context_kinds=kinds))


def test_supporting_a_subject_type_is_not_authorising_ai_for_its_audience(db, world):
    """The HCP's consultations and the patient's own record can be built for deterministic use,
    by their owners only, but a model consumer is refused even with the right permission."""
    hcp = principal(db, world, Role.HCP)
    ctx = build(db, hcp, "consultation_queue")
    assert ctx.items
    req = ContextRequest(operation="t", subject_type="consultation_queue")
    with pytest.raises(Forbidden):
        build_context(
            db,
            hcp,
            req,
            Consumer(id="t", permission="self:consultations:manage", categories=("consultation",)),
        )


def test_pending_review_values_never_enter_a_model_view(db, world):
    from app.ai.diagnostics import _fixture_subject
    from app.ai.policy import FIELD_POLICY, Disposition

    pending = {
        (record, name)
        for record, fields in FIELD_POLICY.items()
        for name, rule in fields.items()
        if rule == Disposition.PENDING_REVIEW
    }
    assert ("quality_measure", "adherent_share") in pending
    assert ("content", "body") in pending and ("hcp_360", "value_score") in pending
    cm = principal(db, world, Role.CARE_MANAGER)
    measures = build(db, cm, "quality_measures")
    assert measures.model_items == []  # server text only
    assert measures.state_of("measure") is CategoryState.EXCLUDED_POLICY
    content = build(db, cm, "content", _fixture_subject(world, SubjectType.CONTENT, "auto"))
    from app.models import Content

    body = db.get(Content, world.subjects["content"]).body
    assert all(body[:40] not in i.text for i in content.model_items)
