"""The single generation path every AI operation goes through.

    submit  (web request)  resolve -> authorise + build context -> mode, rate and capacity
                           checks -> record a queued generation -> queue a job
    run     (worker job)   re-authorise + rebuild context -> provider selection (local only)
                           -> data-policy checks -> provider call -> schema validation ->
                           citation, grounding and safety checks -> record and audit
    current (web request)  authorise + rebuild context -> the template, or the checked model
                           result when it may be shown -> reader notes, sources, status

Shared controls live here once. An operation supplies only its spec (`OperationSpec`). Model
output never changes a record, a consent, a permission or a workflow state: the only write
is the generation row and its audit events. A model cannot mark its own output safe; it can
only produce text that the server's checks accept or reject.
"""

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache

from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import audit, jobs
from app.ai import output_contract, safety
from app.ai.context_policy import UntrustedContentError, assert_no_free_text
from app.ai.contracts import (
    ACTIVE_STATUSES,
    ID_SUBJECTS,
    AuditEvent,
    AuthorizedContext,
    CategoryState,
    ContextRequest,
    FailureCategory,
    GateFinding,
    GateOutcome,
    GenerationRequest,
    GenerationStatus,
    Mode,
    OperationSpec,
    SubjectType,
)
from app.ai.engine import build_context, consumer_for
from app.ai.providers import (
    DataPolicyError,
    ModelProvider,
    ProviderError,
    ProviderNotConfigured,
    check_data_policy,
    get_provider,
)
from app.ai.registry import resolve
from app.ai.sources import SourceUnavailable
from app.core import pseudonym, ratelimit
from app.core.config import Settings, get_settings
from app.core.db import has_table
from app.models import AiGeneration, Job, User
from app.models.tables import utcnow
from app.services.errors import Forbidden, NotFound

log = logging.getLogger("nba.ai")

JOB_KIND = "ai_generation"
ProviderFactory = Callable[[Settings], ModelProvider]

SOURCE_LABELS = {
    "patient": "Patient profile",
    "rx": "Refill record",
    "pdc": "Days covered",
    "touch": "Outreach record",
    "nba": "Open recommendation",
    "consent": "Consent record",
    "absent": "Not recorded",
    "decision": "Recommendation",
    "reason": "Recorded reason",
    "candidate": "Option considered",
    "response": "Response",
    "hcp": "HCP profile",
    "contact_window": "Contact window",
    "hcp_touch": "Engagement record",
    "hcp_nba": "Open recommendation",
    "hcp_work": "Open task",
    "queue_item": "Queue item",
    "queue_counts": "Queue counts",
    "audit_event": "Audit activity",
    "cycle_time": "Cycle time",
}


