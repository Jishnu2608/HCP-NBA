from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# --- Targets -----------------------------------------------------------------


class Hcp(Base):
    __tablename__ = "hcp"

    hcp_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    npi: Mapped[str] = mapped_column(String(10), unique=True)
    first_name: Mapped[str] = mapped_column(String(64))
    last_name: Mapped[str] = mapped_column(String(64))
    specialty: Mapped[str] = mapped_column(String(64), index=True)
    taxonomy_code: Mapped[str] = mapped_column(String(16))
    organization: Mapped[str] = mapped_column(String(128))
    city: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(2))
    zip: Mapped[str] = mapped_column(String(10))
    rx_volume_annual: Mapped[int] = mapped_column(Integer, default=0)
    # Derived by the feature layer.
    segment: Mapped[str | None] = mapped_column(String(32), index=True)
    value_score: Mapped[float | None] = mapped_column(Float)
    channel_affinity: Mapped[str | None] = mapped_column(String(16))


class Patient(Base):
    __tablename__ = "patient"

    patient_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    first_name: Mapped[str] = mapped_column(String(64))
    last_name: Mapped[str] = mapped_column(String(64))
    birth_date: Mapped[date] = mapped_column(Date)
    sex: Mapped[str] = mapped_column(String(1))
    city: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(2))
    zip: Mapped[str] = mapped_column(String(10))
    plan_type: Mapped[str] = mapped_column(String(32))
    preferred_channel: Mapped[str | None] = mapped_column(String(16))
    # Derived by the feature layer: worst risk across the patient's therapies.
    risk_segment: Mapped[str | None] = mapped_column(String(16), index=True)


class PatientHcp(Base):
    """Attribution of a patient to the HCPs who treat them."""

    __tablename__ = "patient_hcp"

    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.patient_id"), primary_key=True)
    hcp_id: Mapped[str] = mapped_column(ForeignKey("hcp.hcp_id"), primary_key=True, index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)


# --- Users and assignments ---------------------------------------------------


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Internal handle, used as the actor name in the audit log. Equals the email for sign-ups.
    username: Mapped[str] = mapped_column(String(254), unique=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    display_name: Mapped[str] = mapped_column(String(128))
    password_hash: Mapped[str] = mapped_column(String(255))
    # The role decides permissions. It is set at account creation and no endpoint changes it.
    role: Mapped[str] = mapped_column(String(32), index=True)
    # Assignments decide data scope and are independent of the role. For the HCP and
    # patient roles this is the single record the account may see as "self".
    hcp_id: Mapped[str | None] = mapped_column(ForeignKey("hcp.hcp_id"))
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patient.patient_id"))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # system = the fixed admin, seed = demo accounts from the generator, signup = registered.
    source: Mapped[str] = mapped_column(String(16), default="signup")
    # Bumped on logout or disable: every token issued before the bump stops working.
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)


class OtpChallenge(Base):
    """A one-time code awaiting entry. Holds only a hash, and is deleted once used."""

    __tablename__ = "otp_challenge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(16), default="signup")
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    resend_available_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RepHcp(Base):
    __tablename__ = "rep_hcp"

    rep_user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), primary_key=True)
    hcp_id: Mapped[str] = mapped_column(ForeignKey("hcp.hcp_id"), primary_key=True, index=True)


class CareManagerPatient(Base):
    __tablename__ = "care_manager_patient"

    care_manager_user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), primary_key=True)
    patient_id: Mapped[str] = mapped_column(
        ForeignKey("patient.patient_id"), primary_key=True, index=True
    )


# --- Patient medication and adherence ----------------------------------------


class PatientTherapy(Base):
    __tablename__ = "patient_therapy"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.patient_id"), index=True)
    measure: Mapped[str] = mapped_column(String(32), index=True)
    drug_name: Mapped[str] = mapped_column(String(64))
    rxnorm: Mapped[str | None] = mapped_column(String(16))
    prescriber_hcp_id: Mapped[str | None] = mapped_column(ForeignKey("hcp.hcp_id"))
    start_date: Mapped[date] = mapped_column(Date)
    days_supply: Mapped[int] = mapped_column(Integer)
    copay: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="active")


