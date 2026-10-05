"""Patient activity: the one ordering for every list of patients.

Every patient list in the application (Patients page, a care manager's panel, an HCP's
patients, assignment lists, searches) is ordered by latest activity, newest first, with the
patient id as a stable tie-breaker. Activity is kept on the record itself
(`patient.last_activity_at`), moved forward by the server whenever something happens for
that patient: the record is created or changed, an entry or refill is recorded, a message
is sent or answered, consent changes, the patient signs in. Lists never sort on the client.
"""

from datetime import date, datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    CareNote,
    CareRequest,
    Consent,
    Interaction,
    MedicationFill,
    Patient,
    PatientCondition,
    PatientNumber,
    PatientTherapy,
    User,
)
from app.models.enums import TargetType
from app.models.tables import utcnow


def activity_key():
    """Latest activity, falling back to the last change and then to creation."""
    return func.coalesce(Patient.last_activity_at, Patient.updated_at, Patient.created_at)


def patient_order() -> tuple:
    """ORDER BY for patient lists: most recent activity first, then patient id (stable)."""
    return (activity_key().desc().nulls_last(), Patient.patient_id)


def _as_datetime(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time(0, 0))
    return None


def touch(db: Session, patient_id: str | None, when: datetime | date | None = None) -> None:
    """Something happened for this patient at `when` (default now). Never moves backwards."""
    if not patient_id:
        return
    patient = db.get(Patient, patient_id)
    if patient is None:
        return
    moment = _as_datetime(when) or utcnow()
    if patient.last_activity_at is None or moment > patient.last_activity_at:
        patient.last_activity_at = moment


def backfill(db: Session, *, only_missing: bool = True, synthetic_only: bool = False) -> int:
    """Sets created_at / last_activity_at from each patient's own history: refills, messages,
    consent, entries, requests, notes, account sign-ins. Used after the migration (records
    that existed before the columns) and after generating synthetic data."""
    query = select(Patient)
    if only_missing:
        query = query.where(Patient.last_activity_at.is_(None))
    if synthetic_only:
        query = query.where(Patient.origin == "synthetic")
    patients = {p.patient_id: p for p in db.scalars(query)}
    if not patients:
        return 0

    latest: dict[str, datetime] = {}
    earliest: dict[str, datetime] = {}

    def later(pid, value):
        value = _as_datetime(value)
        if pid in patients and value is not None and (pid not in latest or value > latest[pid]):
            latest[pid] = value

    def earlier(pid, value):
        value = _as_datetime(value)
        if pid in patients and value is not None and (pid not in earliest or value < earliest[pid]):
            earliest[pid] = value

    for pid, value in db.execute(
        select(MedicationFill.patient_id, func.max(MedicationFill.fill_date)).group_by(
            MedicationFill.patient_id
        )
    ):
        later(pid, value)
    for pid, sent, answered in db.execute(
        select(
            Interaction.target_id, func.max(Interaction.int_ts), func.max(Interaction.outcome_ts)
        )
        .where(Interaction.target_type == TargetType.PATIENT)
        .group_by(Interaction.target_id)
    ):
        later(pid, sent)
        later(pid, answered)
    for pid, first, last in db.execute(
        select(
            Consent.patient_id, func.min(Consent.effective_from), func.max(Consent.effective_from)
        ).group_by(Consent.patient_id)
    ):
        earlier(pid, first)
        later(pid, last)
    for pid, first, confirmed in db.execute(
        select(
            PatientTherapy.patient_id,
            func.min(PatientTherapy.start_date),
            func.max(PatientTherapy.confirmed_at),
        ).group_by(PatientTherapy.patient_id)
    ):
        earlier(pid, first)
        later(pid, confirmed)
    for model, column in (
        (PatientCondition, PatientCondition.reported_at),
        (PatientCondition, PatientCondition.confirmed_at),
        (CareRequest, CareRequest.updated_at),
        (CareNote, CareNote.created_at),
    ):
        for pid, value in db.execute(
            select(model.patient_id, func.max(column)).group_by(model.patient_id)
        ):
            later(pid, value)
    for pid, created, signed_in in db.execute(
        select(User.patient_id, User.created_at, User.last_login_at).where(
            User.patient_id.is_not(None)
        )
    ):
        earlier(pid, created)
        later(pid, signed_in)
        later(pid, created)
    numbers = {f"PAT_R{n.id:06d}": n.created_at for n in db.scalars(select(PatientNumber))}

    for pid, patient in patients.items():
        created = numbers.get(pid) or earliest.get(pid)
        if patient.created_at is None or not only_missing:
            patient.created_at = created or patient.created_at
        moments = [m for m in (latest.get(pid), patient.created_at) if m is not None]
        patient.last_activity_at = max(moments) if moments else None
        if patient.updated_at is None:
            patient.updated_at = patient.created_at
    db.flush()
    return len(patients)