class GenerationRefused(Exception):
    """The request is valid but cannot be accepted now. Audited before it is raised."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class AiUnavailable(GenerationRefused):
    """This database has no AI tables (the traditional database, or one not migrated). AI
    routes answer 503; nothing else in the application depends on these tables."""

    def __init__(self) -> None:
        super().__init__(
            503,
            "ai_unavailable",
            "AI features are not available on this database. Everything else works as usual.",
        )


def ai_tables_present(db: Session) -> bool:
    return has_table(db, AiGeneration.__tablename__)


def mode_for(spec: OperationSpec, settings: Settings | None = None) -> Mode:
    """The operation's configured mode, from one place:

    1. an operation listed in `ai_operations_disabled` is template, whatever else is set;
    2. its entry in `ai_operation_modes`;
    3. its legacy per-operation setting (`spec.mode_setting`, e.g. `ai_case_brief_mode`);
    4. otherwise template. Anything unrecognised means template (fail closed)."""
    settings = settings or get_settings()
    if spec.id in (settings.ai_operations_disabled or []):
        return Mode.TEMPLATE
    raw = (settings.ai_operation_modes or {}).get(spec.id)
    if raw is None and spec.mode_setting:
        raw = getattr(settings, spec.mode_setting, "")
    try:
        return Mode(str(raw or "").strip().lower())
    except ValueError:
        return Mode.TEMPLATE


def lock_key(spec: OperationSpec, subject_id: str) -> str:
    """One queued or running generation per operation and subject or scope (job.lock_key is
    unique). For a self, collection or aggregate subject `subject_id` is the reader's scope
    key (`subject_key`)."""
    digest = hashlib.sha256(f"{spec.id}|{subject_id}".encode()).hexdigest()[:24]
    return f"ai:{digest}"


def _is_record(spec: OperationSpec) -> bool:
    return SubjectType(spec.subject_type) in ID_SUBJECTS


def subject_key(spec: OperationSpec, user: User, subject_id: str | None) -> str:
    """What a generation row is filed under: the record id for a record subject; for a self,
    collection or aggregate subject an opaque key of (subject type, reader), so one reader's
    result is never handed to another reader."""
    if _is_record(spec):
        if not subject_id:
            raise NotFound("Not found")
        return subject_id
    return "s" + (pseudonym.ref("scope", f"{spec.subject_type}|{user.id}") or "")


def _request(spec: OperationSpec, subject_id: str | None) -> ContextRequest:
    return ContextRequest(
        operation=spec.id,
        subject_type=spec.subject_type,
        subject_id=subject_id if _is_record(spec) else None,
        max_age_days=spec.max_age_days,
    )


def _context(
    db: Session, user: User, spec: OperationSpec, subject_id: str | None
) -> AuthorizedContext:
    return build_context(db, user, _request(spec, subject_id), consumer_for(spec))


def subject_ref(spec: OperationSpec, subject_id: str) -> str:
    """The opaque subject reference kept in audit detail instead of the raw id. Without the
    deployment key the reference is "unavailable", never the raw id."""
    if not pseudonym.available():
        return "unavailable"
    return pseudonym.ref(f"subject:{spec.subject_type}", subject_id) or ""


def _require_key() -> None:
    if not pseudonym.available():
        log.error("AI refused: NBA_JWT_SECRET is not set, so subject references are unstable")
        raise GenerationRefused(
            503, "ai_misconfigured", "AI features are not configured on this server."
        )


def _event(spec: OperationSpec, mode: Mode, subject_id: str, status: str, **kw) -> AuditEvent:
    return AuditEvent(
        operation=spec.id,
        operation_version=spec.version,
        prompt_version=spec.prompt_version,
        mode=mode,
        subject_type=str(spec.subject_type),
        subject_ref=subject_ref(spec, subject_id),
        status=status,
        **kw,
    )


def _audit(
    db: Session,
    action: str,
    event: AuditEvent,
    user: User | None,
    *,
    entity_id: str | int,
    reason: str | None = None,
) -> None:
    audit.record(
        db,
        action,
        "ai_generation" if event.generation_id else event.subject_type,
        entity_id,
        actor=user.username if user else audit.log.SYSTEM_ACTOR,
        actor_role=user.role if user else audit.log.SYSTEM_ROLE,
        actor_user_id=user.id if user else None,
        reason=reason,
        detail=event.model_dump(mode="json", exclude_none=True),
    )


def _reject(
    db: Session, user: User, spec: OperationSpec, subject_id: str, reason: str, mode: Mode
) -> None:
    """Audits a refused request in its own commit, so the refusal is kept even though the
    caller then raises."""
    db.rollback()
    event = _event(spec, mode, subject_id, "rejected")
    _audit(db, "ai_generation_rejected", event, user, entity_id=subject_id, reason=reason)
    db.commit()


def _gen_event(gen: AiGeneration, spec: OperationSpec, **kw) -> AuditEvent:
    return _event(
        spec,
        Mode(gen.mode),
        gen.subject_id,
        gen.status,
        generation_id=gen.id,
        job_id=gen.job_id,
        provider=gen.provider,
        model=gen.model,
        model_digest=gen.model_digest,
        context_fingerprint=gen.context_fingerprint,
        context_item_ids=[p["id"] for p in gen.provenance or []],
        gate_reasons=gen.gate_reasons or [],
        failure=gen.failure,
        duration_ms=gen.duration_ms,
        prompt_tokens=gen.prompt_tokens,
        output_tokens=gen.output_tokens,
        **kw,
    )


def _finish(
    db: Session,
    gen: AiGeneration,
    spec: OperationSpec,
    status: GenerationStatus,
    failure: FailureCategory | None = None,
    user: User | None = None,
) -> str:
    gen.status = status
    gen.failure = failure.value if failure else None
    gen.finished_at = utcnow()
    if gen.started_at:
        gen.duration_ms = int((gen.finished_at - gen.started_at).total_seconds() * 1000)
    action = {
        GenerationStatus.COMPLETED: "ai_generation_completed",
        GenerationStatus.FALLBACK: "ai_generation_fallback",
        GenerationStatus.FAILED: "ai_generation_failed",
    }[status]
    _audit(db, action, _gen_event(gen, spec), user, entity_id=gen.id, reason=gen.failure)
    db.commit()
    return status


def _prune(db: Session, spec: OperationSpec, subject_id: str, keep: int) -> None:
    ids = db.scalars(
        select(AiGeneration.id)
        .where(
            AiGeneration.operation == spec.id,
            AiGeneration.subject_type == spec.subject_type,
            AiGeneration.subject_id == subject_id,
        )
        .order_by(AiGeneration.id.desc())
    ).all()
    old = ids[max(keep, 1) :]
    if old:
        db.execute(
            delete(AiGeneration).where(
                AiGeneration.id.in_(old), AiGeneration.status.not_in(ACTIVE_STATUSES)
            )
        )


def latest(db: Session, spec: OperationSpec, subject_id: str) -> AiGeneration | None:
    return db.scalar(
        select(AiGeneration)
        .where(
            AiGeneration.operation == spec.id,
            AiGeneration.subject_type == spec.subject_type,
            AiGeneration.subject_id == subject_id,
        )
        .order_by(AiGeneration.id.desc())
        .limit(1)
    )


# --- Submit (web request) ---------------------------------------------------------------------


def submit(
    db: Session,
    user: User,
    operation: str,
    subject_id: str | None,
    *,
    settings: Settings | None = None,
    provider_factory: ProviderFactory | None = None,
) -> tuple[AiGeneration, bool]:
    """Queues a generation. Returns (generation, created); `created` is False when one is
    already queued or running for this subject (duplicate submission). Raises the read
    services' `Forbidden` / `NotFound`, `GenerationRefused`, or the rate limiter's 429."""
    settings = settings or get_settings()
    provider_factory = provider_factory or get_provider
    spec = resolve(operation)
    mode = mode_for(spec, settings)
    _require_key()
    raw_subject = subject_id
    subject_id = subject_key(spec, user, subject_id)
    try:
        _context(db, user, spec, raw_subject)
    except (Forbidden, NotFound) as exc:
        _reject(db, user, spec, subject_id, type(exc).__name__.lower(), mode)
        raise
    except SourceUnavailable:
        _reject(db, user, spec, subject_id, "context_unavailable", mode)
        raise GenerationRefused(
            503, "context_unavailable", "This information is not available right now."
        ) from None
    # Access first (403 / 404 as everywhere), then whether this database has the AI tables.
    if not ai_tables_present(db):
        _reject(db, user, spec, subject_id, "ai_unavailable", mode)
        raise AiUnavailable()
    if mode is Mode.TEMPLATE:
        _reject(db, user, spec, subject_id, "mode_template", mode)
        raise GenerationRefused(
            409, "ai_off", "AI wording is switched off here; the template brief is shown."
        )
    current = latest(db, spec, subject_id)
    if current is not None and current.status in ACTIVE_STATUSES:
        reconcile(db, current, spec)
        if current.status in ACTIVE_STATUSES:
            return current, False
    try:
        ratelimit.consume(db, "ai_generate", str(user.id))
    except Exception:
        _reject(db, user, spec, subject_id, "rate_limited", mode)
        raise
    busy = db.scalar(select(func.count()).where(AiGeneration.status.in_(ACTIVE_STATUSES)))
    if busy >= settings.ai_max_active_jobs:
        _reject(db, user, spec, subject_id, "busy", mode)
        raise GenerationRefused(503, "ai_busy", "The local model is busy. Try again shortly.")

    gen = AiGeneration(
        operation=spec.id,
        operation_version=spec.version,
        prompt_version=spec.prompt_version,
        mode=mode.value,
        subject_type=spec.subject_type,
        subject_id=subject_id,
        requested_by_user_id=user.id,
        status=GenerationStatus.QUEUED,
        requested_at=utcnow(),
    )
    db.add(gen)
    db.flush()
    _audit(db, "ai_generation_requested", _gen_event(gen, spec), user, entity_id=gen.id)
    try:
        provider = provider_factory(settings)
        gen.provider, gen.model = provider.name, provider.model
    except ProviderNotConfigured:
        # Off or not configured: the template stands. Never another provider.
        gen.started_at = gen.requested_at
        _finish(db, gen, spec, GenerationStatus.FALLBACK, FailureCategory.PROVIDER_DISABLED, user)
        return gen, True
    try:
        job = jobs.submit(
            db, JOB_KIND, user, {"generation_id": gen.id}, lock_key=lock_key(spec, subject_id)
        )
    except jobs.JobConflict:
        # Another request for the same subject holds the lock. Hand back its generation only
        # if that one is still queued or running; a finished result is never passed off as the
        # answer to a new request.
        existing = latest(db, spec, subject_id)
        if existing is not None and existing.status in ACTIVE_STATUSES:
            return existing, False
        raise GenerationRefused(
            409, "ai_busy", "A generation for this subject is still finishing. Try again shortly."
        ) from None
    gen = db.get(AiGeneration, gen.id)
    gen.job_id = job.id
    _prune(db, spec, subject_id, settings.ai_keep_generations)
    db.commit()
    return gen, True


# --- Run (worker job) ---------------------------------------------------------------------------


@lru_cache(maxsize=64)
def _output_kinds(model, cited: tuple, text: tuple) -> dict[str, str] | None:
    try:
        return output_contract.classify(model, cited, text)
    except output_contract.OutputContractError:
        return None


def _refused(kind: str, where: str = "output") -> GateOutcome:
    return GateOutcome(
        passed=False, reasons=[kind], findings=[GateFinding(kind=kind, where=where[:40])]
    )


def evaluate(
    spec: OperationSpec, ctx: AuthorizedContext, text: str, instructions: list[str] | None = None
):
    """(gate outcome, parsed output or None). Schema first, then the shared checks on every text
    value of the output, then the operation's own. Every check runs on the server; none can be
    skipped by the output or by the operation's declarations.

    The text values checked are derived from the output schema (`output_contract`), not only from
    the fields an operation lists: a schema with uncovered text fails closed here even if it
    somehow escaped registration, and an output holding a string at an uncovered path is
    refused (`unchecked_field`)."""
    cited, uncited = tuple(spec.cited_fields), tuple(spec.text_fields)
    if _output_kinds(spec.output_model, cited, uncited) is None:
        return _refused("unchecked_field", "schema"), None
    if len(text) > spec.max_output_chars:
        return GateOutcome(passed=False, reasons=["schema"]), None
    try:
        out = spec.output_model.model_validate_json(text)
    except (ValidationError, ValueError):
        return GateOutcome(passed=False, reasons=["schema"]), None
    leaves, stray = output_contract.text_leaves(out, cited, uncited)
    if stray:
        return _refused("unchecked_field", stray[0]), None
    fields = {leaf.where[:40]: (leaf.text, list(leaf.citations)) for leaf in leaves}
    shared, miscited = safety.check_text_fields(ctx, fields, instructions)
    own, own_miscited = spec.check(ctx, out)
    findings = shared + own
    miscited += own_miscited
    reasons = sorted({f.kind for f in findings} | ({"miscited"} if miscited else set()))
    return (
        GateOutcome(passed=not reasons, reasons=reasons, findings=findings, miscited=miscited),
        out,
    )


def _consent_in_effect(db: Session, user: User, spec: OperationSpec, ctx: AuthorizedContext):
    """True unless the context's audience needs the subject's consent and it is not in effect."""
    from app.ai.policy import approvals
    from app.core import clock
    from app.services.common import consents

    audience = approvals.audience_of(ctx.source)
    if audience not in approvals.CONSENT_AUDIENCES:
        return True
    patient_id = getattr(user, "patient_id", None)
    if not patient_id or not spec.consent_purpose:
        return False
    return any(
        c.purpose == spec.consent_purpose and c.granted and c.in_effect
        for c in consents(db, patient_id, clock.get_today(db))
    )


def _assert_model_items_only(request: GenerationRequest, ctx: AuthorizedContext) -> None:
    """The prompt carries only what the model may read: the text of an item outside the model
    view (a server-text-only category such as consent, or a category outside the operation's
    allowlist) must not appear in any message, whatever the operation's prompt builder does."""
    blob = " ".join(m.content for m in request.messages)
    allowed = {i.text for i in ctx.model_items}
    for item in ctx.items:
        if item.text in allowed or len(item.text) < 12:
            continue
        if item.text in blob:
            raise UntrustedContentError(f"{item.id} is outside the model view")


