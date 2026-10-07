from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    COMPLIANCE = "compliance"
    MEDICAL_REP = "medical_rep"
    CARE_MANAGER = "care_manager"
    HCP = "hcp"
    PATIENT = "patient"


class AccountStatus(StrEnum):
    PENDING = "pending"  # registered, one-time code not yet confirmed
    ACTIVE = "active"
    DISABLED = "disabled"


class AccountSource(StrEnum):
    SYSTEM = "system"
    SEED = "seed"
    SIGNUP = "signup"  # patient self-registration
    # Onboarded through an invitation: a professional, or a clinic patient invited by
    # their care manager.
    INVITATION = "invitation"


class InvitationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


class VerificationSource(StrEnum):
    """How a professional account came to be verified (separate from email verification)."""

    INVITATION = "invitation"  # completed an authorised invitation and its one-time code
    SYSTEM = "system"  # provisioned by the platform (seeded demo staff)


class PatientOrigin(StrEnum):
    """Where a patient record came from. Only synthetic records carry generated history."""

    SYNTHETIC = "synthetic"  # generated demo record (seeded, simulated)
    SELF_REGISTERED = "self_registered"  # created empty when a person signed up
    CLINIC = "clinic"  # created by a care manager for a clinic patient


class TherapyOrigin(StrEnum):
    CLAIMS = "claims"  # pharmacy-claims style history (synthetic data)
    PATIENT_REPORTED = "patient_reported"
    CARE_MANAGER = "care_manager"


class ReviewStatus(StrEnum):
    """A medication's standing. The engine only ever works from confirmed ones."""

    REPORTED = "reported"  # entered by the patient, not yet reviewed by the care team
    CONFIRMED = "confirmed"
    STOPPED = "stopped"
    DISMISSED = "dismissed"  # not added: the care team decided it does not belong


class ConditionStatus(StrEnum):
    REPORTED = "reported"
    CONFIRMED = "confirmed"
    RESOLVED = "resolved"
    # Not added: the care manager decided it does not belong (a duplicate, for example).
    DISMISSED = "dismissed"


class FillSource(StrEnum):
    CLAIMS = "claims"
    PATIENT = "patient"
    CARE_MANAGER = "care_manager"


class CareRequestType(StrEnum):
    CONDITION_REVIEW = "condition_review"
    MEDICATION_REVIEW = "medication_review"
    CONSULTATION = "consultation"
    # Work the care team owes the patient by a due date (owner: a care manager).
    FOLLOW_UP = "follow_up"


class CareRequestStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    # A consultation routed to an HCP, waiting for their response.
    AWAITING_HCP = "awaiting_hcp"
    # The HCP responded; back with the care manager to close.
    HCP_RESPONDED = "hcp_responded"
    CLOSED = "closed"


class CareNoteKind(StrEnum):
    HCP_INSTRUCTION = "hcp_instruction"
    FOLLOW_UP = "follow_up"
    # Why an HCP could not take a consultation: for the care team only.
    HCP_DECLINE = "hcp_decline"


class TargetType(StrEnum):
    HCP = "HCP"
    PATIENT = "PATIENT"


class Channel(StrEnum):
    EMAIL = "email"
    SMS = "sms"
    PORTAL = "portal"
    PHONE = "phone"
    REP_VISIT = "rep_visit"


class Measure(StrEnum):
    """The three Medicare STAR medication-adherence measures."""

    DIABETES = "diabetes"
    HYPERTENSION = "hypertension"
    CHOLESTEROL = "cholesterol"


class RiskSegment(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ConsentPurpose(StrEnum):
    OUTREACH = "outreach"
    PROVIDER_SHARING = "provider_sharing"


class MlrStatus(StrEnum):
    """Lifecycle of one content version. Only `approved` (and in date) is deliverable."""

    DRAFT = "draft"
    PENDING = "pending"  # submitted, waiting for an MLR decision
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    SUPERSEDED = "superseded"


class ActionType(StrEnum):
    # Patient-side
    REFILL_NUDGE = "refill_nudge"
    CHECK_IN = "check_in"
    EDUCATION = "education"
    COST_SUPPORT = "cost_support"
    # HCP-side
    SHARE_STUDY = "share_study"
    HCP_EDUCATION = "hcp_education"
    REP_VISIT = "rep_visit"
    PROGRAM_INFO = "program_info"


class Outcome(StrEnum):
    # Sent through the engine, response not yet known.
    PENDING = "pending"
    NO_RESPONSE = "no_response"
    OPENED = "opened"
    CLICKED = "clicked"
    REPLIED = "replied"
    COMPLETED = "completed"
    FILLED = "filled"
    DECLINED = "declined"


class NbaStatus(StrEnum):
    GENERATED = "generated"
    BLOCKED = "blocked"
    READY_FOR_REVIEW = "ready_for_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SENT = "sent"
    RESPONDED = "responded"
    EXPIRED = "expired"
