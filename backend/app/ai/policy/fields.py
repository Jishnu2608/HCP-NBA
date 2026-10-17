"""The central field policy: for every record type a context source reads, where each field may go.

One table for the whole application. A field that is not listed here cannot reach a model,
because `policy.engine.project` refuses a record with an unclassified field. A consumer's
allowlist only narrows what this table permits; it can never widen it.

Dispositions:

- MODEL: structured, may be written into model-visible context items.
- MODEL_IF_VOCABULARY: a medicine name. Model-visible only when it is a name from the approved
  vocabulary (`clinical.vocabulary`); a name a person typed is replaced by "an unlisted
  medication" and the original is withheld (closes the latent free-text path, POL-02).
- DETERMINISTIC_ONLY: server-side code may use it (counts, ordering, template text); never
  model-visible.
- READER_ONLY: free text typed by people. An authorised reader sees it verbatim under the fixed
  untrusted label; it is withheld from every model request.
- RESTRICTED: identifiers, contact and personal details, internal text. Neither model nor
  context text; identifying values are checked against model requests and output.
- PENDING_REVIEW: a class of data whose model use needs a governance decision (approved MLR
  wording, commercial HCP fields, group adherence figures). Never model-visible; available to
  server-side deterministic code only (counts, ordering, server-written text that no model
  reads). A category built from such fields is a server-text-only category, so no consumer can
  register for it.

A nested record is named with "@record" (a list or a single object of that record type).
"""

from enum import StrEnum

from app.ai.context_policy import FILL, INTERACTION, PATIENT_360, RECOMMENDATION, THERAPY, Use

POLICY_VERSION = "2026-10-17.1"


class Disposition(StrEnum):
    MODEL = "model"
    MODEL_IF_VOCABULARY = "model_if_vocabulary"
    DETERMINISTIC_ONLY = "deterministic_only"
    READER_ONLY = "reader_only"
    RESTRICTED = "restricted"
    PENDING_REVIEW = "pending_review"


D = Disposition
MODEL_VISIBLE = frozenset({D.MODEL, D.MODEL_IF_VOCABULARY})

# Field names that identify a person or carry text typed by people. None of them may ever be
# classified model-visible, in any record (checked by `validate_policy` at import).
GLOBAL_PROHIBITED = frozenset(
    {
        # identity and contact
        "name", "patient_name", "hcp_name", "target_name", "display_name", "username", "email",
        "phone", "npi", "birth_date", "date_of_birth", "patient_id", "hcp_id", "target_id",
        "prescriber_hcp_id", "user_id", "actor", "actor_user_id", "author", "owner",
        "handled_by", "sent_by", "hcp", "prescriber", "care_managers", "care_team",
        "city", "state", "country", "region", "location", "patient_location", "address",
        "zip", "organization", "entity_id", "account_ref",
        # text typed by people, or internal narrative
        "note", "notes", "reason", "outcome_reason", "body", "resolution", "text", "detail",
        "comment", "feedback", "dismissed_reason", "dose_instructions", "schedule",
        "other_text", "withdrawn_reason", "claims", "safety_info", "indication",
        "rationale", "rationale_summary", "timing_note", "block_reason", "gate_text",
        "body_for_recipient", "drafts", "audit", "params", "error", "consents", "label",
    }
)  # fmt: skip
# Restricted fields whose string values identify someone; they feed the identifier check.
IDENTIFYING = frozenset(
    {"name", "patient_name", "hcp_name", "target_name", "patient_id", "hcp_id", "target_id",
     "npi", "city", "organization", "prescriber", "hcp", "author", "owner", "handled_by"}
)  # fmt: skip

_LEGACY = {Use.MODEL: D.MODEL, Use.READER_ONLY: D.READER_ONLY, Use.EXCLUDED: D.RESTRICTED}


def _legacy(table: dict[str, Use], nested: dict[str, str] | None = None) -> dict[str, str]:
    out: dict[str, str] = {k: _legacy_value(v) for k, v in table.items()}
    out.update(nested or {})
    return out