def _assert_no_identifiers(request: GenerationRequest, identifiers: list[str]) -> None:
    import re

    blob = " ".join(m.content for m in request.messages).lower()
    for value in identifiers:
        if re.search(rf"(?<![\w-]){re.escape(value.lower())}(?![\w-])", blob):
            raise UntrustedContentError("an identifier was about to reach a model")


def run(
    db: Session,
    generation_id: int,
    *,
    settings: Settings | None = None,
    provider_factory: ProviderFactory | None = None,
) -> str:
    """Runs one queued generation to its end state. Returns the final status."""
    settings = settings or get_settings()
    provider_factory = provider_factory or get_provider
    if not ai_tables_present(db):
        return GenerationStatus.FAILED
    gen = db.get(AiGeneration, generation_id)
    if gen is None or gen.status != GenerationStatus.QUEUED:
        return gen.status if gen else GenerationStatus.FAILED
    spec = resolve(gen.operation)
    gen.status, gen.started_at = GenerationStatus.RUNNING, utcnow()
    db.commit()
    user = db.get(User, gen.requested_by_user_id) if gen.requested_by_user_id else None
    try:
        return _run(db, gen, spec, user, settings, provider_factory)
    except Exception:  # noqa: BLE001 - an unexpected error ends this generation, nothing else
        log.exception("AI generation %s failed", generation_id)
        db.rollback()
        gen = db.get(AiGeneration, generation_id)
        return _finish(db, gen, spec, GenerationStatus.FAILED, FailureCategory.INTERNAL, user)


