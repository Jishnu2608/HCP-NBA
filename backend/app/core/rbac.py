"""Data scope: which rows a user may see, given their permissions and their assignments.

Permissions (core/permissions.py) say what kind of thing a user may do. This module turns
that into SQL filters using the assignment tables, so scope is enforced in the query and
the UI only mirrors it.

  *_READ_ALL         every row
  *_READ_ASSIGNED    rows linked to the user in rep_hcp / care_manager_patient
  NBA_READ_GATED     blocked or held-back recommendations, identity hidden
  SELF_*             the single record in user.patient_id / user.hcp_id (see api/me.py)
"""

from datetime import date

from sqlalchemy import and_, false, or_, select, true
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core.permissions import Permission as P
from app.core.permissions import can, can_any
from app.models import CareManagerPatient, Consent, Nba, PatientHcp, RepHcp, User
from app.models.enums import ConsentPurpose, NbaStatus, TargetType


def assigned_hcp_ids(user: User):
    return select(RepHcp.hcp_id).where(RepHcp.rep_user_id == user.id)


def assigned_patient_ids(user: User):
    return select(CareManagerPatient.patient_id).where(
        CareManagerPatient.care_manager_user_id == user.id
    )


def hcp_filter(user: User, column) -> ColumnElement[bool]:
    """WHERE clause limiting an HCP id column to what the user may see in full."""
    if can(user, P.HCP_READ_ALL):
        return true()
    if can(user, P.HCP_READ_ASSIGNED):
        return column.in_(assigned_hcp_ids(user))
    return false()


def patient_filter(user: User, column) -> ColumnElement[bool]:
    """WHERE clause limiting a patient id column to what the user may see in full."""
    if can(user, P.PATIENT_READ_ALL):
        return true()
    if can(user, P.PATIENT_READ_ASSIGNED):
        return column.in_(assigned_patient_ids(user))
    return false()


def nba_filter(user: User) -> ColumnElement[bool]:
    if can(user, P.NBA_READ_ALL):
        return true()
    clauses = []
    if can(user, P.NBA_READ_GATED):
        clauses.append(or_(Nba.status == NbaStatus.BLOCKED, Nba.withheld.is_not(None)))
    if can(user, P.NBA_READ_HCP_ASSIGNED):
        clauses.append(
            and_(Nba.target_type == TargetType.HCP, Nba.target_id.in_(assigned_hcp_ids(user)))
        )
    if can(user, P.NBA_READ_PATIENT_ASSIGNED):
        clauses.append(
            and_(
                Nba.target_type == TargetType.PATIENT,
                Nba.target_id.in_(assigned_patient_ids(user)),
            )
        )
    return or_(*clauses) if clauses else false()


def can_review(user: User, nba: Nba) -> bool:
    needed = P.NBA_REVIEW_HCP if nba.target_type == TargetType.HCP else P.NBA_REVIEW_PATIENT
    return can(user, needed)


def can_see_target_profile(user: User) -> bool:
    """Gate-outcome readers see recommendations and gate results, not the person behind them."""
    return can_any(user, P.NBA_READ_ALL, P.NBA_READ_HCP_ASSIGNED, P.NBA_READ_PATIENT_ASSIGNED)


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
