"""Care management endpoints for care managers.

Every route needs `patient:care:manage` and works only on patients on the caller's panel
(`rbac.patient_filter`); anything else looks exactly like a missing row. Writes are allowed
on real patients' records only (self-registered or clinic); generated demo records are
read-only here.
"""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import serializers as out
from app.api.deps import get_current_user, not_found, require_permission
from app.api.schemas import StrictBody
from app.auth import invitations
from app.auth.errors import AuthError
from app.clinical import care, vocabulary
from app.core import rbac
from app.core.db import get_db
from app.core.permissions import Permission, can_any
from app.models import CareRequest, Patient, PatientCondition, PatientTherapy, User
from app.models.enums import FillSource

router = APIRouter(prefix="/api/care", tags=["care"])

manager = require_permission(Permission.PATIENT_CARE_MANAGE)


class ClinicPatientBody(StrictBody):
    name: str = Field(max_length=128)
    date_of_birth: str = Field(max_length=10)
    country: str = Field(max_length=2)
    region: str | None = Field(default=None, max_length=3)


class ConditionBody(StrictBody):
    condition: str = Field(max_length=32)
    other_text: str | None = Field(default=None, max_length=120)


class ConditionStatusBody(StrictBody):
    status: Literal["confirmed", "resolved"]


class ConfirmBody(StrictBody):
    measure: str | None = Field(default=None, max_length=32)
    days_supply: int
    copay: float = 0.0
    prescriber_hcp_id: str | None = Field(default=None, max_length=16)


class MedicationBody(ConfirmBody):
    name: str = Field(max_length=64)
    dose_instructions: str | None = Field(default=None, max_length=300)
    schedule: str | None = Field(default=None, max_length=64)
    start_date: str = Field(max_length=10)
    end_date: str | None = Field(default=None, max_length=10)


class FillBody(StrictBody):
    fill_date: str | None = Field(default=None, max_length=10)


class NoteBody(StrictBody):
    kind: Literal["hcp_instruction", "follow_up"]
    text: str = Field(max_length=1000)
    hcp_id: str | None = Field(default=None, max_length=16)
    visible_to_patient: bool = True


class AssignHcpBody(StrictBody):
    hcp_id: str = Field(max_length=16)
    condition_id: int | None = None
    request_id: int | None = None


class RequestUpdateBody(StrictBody):
    status: Literal["open", "in_progress", "closed"]
    resolution: str | None = Field(default=None, max_length=500)


class InviteBody(StrictBody):
    email: str = Field(max_length=254)


def _patient(db: Session, user: User, patient_id: str) -> Patient:
    patient = db.scalar(
        select(Patient).where(
            Patient.patient_id == patient_id, rbac.patient_filter(user, Patient.patient_id)
        )
    )
    if patient is None:
        raise not_found("Patient not found")
    return patient


def _owned(db: Session, user: User, model, row_id: int):
    """A row that belongs to a patient on the caller's panel, or 404."""
    row = db.get(model, row_id)
    if row is None:
        raise not_found()
    _patient(db, user, row.patient_id)
    return row


def _record(db: Session, patient: Patient) -> dict:
    return care.health_record(db, patient, for_patient=False) | {
        "portal": care.portal_access(db, patient),
        "location": out.location_label(patient),
    }


@router.get("/vocabulary")
def vocabulary_options(user: User = Depends(get_current_user)) -> dict:
    """The condition and medication lists used by the patient and care-manager forms."""
    if not can_any(user, Permission.PATIENT_CARE_MANAGE, Permission.SELF_HEALTH_MANAGE):
        raise AuthError(403, "forbidden", "Your account cannot use this.")
    return {
        "conditions": vocabulary.condition_options(),
        "medications": vocabulary.medication_options(),
        "measures": list(vocabulary.DRUGS),
        "days_supply": list(vocabulary.DAYS_SUPPLY_CHOICES),
    }


@router.get("/requests")
def list_requests(
    status: Literal["open", "in_progress", "closed"] | None = None,
    type: Literal["condition_review", "medication_review", "consultation"] | None = None,  # noqa: A002
    user: User = Depends(manager),
    db: Session = Depends(get_db),
) -> dict:
    rows = care.requests_for(db, lambda column: rbac.patient_filter(user, column), status, type)
    every = care.requests_for(db, lambda column: rbac.patient_filter(user, column), None, None)
    counts: dict[str, int] = {}
    for r in every:
        counts[r.status] = counts.get(r.status, 0) + 1
    return {"counts": counts, "items": [care.request_out(db, r) for r in rows]}


@router.patch("/requests/{request_id}")
def update_request(
    request_id: int,
    body: RequestUpdateBody,
    user: User = Depends(manager),
    db: Session = Depends(get_db),
) -> dict:
    request = _owned(db, user, CareRequest, request_id)
    care.update_request(db, user, request, body.status, body.resolution)
    db.commit()
    return care.request_out(db, request)


