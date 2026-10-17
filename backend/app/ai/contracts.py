"""Contracts shared by every AI operation, whatever the provider.

Provider-neutral and operation-neutral: nothing here names a specific artifact. Every model is
strict (`extra="forbid"`), so an unknown field is an error rather than silently accepted.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

UNTRUSTED_NOTE_LABEL = "Untrusted; review for clinical content."


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Sensitivity(StrEnum):
    OPERATIONAL = "operational"  # dates, counts, statuses, content ids
    PERSONAL = "personal"  # names, contact details: never sent to a model by default
    FREE_TEXT = "free_text"  # notes: untrusted data, may carry injected instructions


# --- Context --------------------------------------------------------------------------------


class SubjectType(StrEnum):
    """What a context request is about. Each value is backed by an authoritative read service
    (see `app.ai.sources`); a subject type grants nothing by itself."""

    # One record
    PATIENT = "patient"  # Patient 360, staff reader
    HCP = "hcp"  # HCP 360, staff reader
    RECOMMENDATION = "recommendation"  # one recommendation and its decision trace
    CONTENT = "content"  # one content version
    CARE_REQUEST = "care_request"  # one care request, staff reader
    PATIENT_SELF = "patient_self"  # the signed-in patient's own record (no id accepted)
    # Collections of records in the reader's scope
    CARE_QUEUE = "care_queue"  # care requests of the reader's panel
    CONSULTATION_QUEUE = "consultation_queue"  # consultations routed to the signed-in HCP
    # Aggregates: figures computed in code, no record-level detail
    CARE_WORKFLOW = "care_workflow"  # cycle times of care requests in scope
    CONTENT_LINEAGE = "content_lineage"  # versions and scoped references of one content item
    AI_TELEMETRY = "ai_telemetry"  # AI generation outcomes
    AUDIT_ACTIVITY = "audit_activity"  # audit events per action and day
    JOB_QUEUE = "job_queue"  # background job states
    QUALITY_MEASURES = "quality_measures"  # STAR-style measures for the reader's population
    ACCESS_SCOPE = "access_scope"  # one account's permissions and assignment counts


class ScopeKind(StrEnum):
    SINGLE = "single"
    COLLECTION = "collection"
    AGGREGATE = "aggregate"


SUBJECT_SCOPE: dict[SubjectType, ScopeKind] = {
    SubjectType.PATIENT: ScopeKind.SINGLE,
    SubjectType.HCP: ScopeKind.SINGLE,
    SubjectType.RECOMMENDATION: ScopeKind.SINGLE,
    SubjectType.CONTENT: ScopeKind.SINGLE,
    SubjectType.CARE_REQUEST: ScopeKind.SINGLE,
    SubjectType.PATIENT_SELF: ScopeKind.SINGLE,
    SubjectType.CARE_QUEUE: ScopeKind.COLLECTION,
    SubjectType.CONSULTATION_QUEUE: ScopeKind.COLLECTION,
    SubjectType.CARE_WORKFLOW: ScopeKind.AGGREGATE,
    SubjectType.CONTENT_LINEAGE: ScopeKind.AGGREGATE,
    SubjectType.AI_TELEMETRY: ScopeKind.AGGREGATE,
    SubjectType.AUDIT_ACTIVITY: ScopeKind.AGGREGATE,
    SubjectType.JOB_QUEUE: ScopeKind.AGGREGATE,
    SubjectType.QUALITY_MEASURES: ScopeKind.AGGREGATE,
    SubjectType.ACCESS_SCOPE: ScopeKind.SINGLE,
}
# Subjects whose id comes from the session, never from a request.
SELF_SUBJECTS = frozenset({SubjectType.PATIENT_SELF, SubjectType.CONSULTATION_QUEUE})
# Subjects that take an explicit record id.
ID_SUBJECTS = frozenset(
    {
        SubjectType.PATIENT,
        SubjectType.HCP,
        SubjectType.RECOMMENDATION,
        SubjectType.CONTENT,
        SubjectType.CARE_REQUEST,
        SubjectType.CONTENT_LINEAGE,
        SubjectType.ACCESS_SCOPE,
    }
)


class CategoryState(StrEnum):
    """Why a category is or is not in a context. "Not in this context" is never the same as
    "not recorded": only `verified_empty` means the source was read and holds nothing."""

    INCLUDED = "included"  # read, policy-eligible, present
    VERIFIED_EMPTY = "verified_empty"  # read; the source holds no matching record
    NOT_REQUESTED = "not_requested"  # the consumer did not ask for it; not read
    EXCLUDED_POLICY = "excluded_policy"  # the field policy keeps it from a model
    EXCLUDED_ALLOWLIST = "excluded_allowlist"  # policy-eligible, outside the consumer allowlist
    UNAVAILABLE = "unavailable"  # the read failed or the source is not available here
    TRUNCATED = "truncated"  # read; more records exist than the context holds
    STALE = "stale"  # read; the source's derived figures are older than allowed


# States in which a statement "X is not recorded" is supported by the record.
ABSENCE_SUPPORTED = frozenset({CategoryState.VERIFIED_EMPTY})


class CategoryStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: str = Field(pattern=r"^[a-z_]+$")
    state: CategoryState
    count: int = 0  # items of this category in the context
    total: int | None = None  # records the source holds, when known
    reason: str | None = Field(default=None, pattern=r"^[a-z_:]+$")  # fixed code, never text


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
    # Optional provenance (new sources fill these; the Case Brief items keep their old shape).
    field: str | None = None  # record type and field path the fact was written from
    category: str | None = None  # the category the item belongs to (defaults to `kind`)
    source_version: str | None = None  # version of the source record (content version, ...)
    subject_ref: str | None = None  # opaque hash of the subject, never a raw id
    as_of: date | None = None  # the date the source's figures are valid for


class ReaderNote(BaseModel):
    """Free text for an authorised person, verbatim, with the one fixed label. Never a model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: Literal["Untrusted; review for clinical content."] = UNTRUSTED_NOTE_LABEL
    text: str
    source: str
    source_id: str
    recorded_at: datetime | date | None = None