@dataclass
class PipelineResult:
    """What one pass through the generation pipeline decided. `trace` lists every step with a
    fixed outcome code and minimised detail (no record text, no model text)."""

    status: GenerationStatus
    failure: FailureCategory | None = None
    ctx: AuthorizedContext | None = None
    request: GenerationRequest | None = None
    outcome: GateOutcome | None = None
    output: dict | None = None
    provider: str | None = None
    model: str | None = None
    model_digest: str | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    trace: list[dict] = field(default_factory=list)

    def step(self, name: str, outcome: str, **detail) -> None:
        self.trace.append({"step": name, "outcome": outcome, **detail})

    def end(self, status: GenerationStatus, failure: FailureCategory | None = None):
        self.status, self.failure = status, failure
        self.step("disposition", status.value, failure=failure.value if failure else None)
        return self


def execute(
    db: Session,
    user: User | None,
    spec: OperationSpec,
    raw_subject: str | None,
    *,
    settings: Settings,
    provider_factory: ProviderFactory,
    filed_under: str | None = None,
) -> PipelineResult:
    """The pipeline every generation goes through, whatever calls it: re-authorise and rebuild
    the context, completeness gate, mode, provider selection (local only), request-side data
    checks, provider call, schema and safety checks, composition. Writes nothing itself: `run`
    records the result, and the Admin evaluation reads the trace."""
    r = PipelineResult(status=GenerationStatus.RUNNING)
    r.step("contract", "declared", operation=spec.id, version=spec.version,
           subject_type=str(spec.subject_type), categories=list(spec.context_kinds))  # fmt: skip
    # Access is decided again now, as the requester: an assignment may have changed.
    if user is None or not getattr(user, "is_active", True):
        r.step("authorisation", "access_lost")
        return r.end(GenerationStatus.FAILED, FailureCategory.ACCESS_LOST)
    try:
        if filed_under is not None and subject_key(spec, user, raw_subject) != filed_under:
            raise NotFound("Not found")  # a scope key belongs to the reader who asked
        ctx = _context(db, user, spec, raw_subject)
    except (Forbidden, NotFound) as exc:
        r.step("authorisation", "denied", code=type(exc).__name__.lower())
        return r.end(GenerationStatus.FAILED, FailureCategory.ACCESS_LOST)
    except UntrustedContentError:
        r.step("context", "data_policy")
        return r.end(GenerationStatus.FALLBACK, FailureCategory.DATA_POLICY)
    except SourceUnavailable:
        r.step("context", "source_unavailable")
        return r.end(GenerationStatus.FALLBACK, FailureCategory.CONTEXT_UNAVAILABLE)
    r.ctx = ctx
    r.step("authorisation", "allowed", role=str(user.role), permission=spec.permission)
    r.step("context", "built", source=ctx.source, complete=ctx.complete,
           policy_version=ctx.policy_version, model_items=len(ctx.model_items),
           withheld=len(ctx.withheld_texts), identifiers=len(ctx.identifiers),
           categories=[c.model_dump(mode="json") for c in ctx.categories])  # fmt: skip
    if not ctx.complete and not spec.allow_partial:
        # Truncated, stale or partly unavailable: a model never describes partial data as if it
        # were complete. The template stands.
        unreadable = any(c.state is CategoryState.UNAVAILABLE for c in ctx.categories)
        failure = (
            FailureCategory.CONTEXT_UNAVAILABLE
            if unreadable
            else FailureCategory.CONTEXT_INCOMPLETE
        )
        r.step("completeness", "refused", allow_partial=False)
        return r.end(GenerationStatus.FALLBACK, failure)
    r.step("completeness", "passed" if ctx.complete else "partial_allowed")
    if not _consent_in_effect(db, user, spec, ctx):
        # An approved patient-facing audience still needs this patient's consent now; consent
        # alone never enables one (registration requires the owner's approval first).
        r.step("consent", "missing", purpose=spec.consent_purpose)
        return r.end(GenerationStatus.FALLBACK, FailureCategory.DATA_POLICY)

    mode = mode_for(spec, settings)
    if mode is Mode.TEMPLATE:
        r.step("mode", "template")
        return r.end(GenerationStatus.FALLBACK, FailureCategory.PROVIDER_DISABLED)
    r.step("mode", mode.value)
    try:
        provider = provider_factory(settings)
    except ProviderNotConfigured:
        r.step("provider", "not_configured")
        return r.end(GenerationStatus.FALLBACK, FailureCategory.PROVIDER_DISABLED)
    r.provider, r.model = provider.name, provider.model
    if not provider.is_local and not spec.allow_cloud:
        r.step("provider", "refused_not_local", provider=provider.name)
        return r.end(GenerationStatus.FALLBACK, FailureCategory.DATA_POLICY)
    r.step("provider", "local", provider=provider.name, model=provider.model)

    request = GenerationRequest(
        artifact=spec.id,
        prompt_version=spec.prompt_version,
        messages=spec.build_messages(ctx),
        json_schema=spec.output_schema(ctx),
        contains_identifiable_data=False,
        temperature=spec.temperature,
        seed=spec.seed,
        max_output_tokens=spec.max_output_tokens,
    )
    r.request = request
    try:
        check_data_policy(provider, request, settings)
        assert_no_free_text(request, ctx.withheld_texts, " ".join(i.text for i in ctx.model_items))
        _assert_no_identifiers(request, ctx.identifiers)
        _assert_model_items_only(request, ctx)
    except (DataPolicyError, UntrustedContentError):
        r.step("payload", "refused")
        return r.end(GenerationStatus.FALLBACK, FailureCategory.DATA_POLICY)
    r.step(
        "payload",
        "checked",
        prompt_version=spec.prompt_version,
        checks=["data_policy", "no_free_text", "no_identifiers", "model_items_only"],
    )

    digest = getattr(provider, "digest", None)
    r.model_digest = digest() if callable(digest) else None
    started = time.perf_counter()
    try:
        result = provider.generate(request)
    except ProviderError:
        r.step("generation", "provider_error")
        return r.end(GenerationStatus.FALLBACK, FailureCategory.PROVIDER_UNAVAILABLE)
    log.info("AI %s: provider answered in %.1fs", spec.id, time.perf_counter() - started)
    r.prompt_tokens, r.output_tokens = result.prompt_tokens, result.output_tokens
    r.step("generation", "answered", output_chars=len(result.text))

    system = [m.content for m in request.messages if m.role == "system"]
    outcome, out = evaluate(spec, ctx, result.text, system)
    r.outcome = outcome
    r.step("validation", "passed" if outcome.passed else "rejected", reasons=outcome.reasons,
           findings=[f.model_dump() for f in outcome.findings],
           miscited=outcome.miscited)  # fmt: skip
    if not outcome.passed:
        failure = (
            FailureCategory.SCHEMA_INVALID
            if outcome.reasons == ["schema"]
            else FailureCategory.GATE_REJECTED
        )
        return r.end(GenerationStatus.FALLBACK, failure)
    r.output = spec.compose(ctx, out)
    return r.end(GenerationStatus.COMPLETED)


