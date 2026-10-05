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


class ConditionStatus(StrEnum):
    REPORTED = "reported"
    CONFIRMED = "confirmed"
    RESOLVED = "resolved"


class FillSource(StrEnum):
    CLAIMS = "claims"
    PATIENT = "patient"
    CARE_MANAGER = "care_manager"


class CareRequestType(StrEnum):
    CONDITION_REVIEW = "condition_review"
    MEDICATION_REVIEW = "medication_review"
    CONSULTATION = "consultation"


class CareRequestStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    CLOSED = "closed"


class CareNoteKind(StrEnum):
    HCP_INSTRUCTION = "hcp_instruction"
    FOLLOW_UP = "follow_up"


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
    APPROVED = "approved"
    PENDING = "pending"
    REJECTED = "rejected"


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
