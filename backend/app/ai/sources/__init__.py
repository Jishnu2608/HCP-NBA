"""The context source registry: every domain the AI context layer can read, and every domain it
deliberately does not, with the reason.

A source is registered here explicitly. Registration lets the engine read a subject type
through the source's typed read services; it grants no access (the read services decide), and
it never lets a consumer see more than the field policy allows.
"""

from dataclasses import replace

from app.ai.contracts import SubjectType
from app.ai.sources import care, content, hcp, operational, patient, recommendation
from app.ai.sources.base import (
    Builder,
    Category,
    ContextSource,
    SourceStatus,
    SourceUnavailable,
)


class UnknownSource(LookupError):
    pass


_SOURCES: dict[str, ContextSource] = {}
_BY_SUBJECT: dict[SubjectType, str] = {}


def register_source(source: ContextSource) -> ContextSource:
    if source.id in _SOURCES and _SOURCES[source.id] is not source:
        raise ValueError(f"context source {source.id!r} is already registered")
    if source.status in (SourceStatus.IMPLEMENTED, SourceStatus.POLICY_BLOCKED):
        if source.read is None or not source.categories:
            raise ValueError(f"{source.id}: a buildable source needs a reader and categories")
        names = [c.name for c in source.categories]
        if len(names) != len(set(names)):
            raise ValueError(f"{source.id}: duplicate category names")
        from app.ai.policy import FIELD_POLICY

        for c in source.categories:
            if c.record not in FIELD_POLICY:
                raise ValueError(f"{source.id}.{c.name}: record {c.record!r} has no field policy")
        for t in source.subject_types:
            if t in _BY_SUBJECT and _BY_SUBJECT[t] != source.id:
                raise ValueError(f"subject type {t} already served by {_BY_SUBJECT[t]}")
            _BY_SUBJECT[t] = source.id
    _SOURCES[source.id] = source
    return source


def unregister_source(source_id: str) -> None:
    """Tests only: removes a source registered for one test."""
    source = _SOURCES.pop(source_id, None)
    if source is not None:
        for t in source.subject_types:
            if _BY_SUBJECT.get(t) == source_id:
                del _BY_SUBJECT[t]


def source_for(subject_type: SubjectType) -> ContextSource:
    try:
        return _SOURCES[_BY_SUBJECT[SubjectType(subject_type)]]
    except (KeyError, ValueError):
        raise UnknownSource(str(subject_type)) from None


def get_source(source_id: str) -> ContextSource:
    try:
        return _SOURCES[source_id]
    except KeyError:
        raise UnknownSource(source_id) from None


def all_sources() -> list[ContextSource]:
    return sorted(_SOURCES.values(), key=lambda s: (s.status != SourceStatus.IMPLEMENTED, s.id))


def _unsupported(id_: str, domain: str, reason: str, services: tuple[str, ...]) -> ContextSource:
    return ContextSource(
        id=id_,
        domain=domain,
        subject_types=(),
        categories=(),
        read=None,
        read_services=services,
        status=SourceStatus.UNSUPPORTED,
        status_reason=reason,
    )


for _source in (
    patient.SOURCE_DEF,
    hcp.SOURCE_DEF,
    recommendation.SOURCE_DEF,
    content.VERSION,
    content.LINEAGE,
    care.REQUEST,
    care.QUEUE,
    # Consultations are the HCP's own view: buildable for deterministic use and diagnostics,
    # but no model consumer may target them until HCP-facing AI is decided (owner, 2026-10-17).
    replace(
        care.CONSULTATIONS,
        status=SourceStatus.POLICY_BLOCKED,
        status_reason="hcp_facing_ai_not_decided",
    ),
    # The patient's own record can be built (deterministic use, diagnostics preview), but no
    # model consumer may target it until patient-facing AI wording is decided.
    replace(
        care.PATIENT_SELF,
        status=SourceStatus.POLICY_BLOCKED,
        status_reason="patient_facing_ai_not_decided",
    ),
    care.WORKFLOW,
    operational.TELEMETRY,
    operational.AUDIT,
    operational.JOBS,
    operational.MEASURES,
    operational.ACCESS,
    # Inventoried, deliberately not context sources (see AI_Context_Layer_Architecture.md).
    _unsupported(
        "inbox",
        "Patient and HCP inboxes",
        "delivered_wording_personalised_and_free_text_questions",
        ("services.inbox.inbox",),
    ),
    _unsupported(
        "legal_privacy",
        "Legal acceptances and privacy requests",
        "personal_and_free_text_no_consumer_need",
        ("legal.consents.history", "legal.requests.own"),
    ),
    _unsupported(
        "invitations",
        "Invitations",
        "personal_contact_data_and_reads_write_status",
        ("auth.invitations.listing",),
    ),
    _unsupported(
        "health_goal_plans",
        "Health goal plans, readings, check-ins, coins",
        "clinical_readings_need_policy_decision_no_typed_service",
        ("clinical.plan.plan_out", "clinical.plan.readings_series"),
    ),
    _unsupported(
        "hcp_self_profile",
        "An HCP's own profile",
        "built_inline_in_router_hcp_facing_ai_not_decided",
        ("api.me.my_profile",),
    ),
    _unsupported(
        "governance_messages",
        "MLR review records and governance conversation",
        "free_text_feedback_comments_and_messages",
        ("content.governance.visible_messages",),
    ),
):
    register_source(_source)

__all__ = [
    "Builder",
    "Category",
    "ContextSource",
    "SourceStatus",
    "SourceUnavailable",
    "UnknownSource",
    "all_sources",
    "get_source",
    "register_source",
    "source_for",
    "unregister_source",
]