def _run(db, gen, spec, user, settings, provider_factory) -> str:
    raw_subject = gen.subject_id if _is_record(spec) else None
    r = execute(db, user, spec, raw_subject, settings=settings,
                provider_factory=provider_factory, filed_under=gen.subject_id)  # fmt: skip
    if r.ctx is None:
        db.rollback()
    else:
        gen.operation_version, gen.prompt_version = spec.version, spec.prompt_version
        gen.context_fingerprint = r.ctx.fingerprint
        gen.provenance = r.ctx.provenance()
    if r.provider is not None:
        gen.provider, gen.model = r.provider, r.model
    if r.model_digest is not None:
        gen.model_digest = r.model_digest
    if r.prompt_tokens is not None or r.output_tokens is not None:
        gen.prompt_tokens, gen.output_tokens = r.prompt_tokens, r.output_tokens
    if r.outcome is not None:
        gen.gate_reasons = r.outcome.reasons
    if r.status is GenerationStatus.COMPLETED:
        gen.output = r.output
    return _finish(db, gen, spec, r.status, r.failure, user)


def reconcile(db: Session, gen: AiGeneration, spec: OperationSpec) -> None:
    """A generation whose job ended without finishing it (worker stopped, job failed) is
    marked failed, so it is never shown as still running."""
    if gen.status not in ACTIVE_STATUSES or gen.job_id is None:
        return
    job = db.get(Job, gen.job_id)
    if job is not None and job.status in (jobs.QUEUED, jobs.RUNNING):
        return
    db.refresh(gen)
    if gen.status not in ACTIVE_STATUSES:
        return
    failure = (
        FailureCategory.INTERRUPTED
        if job is None or job.status == jobs.INTERRUPTED
        else FailureCategory.INTERNAL
    )
    _finish(db, gen, spec, GenerationStatus.FAILED, failure)