@router.post("/patients", status_code=201)
def create_clinic_patient(
    body: ClinicPatientBody, user: User = Depends(manager), db: Session = Depends(get_db)
) -> dict:
    """A clinic patient's record, set up by the care manager who is then responsible."""
    patient = care.create_clinic_patient(
        db, user, name=body.name, date_of_birth=body.date_of_birth, country=body.country,
        region=body.region,
    )  # fmt: skip
    db.commit()
    return _record(db, patient)


@router.get("/patients/{patient_id}")
def patient_record(
    patient_id: str, user: User = Depends(manager), db: Session = Depends(get_db)
) -> dict:
    return _record(db, _patient(db, user, patient_id))


@router.post("/patients/{patient_id}/conditions", status_code=201)
def add_condition(
    patient_id: str,
    body: ConditionBody,
    user: User = Depends(manager),
    db: Session = Depends(get_db),
) -> dict:
    patient = _patient(db, user, patient_id)
    care.add_condition(db, user, patient, body.condition, body.other_text)
    db.commit()
    return _record(db, patient)


@router.post("/conditions/{condition_id}/status")
def set_condition_status(
    condition_id: int,
    body: ConditionStatusBody,
    user: User = Depends(manager),
    db: Session = Depends(get_db),
) -> dict:
    condition = _owned(db, user, PatientCondition, condition_id)
    care.set_condition_status(db, user, condition, body.status)
    db.commit()
    return _record(db, db.get(Patient, condition.patient_id))


@router.post("/patients/{patient_id}/medications", status_code=201)
def add_medication(
    patient_id: str,
    body: MedicationBody,
    user: User = Depends(manager),
    db: Session = Depends(get_db),
) -> dict:
    patient = _patient(db, user, patient_id)
    care.add_medication(db, user, patient, **body.model_dump())
    db.commit()
    return _record(db, patient)


@router.post("/medications/{therapy_id}/confirm")
def confirm_medication(
    therapy_id: int, body: ConfirmBody, user: User = Depends(manager), db: Session = Depends(get_db)
) -> dict:
    therapy = _owned(db, user, PatientTherapy, therapy_id)
    care.confirm_medication(db, user, therapy, **body.model_dump())
    db.commit()
    return _record(db, db.get(Patient, therapy.patient_id))


@router.post("/medications/{therapy_id}/stop")
def stop_medication(
    therapy_id: int, user: User = Depends(manager), db: Session = Depends(get_db)
) -> dict:
    therapy = _owned(db, user, PatientTherapy, therapy_id)
    care.stop_medication(db, user, therapy)
    db.commit()
    return _record(db, db.get(Patient, therapy.patient_id))


@router.post("/medications/{therapy_id}/fills", status_code=201)
def log_fill(
    therapy_id: int, body: FillBody, user: User = Depends(manager), db: Session = Depends(get_db)
) -> dict:
    therapy = _owned(db, user, PatientTherapy, therapy_id)
    care.require_real(db.get(Patient, therapy.patient_id))
    care.log_refill(db, user, therapy, body.fill_date, FillSource.CARE_MANAGER)
    db.commit()
    return _record(db, db.get(Patient, therapy.patient_id))


@router.post("/patients/{patient_id}/notes", status_code=201)
def add_note(
    patient_id: str, body: NoteBody, user: User = Depends(manager), db: Session = Depends(get_db)
) -> dict:
    patient = _patient(db, user, patient_id)
    care.add_note(db, user, patient, **body.model_dump())
    db.commit()
    return _record(db, patient)


@router.get("/patients/{patient_id}/hcp-options")
def hcp_options(
    patient_id: str,
    condition_id: int | None = Query(None),
    user: User = Depends(manager),
    db: Session = Depends(get_db),
) -> dict:
    return care.hcp_options(db, _patient(db, user, patient_id), condition_id)


@router.put("/patients/{patient_id}/hcp")
def assign_hcp(
    patient_id: str,
    body: AssignHcpBody,
    user: User = Depends(manager),
    db: Session = Depends(get_db),
) -> dict:
    patient = _patient(db, user, patient_id)
    care.assign_hcp(
        db, user, patient, body.hcp_id, condition_id=body.condition_id, request_id=body.request_id
    )
    db.commit()
    return _record(db, patient)


@router.post("/patients/{patient_id}/invite", status_code=201)
def invite_patient(
    patient_id: str, body: InviteBody, user: User = Depends(manager), db: Session = Depends(get_db)
) -> dict:
    """Invite the clinic patient to the portal. The invitation is bound to this record."""
    patient = _patient(db, user, patient_id)
    inv, dev_link = care.invite(db, user, patient, body.email)
    db.commit()
    result = {"invitation": invitations.out(db, inv, user), "record": _record(db, patient)}
    if dev_link:
        result["dev_link"] = dev_link  # local run without a mail server only
    return result
