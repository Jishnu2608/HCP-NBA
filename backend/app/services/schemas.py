"""Typed contracts for the read services and the API responses built from them.

Field names, nullability and formats are those the web application already reads; these
models make them explicit (OpenAPI documents them, a mobile client can generate types from
them) without changing them. Dates serialise as ISO dates ("2026-10-13"), instants as ISO
UTC timestamps without an offset, as before.

`Num` keeps an integer an integer and a float a float, so a figure never changes its JSON
form because it passed through a model. Nested structures that are free-form by design
(engine feature snapshots, audit `detail`) stay `dict[str, Any]` and are documented where
they are produced.
"""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

Num = int | float


class Contract(BaseModel):
    """A response contract: unknown fields are an error, so a model can never silently drop
    (or invent) a field the client reads."""

    model_config = ConfigDict(extra="forbid")


# --- Shared pieces ----------------------------------------------------------------------


class PersonRef(Contract):
    id: int
    name: str


class SpecialtyOut(Contract):
    code: str
    label: str


class ConsentOut(Contract):
    purpose: str
    channel: str | None
    granted: bool
    effective_from: date
    effective_to: date | None
    in_effect: bool
    source: str


class InteractionOut(Contract):
    id: int
    ts: datetime
    channel: str
    channel_label: str
    type: str
    type_label: str
    outcome: str
    outcome_ts: datetime | None
    content_id: str | None
    content_title: str | None
    content_version: int | None
    product: str | None
    source: str
    nba_id: int | None
    intent: str | None
    outcome_reason: str | None
    note: str | None
    # HCP history only: who carried out an engine or representative touch.
    sent_by: str | None = None


class GateNow(Contract):
    ok: bool
    codes: list[str]
    reason: str | None


class RecommendationSummary(Contract):
    id: int
    cycle_id: int
    target_type: str
    target_id: str | None
    target_name: str | None
    target_origin: str | None
    segment: str | None
    action: str
    action_label: str
    channel: str
    channel_label: str
    status: str
    score: Num
    priority: Num
    scheduled_for: date
    timing_note: str | None
    rationale_summary: str | None
    content_id: str | None
    content_title: str | None
    has_withheld: bool
    block_reason: str | None
    as_of_date: date
    gate_now: GateNow | None
    response_reviewed_ts: datetime | None


# --- Patient 360 ------------------------------------------------------------------------


class Persistence(Contract):
    first_fill: date | None
    days_persistent: int
    discontinued: bool
    discontinued_on: date | None
    gap_days_allowed: int


class TherapyOut(Contract):
    therapy_id: int
    measure: str | None
    drug_name: str
    start_date: date
    days_supply: int | None
    prescriber_hcp_id: str | None
    pdc: Num | None
    gap_days: int | None
    last_fill_date: date | None
    adherent: bool | None
    # Staff views only.
    copay: Num | None = None
    mpr: Num | None = None
    risk_score: Num | None = None
    risk_segment: str | None = None
    persistence: Persistence | None = None


class FillOut(Contract):
    therapy_id: int
    fill_date: date
    days_supply: int


class CareTeamMember(Contract):
    hcp_id: str
    name: str
    specialties: list[SpecialtyOut]
    is_primary: bool


class PatientProfile(Contract):
    patient_id: str
    name: str
    age: int
    sex: str | None
    plan_type: str | None
    origin: str
    city: str | None
    state: str | None
    country: str | None
    region: str | None
    location: str | None
    preferred_channel: str | None
    risk_segment: str | None
    risk_history: list[dict[str, Any]]
    as_of_date: date
    therapies: list[TherapyOut]
    fills: list[FillOut]
    care_team: list[CareTeamMember]
    consents: list[ConsentOut]
    outreach_channels_granted: int
    outreach_channels_total: int
    care_managers: list[PersonRef]
    last_cycle_at: datetime | None
    # The engine's engagement feature snapshot for this patient (pipeline.py).
    features: dict[str, Any] | None
    interactions: list[InteractionOut]
    open_nba: RecommendationSummary | None
    # Conditions, reported and confirmed medications, care requests, notes (clinical/care.py).
    health: dict[str, Any]


# --- HCP 360 ----------------------------------------------------------------------------


class ContactWindow(Contract):
    allowed_now: bool
    next_allowed: date
    last_contact: date | None
    reason: str | None
    min_gap_days: int


