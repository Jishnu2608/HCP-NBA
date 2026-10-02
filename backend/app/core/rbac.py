"""Row-level access rules. Every data endpoint goes through these; the UI only mirrors them.

admin         all HCPs, all patients, all recommendations
compliance    no profiles; recommendations that are blocked or have a withheld option
medical_rep   assigned HCPs and their recommendations; no patients
care_manager  assigned patients and their recommendations; no HCPs
hcp           own profile; adherence summary of attributed patients who consent to sharing
patient       own record only
"""

from datetime import date

from sqlalchemy import and_, false, or_, select, true
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models import CareManagerPatient, Consent, Nba, PatientHcp, RepHcp, User
from app.models.enums import ConsentPurpose, NbaStatus, Role, TargetType

REVIEWER_ROLES = {
    TargetType.HCP: (Role.ADMIN, Role.MEDICAL_REP),
    TargetType.PATIENT: (Role.ADMIN, Role.CARE_MANAGER),
}


def _rep_hcps(user: User):
    return select(RepHcp.hcp_id).where(RepHcp.rep_user_id == user.id)


def _managed_patients(user: User):
    return select(CareManagerPatient.patient_id).where(
        CareManagerPatient.care_manager_user_id == user.id
    )


def hcp_filter(user: User, column) -> ColumnElement[bool]:
    """WHERE clause limiting an HCP id column to what the user may see in full."""
    if user.role == Role.ADMIN:
        return true()
    if user.role == Role.MEDICAL_REP:
        return column.in_(_rep_hcps(user))
    if user.role == Role.HCP:
        return column == user.hcp_id
    return false()


def patient_filter(user: User, column) -> ColumnElement[bool]:
    """WHERE clause limiting a patient id column to what the user may see in full."""
    if user.role == Role.ADMIN:
        return true()
    if user.role == Role.CARE_MANAGER:
        return column.in_(_managed_patients(user))
    if user.role == Role.PATIENT:
        return column == user.patient_id
    return false()


def nba_filter(user: User) -> ColumnElement[bool]:
    if user.role == Role.ADMIN:
        return true()
    if user.role == Role.COMPLIANCE:
        return or_(Nba.status == NbaStatus.BLOCKED, Nba.withheld.is_not(None))
    if user.role == Role.MEDICAL_REP:
        return and_(Nba.target_type == TargetType.HCP, Nba.target_id.in_(_rep_hcps(user)))
    if user.role == Role.CARE_MANAGER:
        return and_(
            Nba.target_type == TargetType.PATIENT, Nba.target_id.in_(_managed_patients(user))
        )
    return false()


def can_review(user: User, nba: Nba) -> bool:
    return user.role in REVIEWER_ROLES[nba.target_type]


def can_see_target_profile(user: User) -> bool:
    """Compliance sees recommendations and gate results but not the person behind them."""
    return user.role != Role.COMPLIANCE


def shared_patient_ids(db: Session, hcp_id: str, day: date) -> list[str]:
    """Patients attributed to this HCP who currently consent to sharing with their provider."""
    consented = select(Consent.patient_id).where(
        Consent.purpose == ConsentPurpose.PROVIDER_SHARING,
        Consent.granted,
        Consent.effective_from <= day,
        or_(Consent.effective_to.is_(None), Consent.effective_to > day),
    )
    return list(
        db.scalars(
            select(PatientHcp.patient_id)
            .where(PatientHcp.hcp_id == hcp_id, PatientHcp.patient_id.in_(consented))
            .order_by(PatientHcp.patient_id)
        )
    )