# --- Current result (web request) ---------------------------------------------------------------


def current(
    db: Session,
    user: User,
    operation: str,
    subject_id: str | None,
    *,
    settings: Settings | None = None,
) -> dict:
    """What the reader sees now: the template, or a checked model result when the mode allows
    it and the result is still current. Raises `Forbidden` / `NotFound` like the read services."""
    settings = settings or get_settings()
    spec = resolve(operation)
    mode = mode_for(spec, settings)
    _require_key()
    raw_subject = subject_id
    subject_id = subject_key(spec, user, subject_id)
    ctx = _context(db, user, spec, raw_subject)
    if not ai_tables_present(db):
        raise AiUnavailable()
    gen = latest(db, spec, subject_id)
    if gen is not None:
        reconcile(db, gen, spec)
    finished = gen is not None and gen.status not in ACTIVE_STATUSES
    stale = bool(
        finished
        and gen.context_fingerprint is not None
        and gen.context_fingerprint != ctx.fingerprint
    )
    outdated = gen is not None and (
        gen.operation_version != spec.version or gen.prompt_version != spec.prompt_version
    )
    shown = bool(
        gen is not None
        and mode is Mode.PROTOTYPE
        and gen.mode == Mode.PROTOTYPE
        and gen.status == GenerationStatus.COMPLETED
        and gen.output
        and not stale
        and not outdated
    )
    result = gen.output if shown else spec.fallback(ctx)
    return {
        "operation": spec.id,
        "operation_version": spec.version,
        "mode": mode.value,
        "as_of": ctx.as_of,
        "source": "model" if shown else "template",
        "review_required": shown,
        "result": result,
        "sources": [
            {
                "id": i.id,
                "label": SOURCE_LABELS.get(i.kind, i.kind),
                "text": i.text,
                "recorded_at": i.recorded_at,
                "model_visible": i in ctx.model_items,
            }
            for i in ctx.items
        ],
        "notes": [n.model_dump(mode="json") for n in ctx.reader_notes],
        # Whether the context was complete, and why each category is or is not in it.
        "context": {
            "complete": ctx.complete,
            "policy_version": ctx.policy_version,
            "categories": [c.model_dump(mode="json") for c in ctx.categories],
        },
        "can_generate": mode is not Mode.TEMPLATE,
        "generation": (
            None
            if gen is None
            else {
                "id": gen.id,
                "status": gen.status,
                "mode": gen.mode,
                "failure": gen.failure,
                "gate_reasons": gen.gate_reasons or [],
                "provider": gen.provider,
                "model": gen.model,
                "operation_version": gen.operation_version,
                "prompt_version": gen.prompt_version,
                "requested_at": gen.requested_at,
                "finished_at": gen.finished_at,
                "duration_ms": gen.duration_ms,
                "stale": stale,
                "outdated": outdated,
                "shown": shown,
                "active": gen.status in ACTIVE_STATUSES,
            }
        ),
    }
