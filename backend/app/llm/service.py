"""Turns a recommendation into stored message drafts, with validation and fallback."""

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import audit
from app.core.config import get_settings
from app.llm.base import DraftRequest, DraftResult, LLMProvider
from app.llm.template import TemplateProvider
from app.llm.validator import DraftValidationError, validate
from app.models import Content, Hcp, MessageDraft, Nba, Patient, PatientTherapy
from app.models.enums import NbaStatus, TargetType

_TEMPLATE = TemplateProvider()


def get_provider(name: str | None = None) -> LLMProvider:
    name = name or get_settings().llm_provider
    if name == "template":
        return _TEMPLATE
    if name == "claude":
        from app.llm.claude import ClaudeProvider

        return ClaudeProvider()
    raise ValueError(f"unknown LLM provider: {name}")


def build_request(db: Session, nba: Nba) -> DraftRequest:
    content = db.get(Content, nba.content_id)
    drug = specialty = None
    if nba.target_type == TargetType.PATIENT:
        person = db.get(Patient, nba.target_id)
        if nba.therapy_id:
            drug = db.get(PatientTherapy, nba.therapy_id).drug_name
    else:
        person = db.get(Hcp, nba.target_id)
        specialty = person.specialty
    return DraftRequest(
        nba_id=nba.id,
        target_type=nba.target_type,
        action=nba.action,
        channel=nba.channel,
        content_id=content.content_id,
        content_title=content.title,
        content_body=content.body,
        first_name=person.first_name,
        last_name=person.last_name or person.first_name,
        drug_name=drug,
        specialty=specialty,
        reasons=[r["text"] for r in nba.reason_codes],
    )


def _safe_draft(provider: LLMProvider, request: DraftRequest) -> tuple[DraftResult, str | None]:
    """Provider output if it validates, otherwise the template. Second item is why it fell back."""
    if provider is not _TEMPLATE:
        try:
            result = provider.draft(request)
            result.output = validate(result.output, request)
            return result, None
        except DraftValidationError as exc:
            reason = f"output rejected: {exc}"
        except Exception as exc:  # noqa: BLE001 - any provider failure must not block the reviewer
            reason = f"provider error: {type(exc).__name__}"
    else:
        reason = None
    result = _TEMPLATE.draft(request)
    result.output = validate(result.output, request)
    return result, reason


def _store(db: Session, nba: Nba, result: DraftResult) -> list[MessageDraft]:
    # Wording a person has edited is theirs: regenerate around it, never over it.
    db.execute(
        delete(MessageDraft).where(
            MessageDraft.nba_id == nba.id, MessageDraft.edited_by_user_id.is_(None)
        )
    )
    kept = db.scalar(select(func.max(MessageDraft.variant_no)).where(MessageDraft.nba_id == nba.id))
    has_selected = db.scalar(
        select(func.count()).where(MessageDraft.nba_id == nba.id, MessageDraft.is_selected)
    )
    rows = [
        MessageDraft(
            nba_id=nba.id,
            variant_no=(kept or 0) + n,
            subject=v.subject,
            body=v.body,
            provider=result.provider,
            model=result.model,
            is_selected=(n == 1 and not has_selected),
        )
        for n, v in enumerate(result.output.variants, start=1)
    ]
    db.add_all(rows)
    nba.rationale_summary = result.output.rationale_summary
    return rows


def draft_for_nba(
    db: Session,
    nba: Nba,
    provider: LLMProvider | None = None,
    *,
    actor: str = audit.log.SYSTEM_ACTOR,
    actor_role: str = audit.log.SYSTEM_ROLE,
) -> list[MessageDraft]:
    """Drafts wording for one recommendation. Blocked recommendations get no draft."""
    if nba.status == NbaStatus.BLOCKED:
        raise ValueError("blocked recommendations cannot be drafted")
    request = build_request(db, nba)
    result, fallback_reason = _safe_draft(provider or get_provider(), request)
    rows = _store(db, nba, result)
    audit.record(
        db,
        "draft_fallback" if fallback_reason else "draft_generated",
        "nba",
        nba.id,
        nba_id=nba.id,
        actor=actor,
        actor_role=actor_role,
        reason=fallback_reason,
        detail={
            "provider": result.provider,
            "model": result.model,
            "usage": result.usage,
            "variants": len(rows),
        },
    )
    db.flush()
    return rows


def draft_cycle(db: Session, cycle_id: int) -> int:
    """Template drafts for every sendable recommendation in a cycle.

    Bulk drafting always uses the offline provider so a cycle never waits on, or pays for,
    a model call per recommendation. A reviewer can ask for a model-written draft on demand.
    """
    count = 0
    for nba in db.scalars(
        select(Nba).where(Nba.cycle_id == cycle_id, Nba.status == NbaStatus.READY_FOR_REVIEW)
    ):
        result, _ = _safe_draft(_TEMPLATE, build_request(db, nba))
        _store(db, nba, result)
        count += 1
    audit.record(
        db,
        "drafts_generated",
        "engine_cycle",
        cycle_id,
        reason="Template drafts for all ready recommendations",
        detail={"count": count, "provider": _TEMPLATE.name},
    )
    db.flush()
    return count
