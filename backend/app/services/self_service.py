"""A patient's own health record, as the patient sees it (`GET /api/me/health`).

The record comes from the session (`user.patient_id`), never from a request: conditions,
medications, care requests visible to the patient, instructions visible to the patient and the
care team. Nothing here is inferred. Read only; `refresh=False` reads adherence figures without
recomputing them (the AI context layer does).
"""

from sqlalchemy.orm import Session

from app.clinical import care
from app.core.permissions import Permission, can
from app.models import Patient, User
from app.services.errors import Forbidden, NoRecord


def own_patient(db: Session, user: User) -> Patient:
    if not can(user, Permission.SELF_HEALTH_MANAGE):
        raise Forbidden("Your account has no health record.")
    patient = db.get(Patient, user.patient_id) if user.patient_id else None
    if patient is None:
        raise NoRecord("No record is linked to this account yet. Ask an administrator.")
    return patient


def own_health(db: Session, user: User, *, refresh: bool = True) -> dict:
    return care.health_record(db, own_patient(db, user), for_patient=True, refresh=refresh)