def _legacy_value(v: Use) -> str:
    return _LEGACY[v]


FIELD_POLICY: dict[str, dict[str, str]] = {
    # --- Patient 360 (the Case Brief's source). Meaning unchanged from `context_policy`; the
    # health block is classified record by record below, for the categories built from it.
    "patient_360": _legacy(
        PATIENT_360,
        {
            "interactions": "@interaction",
            "therapies": "@therapy",
            "fills": "@fill",
            "open_nba": "@recommendation_summary",
            "health": "@health",
        },
    ),
    "interaction": _legacy(INTERACTION),
    "therapy": {**_legacy(THERAPY), "drug_name": D.MODEL_IF_VOCABULARY},
    "fill": _legacy(FILL),
    "recommendation_summary": _legacy(RECOMMENDATION, {"gate_now": D.RESTRICTED}),
    # --- Health record (clinical/care.health_record), staff or patient view.
    "health": {
        "patient_id": D.RESTRICTED,
        "origin": D.DETERMINISTIC_ONLY,
        "editable": D.DETERMINISTIC_ONLY,
        "conditions": "@condition",
        "medications": "@medication",
        "requests": "@care_request",
        "notes": "@care_note",
        "care_team": D.RESTRICTED,
    },
    "condition": {
        "id": D.DETERMINISTIC_ONLY,
        "condition": D.MODEL,  # vocabulary code
        "label": D.READER_ONLY,  # free text when the code is "other"
        "specialties": D.DETERMINISTIC_ONLY,
        "origin": D.MODEL,
        "status": D.MODEL,
        "dismissed_reason": D.READER_ONLY,
        "reported_at": D.MODEL,
        "confirmed_at": D.MODEL,
    },
    "medication": {
        "therapy_id": D.DETERMINISTIC_ONLY,
        "measure": D.DETERMINISTIC_ONLY,  # names a condition
        "drug_name": D.MODEL_IF_VOCABULARY,
        "start_date": D.MODEL,
        "end_date": D.MODEL,
        "days_supply": D.MODEL,
        "prescriber_hcp_id": D.RESTRICTED,
        "pdc": D.MODEL,
        "gap_days": D.DETERMINISTIC_ONLY,
        "last_fill_date": D.MODEL,
        "adherent": D.DETERMINISTIC_ONLY,
        "copay": D.DETERMINISTIC_ONLY,
        "mpr": D.DETERMINISTIC_ONLY,
        "risk_score": D.DETERMINISTIC_ONLY,
        "risk_segment": D.DETERMINISTIC_ONLY,
        "persistence": D.DETERMINISTIC_ONLY,
        "dose_instructions": D.READER_ONLY,
        "schedule": D.READER_ONLY,
        "origin": D.MODEL,
        "review_status": D.MODEL,
        "reported_last_fill": D.MODEL,
        "dismissed_reason": D.READER_ONLY,
        "prescriber": D.RESTRICTED,
    },
    "care_note": {
        "id": D.DETERMINISTIC_ONLY,
        "kind": D.MODEL,
        "text": D.READER_ONLY,
        "hcp_id": D.RESTRICTED,
        "hcp": D.RESTRICTED,
        "author": D.RESTRICTED,
        "by_hcp": D.DETERMINISTIC_ONLY,
        "visible_to_patient": D.DETERMINISTIC_ONLY,
        "request": D.READER_ONLY,  # carries the request's reason
        "created_at": D.MODEL,
    },
    # One care request (clinical/care.request_out). Structured status, routing state, due date
    # and timestamps are model-eligible; the reason, resolution and notes are not (POL-01).
    "care_request": {
        "id": D.DETERMINISTIC_ONLY,
        "type": D.MODEL,
        "status": D.MODEL,
        "status_label": D.DETERMINISTIC_ONLY,  # patient-facing wording
        "due_date": D.MODEL,
        "created_at": D.MODEL,
        "updated_at": D.MODEL,
        "overdue": D.MODEL,
        "reason": D.READER_ONLY,
        "resolution": D.READER_ONLY,
        "condition": "@condition",
        "medication": D.MODEL_IF_VOCABULARY,
        "assigned_hcp": D.RESTRICTED,
        "notes": "@care_note",
        "entry_pending": D.MODEL,
        "progress": D.DETERMINISTIC_ONLY,
        "patient_id": D.RESTRICTED,
        "patient_name": D.RESTRICTED,
        "patient_origin": D.DETERMINISTIC_ONLY,
        "handled_by": D.RESTRICTED,
        "owner": D.RESTRICTED,
        "visible_to_patient": D.DETERMINISTIC_ONLY,
    },
    # A consultation as the HCP sees it: a care request plus the HCP-view fields.
    "consultation": {
        **{},  # filled below from care_request
        "patient_age": D.MODEL,
        "patient_location": D.RESTRICTED,
        "care_managers": D.RESTRICTED,
        "context_available": D.DETERMINISTIC_ONLY,
        "declined_by_you": D.MODEL,
        "group": D.MODEL,
        "conditions": "@condition",
        "medications": "@medication",
    },
    # Cycle-time figures computed in code from request timestamps (no record detail).
    "care_workflow_metric": {
        "metric": D.MODEL,
        "type": D.MODEL,
        "count": D.MODEL,
        "median_days": D.MODEL,
        "p90_days": D.MODEL,
        "open": D.MODEL,
    },
    # --- HCP 360 (services/hcps.hcp_profile).
    "hcp_360": {
        "hcp_id": D.RESTRICTED,
        "npi": D.RESTRICTED,
        "name": D.RESTRICTED,
        "specialties": "@specialty",
        "origin": D.DETERMINISTIC_ONLY,
        "taxonomy_code": D.DETERMINISTIC_ONLY,
        "organization": D.RESTRICTED,
        "city": D.RESTRICTED,
        "state": D.RESTRICTED,
        "country": D.RESTRICTED,
        "location": D.RESTRICTED,
        "rx_volume_annual": D.PENDING_REVIEW,  # commercial field
        "segment": D.PENDING_REVIEW,  # commercial targeting
        "value_score": D.PENDING_REVIEW,  # commercial field
        "channel_affinity": D.MODEL,
        "as_of_date": D.MODEL,
        "features": D.DETERMINISTIC_ONLY,
        "interactions": "@interaction",
        "open_nba": "@recommendation_summary",
        "panel_adherence": D.PENDING_REVIEW,  # group adherence of consenting patients
        "sources": D.RESTRICTED,  # upstream identity records
        "contact_window": "@contact_window",
        "open_work": "@hcp_task",
    },
    "specialty": {"code": D.MODEL, "label": D.DETERMINISTIC_ONLY},
    "contact_window": {
        "allowed_now": D.MODEL,
        "next_allowed": D.MODEL,
        "last_contact": D.MODEL,
        "reason": D.DETERMINISTIC_ONLY,  # server wording of the contact limit
        "min_gap_days": D.MODEL,
    },
    "hcp_task": {
        "id": D.DETERMINISTIC_ONLY,
        "hcp_id": D.RESTRICTED,
        "hcp_name": D.RESTRICTED,
        "kind": D.MODEL,
        "status": D.MODEL,
        "intent": D.MODEL,
        "intent_label": D.DETERMINISTIC_ONLY,
        "reason": D.READER_ONLY,
        "note": D.READER_ONLY,
        "content": "@task_content",
        "owner": D.RESTRICTED,
        "yours": D.DETERMINISTIC_ONLY,
        "from_hcp": D.MODEL,
        "due_date": D.MODEL,
        "scheduled_at": D.MODEL,
        "timezone": D.DETERMINISTIC_ONLY,
        "mode": D.MODEL,
        "requested_by_hcp": D.MODEL,
        "overdue": D.MODEL,
        "due_today": D.MODEL,
        "outcome": D.MODEL,
        "outcome_reason": D.READER_ONLY,
        "completed_at": D.MODEL,
        "created_at": D.MODEL,
        "nba_id": D.DETERMINISTIC_ONLY,
        "interaction_id": D.DETERMINISTIC_ONLY,
    },
    "task_content": {
        "content_id": D.MODEL,
        "version": D.MODEL,
        "title": D.PENDING_REVIEW,
        "product": D.MODEL,
        "status": D.MODEL,
    },
    # --- Recommendation detail (services/recommendations.recommendation_detail).
    "recommendation": {
        "id": D.DETERMINISTIC_ONLY,
        "cycle_id": D.DETERMINISTIC_ONLY,
        "target_type": D.MODEL,
        "target_id": D.RESTRICTED,
        "target_name": D.RESTRICTED,
        "target_origin": D.DETERMINISTIC_ONLY,
        "segment": D.DETERMINISTIC_ONLY,
        "action": D.MODEL,
        "action_label": D.MODEL,
        "channel": D.MODEL,
        "channel_label": D.MODEL,
        "status": D.MODEL,
        "score": D.DETERMINISTIC_ONLY,
        "priority": D.DETERMINISTIC_ONLY,
        "scheduled_for": D.MODEL,
        "timing_note": D.DETERMINISTIC_ONLY,
        "rationale_summary": D.DETERMINISTIC_ONLY,
        "content_id": D.MODEL,
        "content_title": D.PENDING_REVIEW,
        "has_withheld": D.MODEL,
        "block_reason": D.DETERMINISTIC_ONLY,
        "as_of_date": D.MODEL,
        "gate_now": "@gate_now",
        "response_reviewed_ts": D.MODEL,
        "rationale": D.DETERMINISTIC_ONLY,
        "reason_codes": "@reason",
        "predicted": D.DETERMINISTIC_ONLY,
        "withheld": D.DETERMINISTIC_ONLY,
        "content": "@recommendation_content",
        "candidates": "@candidate",
        "drafts": D.RESTRICTED,  # personalised wording
        "audit": D.RESTRICTED,
        "reviewed_ts": D.MODEL,
        "response": "@response",
        "can_review": D.DETERMINISTIC_ONLY,
    },
    "gate_now": {"ok": D.MODEL, "codes": D.MODEL, "reason": D.DETERMINISTIC_ONLY},
    # One line of the decision trace: the coded kind and code only (POL-03).
    "reason": {
        "kind": D.MODEL,
        "code": D.MODEL,
        "text": D.DETERMINISTIC_ONLY,
        "points": D.DETERMINISTIC_ONLY,
        "level": D.DETERMINISTIC_ONLY,
        "patients": D.DETERMINISTIC_ONLY,
    },
    "candidate": {
        "rank": D.MODEL,
        "action": D.MODEL,
        "action_label": D.MODEL,
        "channel": D.MODEL,
        "channel_label": D.MODEL,
        "content_id": D.MODEL,
        "content_title": D.PENDING_REVIEW,
        "score": D.DETERMINISTIC_ONLY,
        "eligible": D.MODEL,
        "gate_failures": D.MODEL,  # fixed gate codes
        "gate_text": D.DETERMINISTIC_ONLY,
        "chosen": D.MODEL,
    },
    "response": {"outcome": D.MODEL, "at": D.MODEL, "sent_at": D.MODEL, "channel": D.MODEL},
    # --- Content (services/content.content_version). Governed wording awaits a decision.
    "content": {
        "content_id": D.MODEL,
        "version": D.MODEL,
        "title": D.PENDING_REVIEW,
        "body": D.PENDING_REVIEW,
        "audience": D.MODEL,
        "action_type": D.MODEL,
        "topic": D.MODEL,
        "measure": D.MODEL,
        "specialty": D.MODEL,
        "channels": D.MODEL,
        "mlr_status": D.MODEL,
        "effective_date": D.MODEL,
        "expiry_date": D.MODEL,
        "is_expired": D.MODEL,
        "usable": D.MODEL,
        "product": D.MODEL,
    },
    "recommendation_content": {},  # filled below: content + body_for_recipient
    "content_version_ref": {
        "content_id": D.MODEL,
        "version": D.MODEL,
        "mlr_status": D.MODEL,
        "effective_date": D.MODEL,
        "expiry_date": D.MODEL,
        "is_expired": D.MODEL,
        "usable": D.MODEL,
        "current": D.MODEL,
    },
    "content_reference_count": {"kind": D.MODEL, "status": D.MODEL, "count": D.MODEL},
    # --- Operational aggregates.
    "audit_activity_row": {"action": D.MODEL, "day": D.MODEL, "count": D.MODEL},
    "ai_telemetry_row": {
        "operation": D.MODEL,
        "status": D.MODEL,
        "failure": D.MODEL,
        "count": D.MODEL,
        "median_ms": D.MODEL,
        "p95_ms": D.MODEL,
        "prompt_tokens": D.MODEL,
        "output_tokens": D.MODEL,
        "subjects": D.MODEL,  # distinct subject hashes counted, never listed
    },
    "job_queue_row": {
        "kind": D.MODEL,
        "status": D.MODEL,
        "count": D.MODEL,
        "oldest_minutes": D.MODEL,
    },
    # Group adherence figures (already suppressed below the minimum group size) can still expose
    # sensitive information in aggregate: pending a data-use decision (owner, 2026-10-17). The
    # server may compute with them; a model never reads them.
    "quality_measure": {
        "measure": D.MODEL,
        "patients": D.PENDING_REVIEW,
        "suppressed": D.MODEL,
        "adherent_share": D.PENDING_REVIEW,
        "persistence_share": D.PENDING_REVIEW,
        "courses": D.PENDING_REVIEW,
    },
    "access_scope": {
        "account_ref": D.RESTRICTED,
        "role": D.DETERMINISTIC_ONLY,
        "permissions": D.DETERMINISTIC_ONLY,
        "assigned_patients": D.DETERMINISTIC_ONLY,
        "assigned_hcps": D.DETERMINISTIC_ONLY,
        "status": D.DETERMINISTIC_ONLY,
    },
}
FIELD_POLICY["consultation"] = {**FIELD_POLICY["care_request"], **FIELD_POLICY["consultation"]}
FIELD_POLICY["recommendation_content"] = {
    **FIELD_POLICY["content"],
    "body_for_recipient": D.RESTRICTED,  # personalised with the recipient's name
}


class PolicyError(ValueError):
    """The field policy itself is inconsistent (a prohibited field marked model-visible, a
    nested reference to an unknown record type, an unknown disposition)."""


def validate_policy(policy: dict[str, dict[str, str]] = FIELD_POLICY) -> None:
    for record, fields in policy.items():
        for name, value in fields.items():
            if isinstance(value, str) and value.startswith("@"):
                if value[1:] not in policy:
                    raise PolicyError(f"{record}.{name}: unknown nested record {value}")
                continue
            try:
                disposition = Disposition(value)
            except ValueError:
                raise PolicyError(f"{record}.{name}: unknown disposition {value!r}") from None
            if disposition in MODEL_VISIBLE and name in GLOBAL_PROHIBITED:
                raise PolicyError(f"{record}.{name} is globally prohibited from model context")


def model_visible_fields(policy: dict[str, dict[str, str]] = FIELD_POLICY) -> set[str]:
    """Every `record.field` a model may see, for enumeration tests and diagnostics."""
    return {
        f"{record}.{name}"
        for record, fields in policy.items()
        for name, value in fields.items()
        if not str(value).startswith("@") and Disposition(value) in MODEL_VISIBLE
    }


validate_policy()