class MedicationFill(Base):
    __tablename__ = "medication_fill"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.patient_id"), index=True)
    therapy_id: Mapped[int] = mapped_column(ForeignKey("patient_therapy.id"), index=True)
    drug_name: Mapped[str] = mapped_column(String(64))
    fill_date: Mapped[date] = mapped_column(Date)
    days_supply: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer)
    copay: Mapped[float] = mapped_column(Float, default=0.0)


class AdherenceSnapshot(Base):
    """Per patient-therapy adherence summary as of a date. Always derived from fills."""

    __tablename__ = "adherence_snapshot"
    __table_args__ = (Index("ix_adherence_snapshot_therapy_asof", "therapy_id", "as_of_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.patient_id"), index=True)
    therapy_id: Mapped[int] = mapped_column(ForeignKey("patient_therapy.id"))
    as_of_date: Mapped[date] = mapped_column(Date)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    pdc: Mapped[float] = mapped_column(Float)
    mpr: Mapped[float] = mapped_column(Float)
    last_fill_date: Mapped[date | None] = mapped_column(Date)
    gap_days: Mapped[int] = mapped_column(Integer)
    risk_score: Mapped[float | None] = mapped_column(Float)
    risk_segment: Mapped[str | None] = mapped_column(String(16))


class Consent(Base):
    """Patient permission per channel and purpose. A hard gate, never randomized at decision."""

    __tablename__ = "consent"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.patient_id"), index=True)
    purpose: Mapped[str] = mapped_column(String(32))
    # Null channel applies to purposes that are not channel-specific (provider sharing).
    channel: Mapped[str | None] = mapped_column(String(16))
    granted: Mapped[bool] = mapped_column(Boolean)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(32), default="enrollment")


# --- Content and MLR ----------------------------------------------------------


class Content(Base):
    __tablename__ = "content"

    content_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    audience: Mapped[str] = mapped_column(String(16), index=True)
    action_type: Mapped[str] = mapped_column(String(32))
    topic: Mapped[str] = mapped_column(String(64))
    measure: Mapped[str | None] = mapped_column(String(32))
    specialty: Mapped[str | None] = mapped_column(String(64))
    channels: Mapped[list[str]] = mapped_column(JSON)
    mlr_status: Mapped[str] = mapped_column(String(16), index=True)
    effective_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)


class ContentReview(Base):
    __tablename__ = "content_review"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_id: Mapped[str] = mapped_column(ForeignKey("content.content_id"), index=True)
    reviewer_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    decision: Mapped[str] = mapped_column(String(16))
    comment: Mapped[str | None] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# --- Recommendations ----------------------------------------------------------


class EngineCycle(Base):
    __tablename__ = "engine_cycle"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    as_of_date: Mapped[date] = mapped_column(Date)
    started_ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_ts: Mapped[datetime | None] = mapped_column(DateTime)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))


class Nba(Base):
    __tablename__ = "nba"
    __table_args__ = (Index("ix_nba_target", "target_type", "target_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("engine_cycle.id"), index=True)
    target_type: Mapped[str] = mapped_column(String(8))
    target_id: Mapped[str] = mapped_column(String(16))
    therapy_id: Mapped[int | None] = mapped_column(ForeignKey("patient_therapy.id"))
    action: Mapped[str] = mapped_column(String(32))
    channel: Mapped[str] = mapped_column(String(16))
    scheduled_for: Mapped[date] = mapped_column(Date)
    timing_note: Mapped[str | None] = mapped_column(String(200))
    rationale: Mapped[str] = mapped_column(Text)
    # Short reviewer-facing version from the drafting layer; rationale stays authoritative.
    rationale_summary: Mapped[str | None] = mapped_column(Text)
    reason_codes: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    content_id: Mapped[str | None] = mapped_column(ForeignKey("content.content_id"))
    # score ranks options for one target; priority ranks targets in the work queue.
    score: Mapped[float] = mapped_column(Float)
    priority: Mapped[float] = mapped_column(Float, default=0.0)
    predicted: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    # A higher-scoring option that a compliance or consent gate held back, if any.
    withheld: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    status: Mapped[str] = mapped_column(String(24), index=True)
    block_reason: Mapped[str | None] = mapped_column(Text)
    as_of_date: Mapped[date] = mapped_column(Date)
    created_ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    reviewed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    reviewed_ts: Mapped[datetime | None] = mapped_column(DateTime)


class NbaCandidate(Base):
    """Every action considered for a target in a cycle, including losers and gated ones."""

    __tablename__ = "nba_candidate"
    __table_args__ = (Index("ix_nba_candidate_target", "cycle_id", "target_type", "target_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("engine_cycle.id"))
    nba_id: Mapped[int | None] = mapped_column(ForeignKey("nba.id"), index=True)
    target_type: Mapped[str] = mapped_column(String(8))
    target_id: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(32))
    channel: Mapped[str] = mapped_column(String(16))
    content_id: Mapped[str | None] = mapped_column(ForeignKey("content.content_id"))
    eligible: Mapped[bool] = mapped_column(Boolean)
    gate_failures: Mapped[list[str]] = mapped_column(JSON, default=list)
    score: Mapped[float | None] = mapped_column(Float)
    rank: Mapped[int | None] = mapped_column(Integer)


class MessageDraft(Base):
    __tablename__ = "message_draft"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nba_id: Mapped[int] = mapped_column(ForeignKey("nba.id"), index=True)
    variant_no: Mapped[int] = mapped_column(Integer)
    subject: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(64))
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False)
    edited_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))


