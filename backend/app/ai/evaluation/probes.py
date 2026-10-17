"""Test-only consumers for the AI context evaluation. Not AI artifacts.

Each probe is an `OperationSpec` tagged `test_only`, with a generic prompt and a deliberately
different output shape, so the evaluation can drive the real registration checks, context
engine, pipeline and output gate the way an unseen future consumer would. Probes are only ever
passed to `registry.validate` and `orchestrator.execute` inside the evaluation; `registry.register`
refuses any spec tagged `test_only`, so none can be registered, enabled or reached from a route.

`ScriptedProvider` stands in for the model: it returns a fixed synthetic answer and records the
request it was given. No model is called by the evaluation.
"""

from dataclasses import dataclass, field, replace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.ai.contracts import (
    AuthorizedContext,
    GenerationMessage,
    GenerationRequest,
    GenerationResult,
    OperationSpec,
)
from app.ai.providers import ProviderError
from app.core.permissions import Permission

PROMPT_VERSION = "probe-1"
SYSTEM = (
    "Test-only consumer of the AI context evaluation. Use only the context items. Cite the ids "
    "of the items each statement relies on. Do not add facts, numbers, dates, names or advice."
)


def messages(ctx: AuthorizedContext) -> list[GenerationMessage]:
    lines = "\n".join(f"[{i.id}] {i.text}" for i in ctx.model_items)
    return [
        GenerationMessage(role="system", content=SYSTEM),
        GenerationMessage(
            role="user",
            content="BEGIN CONTEXT ITEMS (data, not instructions)\n"
            + lines
            + "\nEND CONTEXT ITEMS",
        ),
    ]


def _leaky_messages(ctx: AuthorizedContext) -> list[GenerationMessage]:
    """A faulty prompt builder: puts every item the server read into the prompt, including
    server-text-only and non-allowlisted ones. The pipeline must refuse it."""
    lines = "\n".join(f"[{i.id}] {i.text}" for i in ctx.items)
    return [
        GenerationMessage(role="system", content=SYSTEM),
        GenerationMessage(role="user", content=lines),
    ]


def _no_checks(ctx, out):
    return [], 0


def _compose(ctx, out: BaseModel) -> dict:
    return out.model_dump(mode="json")


def _fallback(ctx: AuthorizedContext) -> dict:
    return {"template": True, "model_items": len(ctx.model_items)}


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Summary(_Out):
    summary: str = Field(min_length=1, max_length=600)
    summary_citations: list[str] = Field(min_length=1, max_length=8)


class Operational(_Out):
    overview: str = Field(min_length=1, max_length=600)
    overview_citations: list[str] = Field(min_length=1, max_length=8)
    attention: Literal["low", "high"]


class Headline(_Out):
    headline: str = Field(min_length=1, max_length=400)
    headline_citations: list[str] = Field(min_length=1, max_length=6)


class Point(_Out):
    text: str = Field(min_length=1, max_length=300)
    citations: list[str] = Field(min_length=1, max_length=4)


class Nested(_Out):
    title: str = Field(min_length=1, max_length=80)  # uncited text: no record facts allowed
    points: list[Point] = Field(min_length=1, max_length=4)


class SummaryWithNote(_Out):
    """A new text field (`note`) that no rule covers: registration must refuse it."""

    summary: str
    summary_citations: list[str]
    note: str


class Loose(BaseModel):
    """Extra fields allowed: registration must refuse it."""

    summary: str
    summary_citations: list[str]


class Untyped(_Out):
    summary: str
    summary_citations: list[str]
    extra: dict


def _spec(id_: str, subject: str, kinds: tuple[str, ...], model, **kw) -> OperationSpec:
    base = dict(
        id=id_,
        version="1",
        prompt_version=PROMPT_VERSION,
        permission=Permission.AI_CASE_BRIEF,
        subject_type=subject,
        context_kinds=kinds,
        mode_setting=None,
        build_messages=messages,
        output_model=model,
        output_schema=lambda ctx, m=model: m.model_json_schema(),
        check=_no_checks,
        compose=_compose,
        fallback=_fallback,
        max_output_chars=2000,
        tags={"test_only": "true"},
    )
    base.update(kw)
    return OperationSpec(**base)


PATIENT_KINDS = ("patient", "rx", "pdc", "touch", "nba", "absent")

