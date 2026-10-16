"""Contracts shared by every AI operation, whatever the provider.

Provider-neutral and operation-neutral: nothing here names a specific artifact. Every model is
strict (`extra="forbid"`), so an unknown field is an error rather than silently accepted.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

UNTRUSTED_NOTE_LABEL = "Untrusted; review for clinical content."


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Sensitivity(StrEnum):
    OPERATIONAL = "operational"  # dates, counts, statuses, content ids
    PERSONAL = "personal"  # names, contact details: never sent to a model by default
    FREE_TEXT = "free_text"  # notes: untrusted data, may carry injected instructions


# --- Context --------------------------------------------------------------------------------


class ContextItem(BaseModel):
    """One piece of evidence, with its provenance. Built by the context service only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^ctx:[a-z_]+:\d+$")  # evidence id a statement must cite
    kind: str  # patient, rx, pdc, consent, touch, nba, absent, ...
    text: str  # the fact as written by the server
    source: str  # provenance: the read service and record type the fact came from
    source_id: str  # record id in that source
    recorded_at: datetime | date | None = None  # when the underlying fact was recorded
    sensitivity: Sensitivity = Sensitivity.OPERATIONAL


class ReaderNote(BaseModel):
    """Free text for an authorised person, verbatim, with the one fixed label. Never a model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: Literal["Untrusted; review for clinical content."] = UNTRUSTED_NOTE_LABEL
    text: str
    source: str
    source_id: str
    recorded_at: datetime | date | None = None


class ContextRequest(_Strict):
    """What an operation asks the context service for. The acting user is passed separately,
    from the server-side session, never from this request."""

    operation: str = Field(min_length=1, max_length=48)
    subject_type: Literal["patient"]
    subject_id: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")


class AuthorizedContext(BaseModel):
    """The context the server assembled for one user, one subject and one operation.

    `items` are every structured fact the server may cite in deterministic text; `model_items`
    is the allowlisted subset a model may read. `reader_notes` are for the person only.
    `withheld_texts` and `identifiers` are what must never appear in a model request or answer;
    they are excluded from serialisation so they cannot leak through a dump or a log."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request: ContextRequest
    as_of: date
    items: list[ContextItem]
    model_items: list[ContextItem]
    reader_notes: list[ReaderNote]
    facts: dict[str, Any] = Field(exclude=True)  # typed per subject by the context service
    withheld_texts: list[str] = Field(default_factory=list, exclude=True, repr=False)
    identifiers: list[str] = Field(default_factory=list, exclude=True, repr=False)
    vocabulary: list[str] = Field(default_factory=list, exclude=True, repr=False)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    def provenance(self) -> list[dict]:
        """Minimised source references: ids and record pointers, no text."""
        return [
            {"id": i.id, "source": i.source, "source_id": i.source_id} for i in self.model_items
        ]


# --- Generation -----------------------------------------------------------------------------


class GenerationMessage(_Strict):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str


class GenerationRequest(_Strict):
    """What an operation asks of a provider. Identical for every provider."""

    artifact: str  # the registered operation id
    prompt_version: str
    messages: list[GenerationMessage] = Field(min_length=1)
    json_schema: dict  # output contract the provider must enforce or the gate will reject
    contains_identifiable_data: bool = False
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    seed: int | None = 7
    max_output_tokens: int = Field(default=1200, gt=0, le=8000)


class GenerationResult(_Strict):
    """Raw provider output. Untrusted until the operation's safety gate has checked it."""

    text: str
    provider: str
    model: str
    is_local: bool
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None


# --- Validation, gate and lifecycle ---------------------------------------------------------


class GateFinding(_Strict):
    """One failed check. `kind` is a fixed code; `where` names the output field. No text from
    the model is kept here, so findings are safe to audit."""

    kind: str = Field(pattern=r"^[a-z_]+(?::[a-z_]+)?$")
    where: str = Field(max_length=40)