# --- Engagement ---------------------------------------------------------------


class Interaction(Base):
    """Every touch and how it landed. Stands in for CRM activity history."""

    __tablename__ = "interaction"
    __table_args__ = (Index("ix_interaction_target", "target_type", "target_id", "int_ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_type: Mapped[str] = mapped_column(String(8))
    target_id: Mapped[str] = mapped_column(String(16))
    channel: Mapped[str] = mapped_column(String(16))
    int_ts: Mapped[datetime] = mapped_column(DateTime)
    type: Mapped[str] = mapped_column(String(32))
    outcome: Mapped[str] = mapped_column(String(16))
    outcome_ts: Mapped[datetime | None] = mapped_column(DateTime)
    content_id: Mapped[str | None] = mapped_column(ForeignKey("content.content_id"))
    # Patient outreach only: the therapy the touch was about.
    therapy_id: Mapped[int | None] = mapped_column(ForeignKey("patient_therapy.id"))
    nba_id: Mapped[int | None] = mapped_column(ForeignKey("nba.id"), index=True)
    # "history" = seeded baseline outreach, "nba" = sent through the engine.
    source: Mapped[str] = mapped_column(String(16), default="history")
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))


class FeatureSnapshot(Base):
    """Engagement features per target as of a date. Feeds scoring and the 360 profile view."""

    __tablename__ = "feature_snapshot"

    target_type: Mapped[str] = mapped_column(String(8), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    features: Mapped[dict[str, Any]] = mapped_column(JSON)


# --- Governance ---------------------------------------------------------------


class AuditLog(Base):
    """Append-only. Rows are never updated or deleted by application code."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    nba_id: Mapped[int | None] = mapped_column(ForeignKey("nba.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(48))
    actor: Mapped[str] = mapped_column(String(64))
    actor_role: Mapped[str] = mapped_column(String(32))
    compliance_ok: Mapped[bool | None] = mapped_column(Boolean)
    consent_ok: Mapped[bool | None] = mapped_column(Boolean)
    reason: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))


class ModelVersion(Base):
    __tablename__ = "model_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    algorithm: Mapped[str] = mapped_column(String(64))
    trained_ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    as_of_date: Mapped[date] = mapped_column(Date)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON)
    features: Mapped[list[str]] = mapped_column(JSON)
    artifact_path: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class EngineConfig(Base):
    """Tunable settings (weights, frequency caps, thresholds, demo clock) as key/value JSON."""

    __tablename__ = "engine_config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SimLatent(Base):
    """Hidden ground-truth traits used only by the data generator and outcome simulator.

    The engine, features and models must never read this table: it stands in for
    real-world behaviour that a production system could only observe through outcomes.
    """

    __tablename__ = "sim_latent"

    target_type: Mapped[str] = mapped_column(String(8), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    traits: Mapped[dict[str, Any]] = mapped_column(JSON)