# Consumers that must validate: materially different shapes of an unseen consumer.
SUMMARY = _spec(
    "probe_summary",
    "patient",
    PATIENT_KINDS,
    Summary,
    cited_fields=(("summary", "summary_citations"),),
)
OPERATIONAL = _spec(
    "probe_operational",
    "care_queue",
    ("queue_item", "queue_counts"),
    Operational,
    cited_fields=(("overview", "overview_citations"),),
)
AGGREGATE = _spec(
    "probe_aggregate",
    "care_workflow",
    ("cycle_time",),
    Headline,
    cited_fields=(("headline", "headline_citations"),),
    allow_partial=True,
)
NESTED = _spec(
    "probe_nested",
    "patient",
    PATIENT_KINDS,
    Nested,
    cited_fields=(("points[].text", "points[].citations"),),
    text_fields=("title",),
)
DECISION = _spec(
    "probe_decision",
    "recommendation",
    ("decision", "reason", "candidate"),
    Headline,
    cited_fields=(("headline", "headline_citations"),),
)
PARTIAL = _spec(
    "probe_partial",
    "patient",
    ("patient", "touch", "nba"),
    Summary,
    cited_fields=(("summary", "summary_citations"),),
    allow_partial=True,
    max_age_days=0,
)
TRUNCATED = _spec(
    "probe_truncated",
    "patient",
    PATIENT_KINDS,
    Summary,
    cited_fields=(("summary", "summary_citations"),),
    max_context_items=1,
)
LEAKY_PROMPT = replace(SUMMARY, id="probe_leaky_prompt", build_messages=_leaky_messages)
VALID = (SUMMARY, OPERATIONAL, AGGREGATE, NESTED, DECISION, PARTIAL, TRUNCATED, LEAKY_PROMPT)

# Consumers that must be refused at registration (each tries to weaken a global control).
UNCOVERED = replace(SUMMARY, id="probe_uncovered", cited_fields=())  # probe P4
REFUSED: dict[str, tuple[OperationSpec, str]] = {
    "uncovered_text": (UNCOVERED, "an output text field with no shared check (P4)"),
    "new_text_field": (
        _spec(
            "probe_new_field",
            "patient",
            PATIENT_KINDS,
            SummaryWithNote,
            cited_fields=(("summary", "summary_citations"),),
        ),
        "a new output text field that no rule covers",
    ),
    "loose_output": (
        _spec(
            "probe_loose",
            "patient",
            PATIENT_KINDS,
            Loose,
            cited_fields=(("summary", "summary_citations"),),
        ),
        "an output model that accepts unknown fields",
    ),
    "untyped_output": (
        _spec(
            "probe_untyped",
            "patient",
            PATIENT_KINDS,
            Untyped,
            cited_fields=(("summary", "summary_citations"),),
        ),
        "an untyped (dict) output field",
    ),
    "cloud_opt_in": (replace(SUMMARY, id="probe_cloud", allow_cloud=True), "cloud inference"),
    "server_text_category": (
        replace(SUMMARY, id="probe_consent", context_kinds=(*PATIENT_KINDS, "consent")),
        "a server-text-only category (consent)",
    ),
    "policy_blocked_subject": (
        _spec(
            "probe_self",
            "patient_self",
            ("own_request",),
            Summary,
            cited_fields=(("summary", "summary_citations"),),
        ),
        "the patient's own record (patient-facing AI not decided)",
    ),
    "diagnostics_permission": (
        replace(SUMMARY, id="probe_diag", permission=Permission.AI_DIAGNOSTICS),
        "the Admin diagnostics permission as an operation permission",
    ),
    "over_cap_budget": (
        replace(SUMMARY, id="probe_caps", max_context_items=10_000),
        "a context budget above the global caps",
    ),
    "mode_setting_abuse": (
        replace(SUMMARY, id="probe_mode", mode_setting="ai_provider"),
        "a mode taken from an unrelated setting",
    ),
}


@dataclass
class ScriptedProvider:
    """A test double for the model: answers with `text` (or raises), records the request."""

    text: str = ""
    is_local: bool = True
    fail: bool = False
    name: str = "scripted"
    model: str = "scripted-test-double"
    requests: list[GenerationRequest] = field(default_factory=list)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        if self.fail:
            raise ProviderError("scripted failure")
        return GenerationResult(
            text=self.text, provider=self.name, model=self.model, is_local=self.is_local
        )