class GateOutcome(_Strict):
    passed: bool
    reasons: list[str]  # sorted, distinct finding kinds (plus "miscited", "schema")
    findings: list[GateFinding] = Field(default_factory=list)
    miscited: int = 0


class GenerationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"  # model text passed every check
    FALLBACK = "fallback"  # the deterministic template stands; `failure` says why
    FAILED = "failed"  # nothing was produced (internal error, interrupted, access lost)


ACTIVE_STATUSES = (GenerationStatus.QUEUED, GenerationStatus.RUNNING)


class FailureCategory(StrEnum):
    PROVIDER_DISABLED = "provider_disabled"  # AI switched off or provider not configured
    PROVIDER_UNAVAILABLE = "provider_unavailable"  # runtime error or timeout
    DATA_POLICY = "data_policy"  # the request may not go to this provider
    SCHEMA_INVALID = "schema_invalid"  # the answer was not the required JSON
    GATE_REJECTED = "gate_rejected"  # a safety check failed
    ACCESS_LOST = "access_lost"  # the requester may no longer read the subject
    INTERRUPTED = "interrupted"  # the worker stopped before the job finished
    INTERNAL = "internal"


class Mode(StrEnum):
    TEMPLATE = "template"  # no model call; the deterministic template is the output
    SHADOW = "shadow"  # the model runs and is checked and audited; readers see the template
    PROTOTYPE = "prototype"  # readers see model wording when every check passes


class AuditEvent(_Strict):
    """What the audit trail keeps about one generation step: identifiers, versions, outcomes
    and timings. Never prompts, notes, model text or patient narrative."""

    operation: str
    operation_version: str
    prompt_version: str
    mode: Mode
    subject_type: str
    subject_id: str
    generation_id: int | None = None
    job_id: int | None = None
    status: str
    provider: str | None = None
    model: str | None = None
    model_digest: str | None = None
    context_fingerprint: str | None = None
    context_item_ids: list[str] = Field(default_factory=list)
    gate_reasons: list[str] = Field(default_factory=list)
    failure: FailureCategory | None = None
    duration_ms: int | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None


# --- Operation specification -----------------------------------------------------------------


@dataclass(frozen=True)
class OperationSpec:
    """Everything one AI operation supplies. The shared layer does the rest: authorisation,
    context, data policy, provider call, schema validation, audit, jobs and fallback.

    - `context_kinds`: the only item kinds the model may read (the context allowlist).
    - `build_messages(ctx)`: the prompt, from `ctx.model_items` only.
    - `output_model`: strict Pydantic model of what the model may write.
    - `output_schema(ctx)`: the JSON schema handed to the provider for constrained decoding.
    - `check(ctx, output)`: operation-specific deterministic checks, returning findings and the
      number of miscited statements. Shared checks run as well; they cannot be skipped.
    - `compose(ctx, output)`: the reader-facing result built from a checked output.
    - `fallback(ctx)`: the deterministic template, always available.
    """

    id: str
    version: str
    prompt_version: str
    permission: str
    subject_type: Literal["patient"]
    context_kinds: tuple[str, ...]
    mode_setting: str
    build_messages: Callable[[AuthorizedContext], list[GenerationMessage]]
    output_model: type[BaseModel]
    output_schema: Callable[[AuthorizedContext], dict]
    check: Callable[[AuthorizedContext, BaseModel], tuple[list[GateFinding], int]]
    compose: Callable[[AuthorizedContext, BaseModel], dict]
    fallback: Callable[[AuthorizedContext], dict]
    # Text fields of the output and the citation field each one relies on.
    cited_fields: tuple[tuple[str, str], ...] = ()
    temperature: float = 0.0
    seed: int | None = 7
    max_output_tokens: int = 1200
    max_output_chars: int = 4000
    allow_cloud: bool = False
    tags: dict[str, str] = field(default_factory=dict)