MAX_PAGE = 50  # the most records a collection context may hold in one page


class ContextRequest(_Strict):
    """What a consumer asks the context service for. The acting user is passed separately,
    from the server-side session, never from this request; nothing here can widen access.

    - `subject_id` is required for record subjects, refused for self subjects (the record comes
      from the session) and optional for collections and aggregates.
    - `categories`: the categories asked for; empty means the consumer's allowlist. A category
      the field policy keeps from a model is reported `excluded_policy`, never included.
    - `limit` / `cursor`: page size (at most `MAX_PAGE`) and the position to continue from.
    - `max_age_days`: derived figures older than this are reported `stale`.
    - `require_complete`: a context with any truncated, unavailable or stale category is
      refused for a model call (the orchestrator falls back) unless this is False.
    """

    operation: str = Field(min_length=1, max_length=48, pattern=r"^[a-z_]+$")
    subject_type: SubjectType
    subject_id: str | None = Field(
        default=None, min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$"
    )
    categories: tuple[str, ...] = Field(default=(), max_length=24)
    limit: int = Field(default=20, ge=1, le=MAX_PAGE)
    cursor: int | None = Field(default=None, ge=0)
    max_age_days: int | None = Field(default=None, ge=0, le=3650)
    require_complete: bool = True
    correlation_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")

    @model_validator(mode="after")
    def _subject_shape(self) -> "ContextRequest":
        for c in self.categories:
            if not re.fullmatch(r"[a-z_]{1,32}", c):
                raise ValueError("categories are lower-case codes")
        if self.subject_type in ID_SUBJECTS and self.subject_id is None:
            raise ValueError(f"{self.subject_type} needs a subject_id")
        if self.subject_type in SELF_SUBJECTS and self.subject_id is not None:
            raise ValueError("a self subject takes no id; the record comes from the session")
        return self

    @property
    def scope(self) -> ScopeKind:
        return SUBJECT_SCOPE[self.subject_type]


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
    # Why each category is or is not present (see `CategoryState`).
    categories: list[CategoryStatus] = Field(default_factory=list)
    complete: bool = True  # no category truncated, unavailable or stale
    policy_version: str = ""
    source: str = ""  # the registered context source that built this context
    # Hash of the principal's scope for views that depend on who reads them; part of the
    # fingerprint for those sources so a stored result is never reused across scopes.
    scope_key: str = Field(default="", exclude=True, repr=False)

    def state_of(self, category: str) -> CategoryState | None:
        for c in self.categories:
            if c.category == category:
                return c.state
        return None

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
    CONTEXT_INCOMPLETE = "context_incomplete"  # truncated, stale or partly unavailable context
    CONTEXT_UNAVAILABLE = "context_unavailable"  # the context source could not be read
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
    # Opaque, salted hash of the subject (telemetry minimisation). The raw id appears only as
    # the audit row's entity id, under the audit log's own masking rules.
    subject_ref: str
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
    subject_type: SubjectType
    context_kinds: tuple[str, ...]
    # Legacy: the name of a Settings attribute holding this operation's mode. New operations
    # leave it empty and are configured through `Settings.ai_operation_modes`.
    mode_setting: str | None
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
    # A context with truncated, unavailable or stale categories goes to a model only when the
    # operation says it can describe partial data honestly. Default: fall back.
    allow_partial: bool = False
    # Context budget for this operation; never above the global caps (checked at registration).
    max_context_items: int = 40
    max_context_chars: int = 6000
    tags: dict[str, str] = field(default_factory=dict)
