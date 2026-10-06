"""What is waiting for a care manager: one definition for the menu counts, the page counts
and the end-of-day check, so no screen says "nothing to do" while another says otherwise.

Care requests (the `/care` page):
  needs_you     open, in progress, or answered by the HCP; follow-ups once due
  scheduled     follow-ups the care manager owns that are not due yet (intentionally waiting)
  with_hcp      consultations waiting for the HCP (another role's turn)

Outreach (the recommendation queue):
  to_review       recommendations waiting for a decision
  to_send         approved but not sent
  call_outcomes   calls sent for which the outcome has not been recorded
  responses       real patients' responses not yet looked at by the care team
"""

from sqlalchemy import and_, func, not_, select
from sqlalchemy.orm import Session

from app.core import clock, rbac
from app.models import CareRequest, Interaction, Nba, Patient, User
from app.models.enums import (
    CareRequestStatus,
    CareRequestType,
    Channel,
    NbaStatus,
    Outcome,
    PatientOrigin,
    TargetType,
)

NEEDS_CARE_MANAGER = (
    CareRequestStatus.OPEN,
    CareRequestStatus.IN_PROGRESS,
    CareRequestStatus.HCP_RESPONDED,
)


def _count(db: Session, query) -> int:
    return db.scalar(query) or 0


def care_requests(db: Session, user: User) -> dict[str, int]:
    mine = rbac.patient_filter(user, CareRequest.patient_id)
    today = clock.today()
    follow_up = CareRequest.type == CareRequestType.FOLLOW_UP
    not_due = and_(follow_up, CareRequest.due_date > today)
    base = select(func.count()).select_from(CareRequest).where(mine)
    return {
        "needs_you": _count(
            db, base.where(CareRequest.status.in_(NEEDS_CARE_MANAGER), not_(not_due))
        ),
        "scheduled": _count(db, base.where(CareRequest.status.in_(NEEDS_CARE_MANAGER), not_due)),
        "with_hcp": _count(db, base.where(CareRequest.status == CareRequestStatus.AWAITING_HCP)),
    }


REAL_PATIENTS = select(Patient.patient_id).where(Patient.origin != PatientOrigin.SYNTHETIC)


def outreach(db: Session, user: User) -> dict[str, int]:
    scope = rbac.nba_filter(user)
    patient = Nba.target_type == TargetType.PATIENT
    base = select(func.count()).select_from(Nba).where(scope, patient)
    pending_call = (
        select(Interaction.nba_id)
        .where(Interaction.channel == Channel.PHONE, Interaction.outcome == Outcome.PENDING)
        .where(Interaction.nba_id.is_not(None))
    )
    return {
        "to_review": _count(db, base.where(Nba.status == NbaStatus.READY_FOR_REVIEW)),
        "to_send": _count(db, base.where(Nba.status == NbaStatus.APPROVED)),
        "call_outcomes": _count(
            db, base.where(Nba.status == NbaStatus.SENT, Nba.id.in_(pending_call))
        ),
        "responses": _count(
            db,
            base.where(
                Nba.status == NbaStatus.RESPONDED,
                Nba.target_id.in_(REAL_PATIENTS),
                Nba.response_reviewed_ts.is_(None),
            ),
        ),
    }


def responses_filter():
    """Recommendations whose real patient responded and nobody has looked yet."""
    return and_(
        Nba.status == NbaStatus.RESPONDED,
        Nba.target_id.in_(REAL_PATIENTS),
        Nba.response_reviewed_ts.is_(None),
    )