class PanelMeasure(Contract):
    measure: str
    label: str
    level: str | None
    scope: str | None
    suppressed: bool
    patients: int | None = None
    adherent_share: Num | None = None
    mean_pdc: Num | None = None
    in_gap_share: Num | None = None
    below_target: bool | None = None


class PanelAdherence(Contract):
    min_cohort: int
    pdc_threshold: Num
    target: Num
    basis: str
    measures: list[PanelMeasure]


class SourceMatch(Contract):
    source: str
    source_label: str
    source_key: str
    name_in_source: str
    npi_in_source: str | None
    state: str | None
    zip: str | None
    status: str
    status_label: str
    rule: str | None
    rule_label: str | None
    confidence: Num | None
    hcp_id: str | None
    candidates: list[str]
    other_candidates: int | None = None


class HcpSources(Contract):
    registered_directly: bool
    merged: list[SourceMatch]
    review: list[SourceMatch]


class HcpProfile(Contract):
    hcp_id: str
    npi: str | None
    name: str
    specialties: list[SpecialtyOut]
    origin: str
    taxonomy_code: str | None
    organization: str | None
    city: str | None
    state: str | None
    country: str | None
    location: str | None
    rx_volume_annual: int | None
    segment: str | None
    value_score: Num | None
    channel_affinity: str | None
    as_of_date: date
    features: dict[str, Any] | None
    interactions: list[InteractionOut]
    open_nba: RecommendationSummary | None
    panel_adherence: PanelAdherence
    sources: HcpSources
    contact_window: ContactWindow
    # Commercial work items (commercial/tasks.task_out).
    open_work: list[dict[str, Any]]


# --- Recommendation detail ----------------------------------------------------------------


class Reason(BaseModel):
    """One line of the rationale. `kind` and `code` are fixed vocabularies (nba/rationale.py);
    some reasons carry extra numbers (`points`, `level`, `patients`)."""

    model_config = ConfigDict(extra="allow")

    kind: str
    code: str
    text: str


class ContentOut(Contract):
    content_id: str
    version: int
    title: str
    body: str
    audience: str
    action_type: str
    topic: str
    measure: str | None
    specialty: str | None
    channels: list[str]
    mlr_status: str
    effective_date: date | None
    expiry_date: date | None
    is_expired: bool
    usable: bool
    product: str | None


class RecommendationContent(ContentOut):
    body_for_recipient: str | None


class CandidateOut(Contract):
    rank: int | None
    action: str
    action_label: str
    channel: str
    channel_label: str
    content_id: str | None
    content_title: str | None
    score: Num | None
    eligible: bool
    gate_failures: list[str]
    gate_text: str | None
    chosen: bool


class DraftOut(Contract):
    id: int
    variant_no: int
    subject: str | None
    body: str
    provider: str
    model: str | None
    is_selected: bool
    edited: bool


class AuditEntry(Contract):
    id: int
    ts: datetime
    nba_id: int | None
    entity_type: str
    entity_id: str
    action: str
    actor: str
    actor_user_id: int | None
    actor_is_you: bool
    actor_role: str
    actor_verified: bool
    compliance_ok: bool | None
    consent_ok: bool | None
    reason: str | None
    detail: Any


class ResponseOut(Contract):
    outcome: str
    at: datetime | None
    sent_at: datetime
    channel: str


class RecommendationDetail(RecommendationSummary):
    rationale: str
    reason_codes: list[Reason]
    predicted: dict[str, Any] | None
    withheld: dict[str, Any] | None
    content: RecommendationContent
    candidates: list[CandidateOut]
    drafts: list[DraftOut]
    audit: list[AuditEntry]
    reviewed_ts: datetime | None
    response: ResponseOut | None
    can_review: bool


# --- Inbox --------------------------------------------------------------------------------


class InboxMessage(Contract):
    id: int
    author: str
    body: str
    ts: datetime


class InboxItem(Contract):
    id: int
    received: datetime
    channel: str
    subject: str | None
    body: str
    status: str
    responded: datetime | None
    can_refill: bool
    channel_note: str | None
    content_notice: str | None
    product: str | None
    intent: str | None
    # HCP inbox only: which approved content it is, who sent it, the MLR conversation.
    content_title: str | None = None
    content_version: int | None = None
    sent_by: str | None = None
    conversation: list[InboxMessage] | None = None
    can_ask: bool | None = None
