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
    """A healthcare professional. Synthetic HCPs are generated with a practice, prescribing
    volume and history, and are the HCP engine's population. An invited HCP gets a new,
    blank record: nothing is invented for a real person. Specialties for every HCP live in
    `hcp_specialty` (an invited HCP's are set by an administrator)."""

    __tablename__ = "hcp"

    hcp_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    npi: Mapped[str | None] = mapped_column(String(10), unique=True)
    first_name: Mapped[str] = mapped_column(String(64))
    last_name: Mapped[str] = mapped_column(String(64))
    # The generator's single specialty for a synthetic HCP (used by the engine). Matching
    # and display use `hcp_specialty`.
    specialty: Mapped[str | None] = mapped_column(String(64), index=True)
    taxonomy_code: Mapped[str | None] = mapped_column(String(16))
    organization: Mapped[str | None] = mapped_column(String(128))
    city: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[str | None] = mapped_column(String(2))
    zip: Mapped[str | None] = mapped_column(String(10))
    rx_volume_annual: Mapped[int] = mapped_column(Integer, default=0)
    origin: Mapped[str] = mapped_column(
        String(16), default="synthetic", server_default="synthetic", index=True
    )
    # Derived by the feature layer.
    segment: Mapped[str | None] = mapped_column(String(32), index=True)
    value_score: Mapped[float | None] = mapped_column(Float)
    channel_affinity: Mapped[str | None] = mapped_column(String(16))


class HcpSpecialty(Base):
    """An HCP's specialties, from the controlled list (clinical/vocabulary.SPECIALTIES).
    The only source used for routing patients to an HCP."""

    __tablename__ = "hcp_specialty"

    hcp_id: Mapped[str] = mapped_column(ForeignKey("hcp.hcp_id"), primary_key=True)
    specialty: Mapped[str] = mapped_column(String(64), primary_key=True)


class HcpNumber(Base):
    """Issues the number in an invited HCP's id (HCP_R000001). Never reused."""

    __tablename__ = "hcp_number"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SpecialtyChangeRequest(Base):
    """An HCP asking an administrator to change their specialties. Nothing changes until an
    administrator approves; approval applies `requested` only if the list still equals
    `previous` (otherwise the request is stale)."""

    __tablename__ = "specialty_change_request"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hcp_id: Mapped[str] = mapped_column(ForeignKey("hcp.hcp_id"), index=True)
    requested_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    action: Mapped[str] = mapped_column(String(16))  # add / remove / replace
    requested: Mapped[list[str]] = mapped_column(JSON)
    previous: Mapped[list[str]] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    reviewer_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    decision_notes: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)


class Patient(Base):
    """A patient record. Synthetic records are generated demo people with history; a real
    person's record (self-registered or clinic) is created for them and starts empty:
    clinical data enters only through the patient's own entries or their care team."""

    __tablename__ = "patient"

    patient_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    first_name: Mapped[str] = mapped_column(String(64))
    last_name: Mapped[str] = mapped_column(String(64))
    birth_date: Mapped[date] = mapped_column(Date)
    # Known for synthetic records only; never invented for a real person.
    sex: Mapped[str | None] = mapped_column(String(1))
    city: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[str | None] = mapped_column(String(2))
    zip: Mapped[str | None] = mapped_column(String(10))
    plan_type: Mapped[str | None] = mapped_column(String(32))
    preferred_channel: Mapped[str | None] = mapped_column(String(16))
    # Derived by the feature layer: worst risk across the patient's therapies.
    risk_segment: Mapped[str | None] = mapped_column(String(16), index=True)
    origin: Mapped[str] = mapped_column(
        String(16), default="synthetic", server_default="synthetic", index=True
    )
    # Country of residence (ISO 3166-1 alpha-2) and US state, as the person gave them.
    country: Mapped[str | None] = mapped_column(String(2))
    region: Mapped[str | None] = mapped_column(String(3))
    # The care manager who created a clinic patient. A plain id, not a foreign key: `user`
    # already points at `patient`, and a cycle would break table ordering.
    created_by_user_id: Mapped[int | None] = mapped_column(Integer)
    # When the record was created and last changed, and the latest activity for the patient
    # (any change, entry, refill, message, consent or sign-in). Patient lists everywhere are
    # ordered by activity, newest first (clinical/records.patient_order).
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, index=True)


