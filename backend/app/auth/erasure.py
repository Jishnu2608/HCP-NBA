"""Permanent deletion of a patient account (the erasure part of a privacy request).

Removes the account and, for a real patient, the patient record with everything recorded
for it: medications, refills, conditions, care requests and notes, outreach consent,
recommendations, messages and engine snapshots. A demo account on a generated record is
only unlinked: the record is demo data, not the person's.

The audit trail keeps its rows (what happened, when, which member of staff acted), but
the deleted person's email, name, account handle and patient id inside them are replaced
by a neutral label. This is the one place application code changes audit rows; the change
itself is recorded as `account_deleted` without any identifying value.

Afterwards the same email can register again and gets a new account id and a new patient
id (neither is ever reused), starting empty.
"""

from sqlalchemy import String, cast, delete, or_, select, update
from sqlalchemy.orm import Session

from app import audit
from app.auth.errors import AuthError
from app.clinical import records
from app.core.db import Base
from app.core.permissions import Permission, can
from app.models import (
    AdherenceSnapshot,
    AuditLog,
    CareManagerPatient,
    CareNote,
    CareRequest,
    Consent,
    ConsentRecord,
    FeatureSnapshot,
    Interaction,
    Invitation,
    MedicationFill,
    MessageDraft,
    Nba,
    NbaCandidate,
    OtpChallenge,
    Patient,
    PatientCondition,
    PatientHcp,
    PatientTherapy,
    PrivacyRequest,
    SimLatent,
    User,
    UserSession,
)
from app.models.enums import AccountSource, TargetType
from app.models.tables import utcnow


def label_for(user: User) -> str:
    return f"deleted patient #{user.id}"


def _check(admin: User, user: User, confirm_email: str) -> None:
    if user.id == admin.id:
        raise AuthError(409, "cannot_change_self", "You cannot delete your own account.")
    if user.source == AccountSource.SYSTEM or not can(user, Permission.SELF_HEALTH_MANAGE):
        raise AuthError(
            409,
            "not_deletable",
            "Only patient accounts can be deleted. Disable other accounts instead.",
        )
    if (confirm_email or "").strip().lower() != user.email:
        raise AuthError(
            422, "confirmation_mismatch", "Type the account's email address to confirm."
        )


def _delete_patient_data(db: Session, patient_id: str) -> dict:
    target = (Nba.target_type == TargetType.PATIENT) & (Nba.target_id == patient_id)
    nba_ids = list(db.scalars(select(Nba.id).where(target)))
    therapy_ids = list(
        db.scalars(select(PatientTherapy.id).where(PatientTherapy.patient_id == patient_id))
    )
    counts = {"recommendations": len(nba_ids), "medications": len(therapy_ids)}
    if nba_ids:
        db.execute(update(AuditLog).where(AuditLog.nba_id.in_(nba_ids)).values(nba_id=None))
        db.execute(delete(MessageDraft).where(MessageDraft.nba_id.in_(nba_ids)))
    db.execute(
        delete(NbaCandidate).where(
            NbaCandidate.target_type == TargetType.PATIENT, NbaCandidate.target_id == patient_id
        )
    )
    db.execute(
        delete(Interaction).where(
            Interaction.target_type == TargetType.PATIENT, Interaction.target_id == patient_id
        )
    )
    if nba_ids:
        db.execute(delete(Nba).where(Nba.id.in_(nba_ids)))
    for model in (FeatureSnapshot, SimLatent):
        db.execute(
            delete(model).where(
                model.target_type == TargetType.PATIENT, model.target_id == patient_id
            )
        )
    db.execute(delete(CareRequest).where(CareRequest.patient_id == patient_id))
    db.execute(delete(CareNote).where(CareNote.patient_id == patient_id))
    for model in (AdherenceSnapshot, MedicationFill, PatientCondition, Consent):
        db.execute(delete(model).where(model.patient_id == patient_id))
    db.execute(delete(PatientTherapy).where(PatientTherapy.patient_id == patient_id))
    db.execute(delete(PatientHcp).where(PatientHcp.patient_id == patient_id))
    db.execute(delete(CareManagerPatient).where(CareManagerPatient.patient_id == patient_id))
    db.execute(delete(Invitation).where(Invitation.patient_id == patient_id))
    db.execute(delete(Patient).where(Patient.patient_id == patient_id))
    return counts


