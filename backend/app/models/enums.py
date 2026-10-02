from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    COMPLIANCE = "compliance"
    MEDICAL_REP = "medical_rep"
    CARE_MANAGER = "care_manager"
    HCP = "hcp"
    PATIENT = "patient"


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