class PatientNumber(Base):
    """Issues the number in a real patient's id (PAT_R000001). Never reused, also after an
    account is deleted or the demo data is rebuilt, so a new person never inherits an id."""

    __tablename__ = "patient_number"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PatientHcp(Base):
    """Attribution of a patient to the HCPs who treat them."""

    __tablename__ = "patient_hcp"

    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.patient_id"), primary_key=True)
    hcp_id: Mapped[str] = mapped_column(ForeignKey("hcp.hcp_id"), primary_key=True, index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)


# --- Users and assignments ---------------------------------------------------


class User(Base):
    __tablename__ = "user"
    # Account ids are never reused (SQLite AUTOINCREMENT; PostgreSQL sequences already
    # behave this way), so the audit trail of a deleted account can never be mistaken for
    # a later one. This also holds across a demo reset.
    __table_args__ = {"sqlite_autoincrement": True}

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
    # Email verification: the account holder proved they control the address.
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # system = the fixed admin, seed = demo accounts from the generator, signup = a patient
    # who registered, invitation = a professional onboarded through an invitation.
    source: Mapped[str] = mapped_column(String(16), default="signup")
    # Kept for the account's lifetime; age and minor status are always derived from it on
    # the server (core/age.py) and it is never returned to a browser.
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    # Country of residence (ISO 3166-1 alpha-2) and, for the US, the state. Decides which
    # age-of-adulthood and privacy rules apply (core/jurisdiction.py). Nothing finer.
    country: Mapped[str | None] = mapped_column(String(2))
    region: Mapped[str | None] = mapped_column(String(3))
    # Professional verification: separate from email verification. Set only by a completed
    # invitation (or for platform-provisioned seed staff); no endpoint accepts it.
    professionally_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    professionally_verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    verification_source: Mapped[str | None] = mapped_column(String(16))
    invited_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    # Bumped on password rotation. Sessions themselves live in user_session.
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


class UserSession(Base):
    """A signed-in browser. The cookie carries a random token; only its hash is stored."""

    __tablename__ = "user_session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    user_agent: Mapped[str | None] = mapped_column(String(160))


class ConsentRecord(Base):
    """Append-only record of agreeing to, acknowledging or withdrawing something: which
    document or consent statement, which version, when, where the person said they live,
    and from which screen. Never updated: the current state is the latest row per kind."""

    __tablename__ = "consent_record"
    __table_args__ = (Index("ix_consent_record_user_kind", "user_id", "kind", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    kind: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(16))  # accepted / withdrawn
    jurisdiction: Mapped[str | None] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(16))  # signup / invitation / settings / reacceptance
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PrivacyRequest(Base):
    """A data-subject request (access, correction, erasure, ...). Handled by a person with
    privacy:manage; the requester sees its status. Submitting does not perform it."""

    __tablename__ = "privacy_request"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Empty once the requester's account was deleted; `subject_label` then names it.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), index=True)
    subject_label: Mapped[str | None] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="submitted", index=True)
    details: Mapped[str | None] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(Text)
    jurisdiction: Mapped[str | None] = mapped_column(String(8))
    handled_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    respond_by: Mapped[date | None] = mapped_column(Date)