def _clear_user_references(db: Session, user_id: int) -> None:
    """Any remaining pointer to the account (for example who confirmed or edited something)
    is emptied, so no row can lead back to it."""
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            refs = {fk.column.table.name for fk in column.foreign_keys}
            if "user" in refs and column.nullable and table.name != "user":
                db.execute(table.update().where(column == user_id).values({column.name: None}))


def _redact(value, secrets: dict[str, str]):
    if isinstance(value, str):
        for secret, replacement in secrets.items():
            value = value.replace(secret, replacement)
        return value
    if isinstance(value, dict):
        return {k: _redact(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, secrets) for v in value]
    return value


def _redact_audit(db: Session, user: User, patient_id: str | None, label: str) -> int:
    secrets = {s: label for s in (user.email, user.username, patient_id) if s}
    if len(user.display_name) >= 4:
        secrets[user.display_name] = label
    matches = [AuditLog.entity_id == str(user.id), AuditLog.actor.in_(list(secrets))]
    for secret in secrets:
        matches += [
            AuditLog.entity_id == secret,
            AuditLog.reason.contains(secret),
            cast(AuditLog.detail, String).contains(secret),
        ]
    rows = db.scalars(select(AuditLog).where(or_(*matches))).all()
    for row in rows:
        if row.entity_type == "user" and row.entity_id == str(user.id):
            row.entity_id = label[:32]
        row.entity_id = _redact(row.entity_id, secrets)[:32]
        row.actor = _redact(row.actor, secrets)[:64]
        row.reason = _redact(row.reason, secrets)
        row.detail = _redact(row.detail, secrets)
    return len(rows)


def erase_patient(
    db: Session,
    admin: User,
    user: User,
    confirm_email: str,
    privacy_request_id: int | None = None,
) -> dict:
    _check(admin, user, confirm_email)
    label = label_for(user)
    request = db.get(PrivacyRequest, privacy_request_id) if privacy_request_id else None
    if privacy_request_id and (request is None or request.user_id != user.id):
        raise AuthError(404, "request_not_found", "That privacy request is not this account's.")

    patient = db.get(Patient, user.patient_id) if user.patient_id else None
    patient_id = patient.patient_id if patient else None
    counts: dict = {}
    user.patient_id = None
    db.flush()
    if records.is_real(patient):
        counts = _delete_patient_data(db, patient_id)
    # A demo account's generated record stays: it is demo data, not this person's.

    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    db.execute(delete(OtpChallenge).where(OtpChallenge.user_id == user.id))
    db.execute(delete(ConsentRecord).where(ConsentRecord.user_id == user.id))
    db.execute(
        delete(Invitation).where(
            Invitation.claimed_user_id == user.id, Invitation.patient_id.is_(None)
        )
    )
    others = PrivacyRequest.id != (request.id if request else -1)
    db.execute(delete(PrivacyRequest).where(PrivacyRequest.user_id == user.id, others))
    if request is not None:
        # Kept, anonymised, as the record that the erasure was carried out.
        request.user_id = None
        request.subject_label = label
        request.details = None
        request.status = "completed"
        request.handled_by_user_id = admin.id
        request.resolution = "Account and patient data deleted."
        request.updated_at = utcnow()
    _clear_user_references(db, user.id)
    redacted = _redact_audit(db, user, patient_id if records.is_real(patient) else None, label)
    db.flush()
    db.delete(user)
    db.flush()
    audit.record(
        db, "account_deleted", "user", label[:32], actor=admin.username, actor_role=admin.role,
        detail={
            "subject": label,
            "patient_data_deleted": bool(counts),
            **counts,
            "audit_rows_redacted": redacted,
            "privacy_request": request.id if request else None,
        },
    )  # fmt: skip
    return {"deleted": label, "patient_data_deleted": bool(counts), **counts}