class RateLimitHit(Base):
    """One counted attempt for a rate limit. The key (an address, an email + address pair,
    an account) is stored only as a keyed hash. Rows older than the longest window are
    deleted as new ones arrive."""

    __tablename__ = "rate_limit_hit"
    __table_args__ = (Index("ix_rate_limit_hit_lookup", "bucket", "key_hash", "ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bucket: Mapped[str] = mapped_column(String(32))
    key_hash: Mapped[str] = mapped_column(String(32))
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)


class Invitation(Base):
    """An invitation to join in a professional role. The role is fixed by the invitation;
    the token itself is never stored, only its hash."""

    __tablename__ = "invitation"
    # Never reused, like account ids: the audit log refers to invitations by id.
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    role: Mapped[str] = mapped_column(String(32))
    invited_by_user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    # The inviter's role when the invitation was sent (lineage survives later changes).
    inviter_role: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    delivery: Mapped[str] = mapped_column(String(16), default="email")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    replaced_by_id: Mapped[int | None] = mapped_column(ForeignKey("invitation.id"))
    # The pending account created when the recipient filled in the form (before the code).
    claimed_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    # Patient invitations only: the clinic record the care manager prepared for this person.
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patient.patient_id"))
    # HCP invitations only: specialties chosen by the inviter, applied at acceptance.
    specialties: Mapped[list[str] | None] = mapped_column(JSON(none_as_null=True))


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
    """A medication. Synthetic history arrives confirmed (claims); a patient's own entry
    starts as reported and counts for the engine only once a care manager confirms it."""

    __tablename__ = "patient_therapy"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.patient_id"), index=True)
    # Adherence measure; unknown for a reported medication outside the three measures.
    measure: Mapped[str | None] = mapped_column(String(32), index=True)
    drug_name: Mapped[str] = mapped_column(String(64))
    rxnorm: Mapped[str | None] = mapped_column(String(16))
    prescriber_hcp_id: Mapped[str | None] = mapped_column(ForeignKey("hcp.hcp_id"))
    start_date: Mapped[date] = mapped_column(Date)
    # Set by the care team when confirming; a patient's report may not know it.
    days_supply: Mapped[int | None] = mapped_column(Integer)
    copay: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="active")
    origin: Mapped[str] = mapped_column(String(16), default="claims", server_default="claims")
    review_status: Mapped[str] = mapped_column(
        String(16), default="confirmed", server_default="confirmed", index=True
    )
    dose_instructions: Mapped[str | None] = mapped_column(String(300))
    schedule: Mapped[str | None] = mapped_column(String(64))
    end_date: Mapped[date | None] = mapped_column(Date)
    confirmed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    # When the patient says they last collected a supply (their own report). The care
    # manager confirms it; it becomes a fill only then.
    reported_last_fill: Mapped[date | None] = mapped_column(Date)


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
    source: Mapped[str] = mapped_column(String(16), default="claims", server_default="claims")


class PatientCondition(Base):
    """A condition the patient or their care team recorded. Never inferred."""

    __tablename__ = "patient_condition"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.patient_id"), index=True)
    condition: Mapped[str] = mapped_column(String(32))
    other_text: Mapped[str | None] = mapped_column(String(120))
    origin: Mapped[str] = mapped_column(String(16))  # patient_reported / care_manager
    status: Mapped[str] = mapped_column(String(16), default="reported")
    reported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    confirmed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)


class CareRequest(Base):
    """Something the care manager must act on: a condition or medication to review, or a
    patient asking to consult an HCP. The care manager routes it; the patient sees its status."""

    __tablename__ = "care_request"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.patient_id"), index=True)
    type: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    reason: Mapped[str | None] = mapped_column(String(500))
    condition_id: Mapped[int | None] = mapped_column(ForeignKey("patient_condition.id"))
    therapy_id: Mapped[int | None] = mapped_column(ForeignKey("patient_therapy.id"))
    assigned_hcp_id: Mapped[str | None] = mapped_column(ForeignKey("hcp.hcp_id"))
    handled_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    resolution: Mapped[str | None] = mapped_column(String(500))
    # A follow-up is work the care team owes the patient: who owns it and by when.
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    due_date: Mapped[date | None] = mapped_column(Date)
    visible_to_patient: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class CareNote(Base):
    """An HCP's instruction or a follow-up, recorded by the care team."""

    __tablename__ = "care_note"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.patient_id"), index=True)
    author_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    hcp_id: Mapped[str | None] = mapped_column(ForeignKey("hcp.hcp_id"))
    kind: Mapped[str] = mapped_column(String(24))
    text: Mapped[str] = mapped_column(String(1000))
    visible_to_patient: Mapped[bool] = mapped_column(Boolean, default=True)
    # The request this instruction answers (a consultation, a follow-up), if any.
    request_id: Mapped[int | None] = mapped_column(ForeignKey("care_request.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


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
