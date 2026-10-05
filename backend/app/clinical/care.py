"""Care management for real patients: what the patient reports, what the care manager
records and confirms, refills, instructions, consultations and routing to an HCP.

Everything is explicit: a condition exists because someone entered it, a medication counts
for the engine only once the care team confirms it, and an HCP is linked only when a care
manager chooses one. Synthetic demo records are read-only here; their history is generated.
"""

from datetime import date, datetime, time, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app import audit
from app.api import serializers as out
from app.auth.errors import AuthError
from app.clinical import activity, records, vocabulary
from app.clinical import hcps as hcp_records
from app.core import age, clock, jurisdiction
from app.engagement import simulator
from app.models import (
    CareNote,
    CareRequest,
    Hcp,
    HcpSpecialty,
    Invitation,
    Patient,
    PatientCondition,
    PatientHcp,
    PatientTherapy,
    User,
)
from app.models.enums import (
    CareNoteKind,
    CareRequestStatus,
    CareRequestType,
    ConditionStatus,
    InvitationStatus,
    PatientOrigin,
    ReviewStatus,
    Role,
    TherapyOrigin,
)
from app.models.tables import utcnow

PATIENT_REPORTED, CARE_MANAGER = TherapyOrigin.PATIENT_REPORTED, TherapyOrigin.CARE_MANAGER


def _audit(db: Session, actor: User, action: str, patient_id: str, **detail) -> None:
    activity.touch(db, patient_id)
    audit.record(
        db, action, "patient", patient_id, actor=actor.username, actor_role=actor.role,
        detail=detail or None,
    )  # fmt: skip


def require_real(patient: Patient) -> None:
    if not records.is_real(patient):
        raise AuthError(
            409,
            "demo_record",
            "This is a generated demo record. Its history comes from the demo data.",
        )


def _text(value: str | None, field: str, limit: int, *, required: bool = False) -> str | None:
    value = " ".join((value or "").split())
    if required and not value:
        raise AuthError(422, f"invalid_{field}", f"Enter the {field.replace('_', ' ')}.")
    if len(value) > limit:
        raise AuthError(422, f"invalid_{field}", f"Keep the {field.replace('_', ' ')} short.")
    return value or None


def _date(value: str | None, field: str, *, required: bool = False) -> date | None:
    if not value:
        if required:
            raise AuthError(422, f"invalid_{field}", f"Enter the {field.replace('_', ' ')}.")
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise AuthError(422, f"invalid_{field}", "Use a valid date.") from None


def _condition_code(code: str, other_text: str | None) -> tuple[str, str | None]:
    if code not in vocabulary.CONDITIONS:
        raise AuthError(422, "invalid_condition", "Choose a condition from the list.")
    if code == vocabulary.OTHER:
        return code, _text(other_text, "condition_name", 120, required=True)
    return code, None


# --- Shapes ---------------------------------------------------------------------------------


def condition_out(c: PatientCondition) -> dict:
    return {
        "id": c.id,
        "condition": c.condition,
        "label": vocabulary.condition_label(c.condition, c.other_text),
        "specialties": vocabulary.specialties_for(c.condition),
        "origin": c.origin,
        "status": c.status,
        "reported_at": c.reported_at,
        "confirmed_at": c.confirmed_at,
    }


def medication_out(db: Session, t: PatientTherapy, today: date, *, with_risk: bool) -> dict:
    prescriber = db.get(Hcp, t.prescriber_hcp_id) if t.prescriber_hcp_id else None
    adherence = (
        out.therapy_out(db, t, today, with_risk=with_risk)
        if t.review_status == ReviewStatus.CONFIRMED
        else {}
    )
    return adherence | {
        "therapy_id": t.id,
        "drug_name": t.drug_name,
        "measure": t.measure,
        "start_date": t.start_date,
        "end_date": t.end_date,
        "days_supply": t.days_supply,
        "dose_instructions": t.dose_instructions,
        "schedule": t.schedule,
        "origin": t.origin,
        "review_status": t.review_status,
        "prescriber": out.hcp_name(prescriber) if prescriber else None,
    }


def request_out(db: Session, r: CareRequest, *, for_patient: bool = False) -> dict:
    hcp = db.get(Hcp, r.assigned_hcp_id) if r.assigned_hcp_id else None
    condition = db.get(PatientCondition, r.condition_id) if r.condition_id else None
    therapy = db.get(PatientTherapy, r.therapy_id) if r.therapy_id else None
    row = {
        "id": r.id,
        "type": r.type,
        "status": r.status,
        "reason": r.reason,
        "resolution": r.resolution,
        "condition": condition_out(condition) if condition else None,
        "medication": therapy.drug_name if therapy else None,
        "assigned_hcp": (
            {
                "hcp_id": hcp.hcp_id,
                "name": out.hcp_name(hcp),
                "specialties": hcp_records.specialty_out(db, hcp.hcp_id),
            }
            if hcp
            else None
        ),
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }
    if not for_patient:
        patient = db.get(Patient, r.patient_id)
        handler = db.get(User, r.handled_by_user_id) if r.handled_by_user_id else None
        row |= {
            "patient_id": r.patient_id,
            "patient_name": out.patient_name(patient),
            "patient_origin": patient.origin,
            "handled_by": handler.display_name if handler else None,
        }
    return row


def note_out(db: Session, n: CareNote) -> dict:
    hcp = db.get(Hcp, n.hcp_id) if n.hcp_id else None
    author = db.get(User, n.author_user_id) if n.author_user_id else None
    return {
        "id": n.id,
        "kind": n.kind,
        "text": n.text,
        "hcp": out.hcp_name(hcp) if hcp else None,
        "author": author.display_name if author else None,
        "visible_to_patient": n.visible_to_patient,
        "created_at": n.created_at,
    }


def care_team(db: Session, patient_id: str) -> dict:
    hcps = db.execute(
        select(Hcp, PatientHcp.is_primary)
        .join(PatientHcp, PatientHcp.hcp_id == Hcp.hcp_id)
        .where(PatientHcp.patient_id == patient_id)
        .order_by(PatientHcp.is_primary.desc(), Hcp.last_name)
    ).all()
    return {
        "care_managers": [
            {"id": u.id, "name": u.display_name}
            for u in records.responsible_care_managers(db, patient_id)
        ],
        "hcps": [
            {
                "hcp_id": h.hcp_id,
                "name": out.hcp_name(h),
                "specialties": hcp_records.specialty_out(db, h.hcp_id),
                "is_primary": primary,
            }
            for h, primary in hcps
        ],
    }


def _rows(db: Session, model, patient_id: str, *order):
    return db.scalars(select(model).where(model.patient_id == patient_id).order_by(*order)).all()


def health_record(db: Session, patient: Patient, *, for_patient: bool) -> dict:
    """Conditions, medications, requests, instructions and care team for one patient."""
    today = clock.get_today(db)
    pid = patient.patient_id
    notes = _rows(db, CareNote, pid, CareNote.created_at.desc())
    if for_patient:
        notes = [n for n in notes if n.visible_to_patient]
    return {
        "patient_id": pid,
        "origin": patient.origin,
        "editable": records.is_real(patient),
        "conditions": [
            condition_out(c) for c in _rows(db, PatientCondition, pid, PatientCondition.id)
        ],
        "medications": [
            medication_out(db, t, today, with_risk=not for_patient)
            for t in _rows(db, PatientTherapy, pid, PatientTherapy.id)
        ],
        "requests": [
            request_out(db, r, for_patient=for_patient)
            for r in _rows(db, CareRequest, pid, CareRequest.id.desc())
        ],
        "notes": [note_out(db, n) for n in notes],
        "care_team": care_team(db, pid),
    }


# --- What the patient reports -----------------------------------------------------------------


def _open_request(
    db: Session, patient_id: str, type_: str, *, reason=None, condition_id=None, therapy_id=None
) -> CareRequest:
    request = CareRequest(
        patient_id=patient_id,
        type=type_,
        status=CareRequestStatus.OPEN,
        reason=reason,
        condition_id=condition_id,
        therapy_id=therapy_id,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(request)
    db.flush()
    return request


def report_condition(
    db: Session, user: User, patient: Patient, code: str, other_text: str | None
) -> PatientCondition:
    require_real(patient)
    code, other_text = _condition_code(code, other_text)
    condition = PatientCondition(
        patient_id=patient.patient_id,
        condition=code,
        other_text=other_text,
        origin=PATIENT_REPORTED,
        status=ConditionStatus.REPORTED,
        reported_at=utcnow(),
    )
    db.add(condition)
    db.flush()
    _open_request(
        db, patient.patient_id, CareRequestType.CONDITION_REVIEW, condition_id=condition.id
    )
    _audit(db, user, "condition_reported", patient.patient_id, condition=code)
    return condition


MAX_PLANNED_START = 366  # days: a care team may record a course that starts later
MAX_END_AHEAD = 5 * 366  # days: a planned end date at most this far ahead


def medication_dates(
    patient: Patient, start_date: str | None, end_date: str | None, ongoing: bool, origin: str
) -> tuple[date, date | None]:
    """Start and end of a medication, checked against the real current date. Ongoing means
    no end date; otherwise an end date is required and cannot precede the start."""
    today = clock.today()
    start = _date(start_date, "start_date", required=True)
    if start < patient.birth_date:
        raise AuthError(422, "invalid_start_date", "The start date is before the date of birth.")
    if origin == PATIENT_REPORTED and start > today:
        raise AuthError(422, "invalid_start_date", "The start date cannot be in the future.")
    if start > today + timedelta(days=MAX_PLANNED_START):
        raise AuthError(422, "invalid_start_date", "The start date is more than a year ahead.")
    if ongoing:
        if end_date:
            raise AuthError(422, "invalid_end_date", "An ongoing medication has no end date.")
        return start, None
    end = _date(end_date, "end_date", required=True)
    if end < start:
        raise AuthError(422, "end_before_start", "The end date is before the start date.")
    if end > today + timedelta(days=MAX_END_AHEAD):
        raise AuthError(422, "invalid_end_date", "The end date is too far ahead.")
    return start, end


def _medication(
    db: Session,
    patient: Patient,
    *,
    name: str,
    dose_instructions: str | None,
    schedule: str | None,
    start_date: str | None,
    end_date: str | None,
    ongoing: bool,
    origin: str,
) -> PatientTherapy:
    name = _text(name, "medication_name", 64, required=True)
    start, end = medication_dates(patient, start_date, end_date, ongoing, origin)
    measure, rxnorm = vocabulary.drug_info(name)
    therapy = PatientTherapy(
        patient_id=patient.patient_id,
        measure=measure,
        drug_name=name.lower() if measure else name,
        rxnorm=rxnorm,
        start_date=start,
        end_date=end,
        days_supply=None,
        copay=0.0,
        status="active",
        origin=origin,
        review_status=ReviewStatus.REPORTED,
        dose_instructions=_text(dose_instructions, "dose_instructions", 300),
        schedule=_text(schedule, "schedule", 64),
    )
    db.add(therapy)
    db.flush()
    return therapy


def report_medication(db: Session, user: User, patient: Patient, **fields) -> PatientTherapy:
    require_real(patient)
    therapy = _medication(db, patient, origin=PATIENT_REPORTED, **fields)
    _open_request(db, patient.patient_id, CareRequestType.MEDICATION_REVIEW, therapy_id=therapy.id)
    _audit(db, user, "medication_reported", patient.patient_id, therapy=therapy.id)
    return therapy


def request_consultation(db: Session, user: User, patient: Patient, reason: str) -> CareRequest:
    require_real(patient)
    reason = _text(reason, "reason", 500, required=True)
    request = _open_request(db, patient.patient_id, CareRequestType.CONSULTATION, reason=reason)
    _audit(db, user, "consultation_requested", patient.patient_id, request=request.id)
    return request


def log_refill(
    db: Session, actor: User, therapy: PatientTherapy, fill_date: str | None, source: str
) -> None:
    """A refill of a confirmed medication, logged by the patient or the care team."""
    if therapy.review_status != ReviewStatus.CONFIRMED or not therapy.days_supply:
        raise AuthError(
            409, "not_confirmed", "Your care team has to confirm this medication first."
        )
    today = clock.get_today(db)
    day = _date(fill_date, "fill_date") or today
    if day > today or day < therapy.start_date:
        raise AuthError(422, "invalid_fill_date", "Use a date between the start date and today.")
    simulator.apply_fill(db, therapy, datetime.combine(day, time(12, 0)), source)
    _audit(db, actor, "refill_logged", therapy.patient_id, therapy=therapy.id, source=source)


# --- What the care manager records --------------------------------------------------------------


def create_clinic_patient(
    db: Session,
    cm: User,
    *,
    name: str,
    date_of_birth: str,
    country: str,
    region: str | None,
) -> Patient:
    from app.auth.service import validate_name

    name = validate_name(name)
    country, region = jurisdiction.validate(country, region)
    dob = age.parse_dob(date_of_birth)
    patient = records.create_patient(
        db, name=name, birth_date=dob, country=country, region=region,
        origin=PatientOrigin.CLINIC, created_by=cm,
    )  # fmt: skip
    # The care manager who set up the clinic patient is responsible for them.
    records.assign_care_manager(db, patient, cm)
    _audit(db, cm, "patient_record_created", patient.patient_id, origin=patient.origin)
    return patient


def add_condition(
    db: Session, cm: User, patient: Patient, code: str, other_text: str | None
) -> PatientCondition:
    require_real(patient)
    code, other_text = _condition_code(code, other_text)
    now = utcnow()
    condition = PatientCondition(
        patient_id=patient.patient_id, condition=code, other_text=other_text,
        origin=CARE_MANAGER, status=ConditionStatus.CONFIRMED, reported_at=now,
        confirmed_by_user_id=cm.id, confirmed_at=now,
    )  # fmt: skip
    db.add(condition)
    db.flush()
    _audit(db, cm, "condition_recorded", patient.patient_id, condition=code)
    return condition


def _close_linked(db: Session, cm: User, *, condition_id=None, therapy_id=None, note: str) -> None:
    column = CareRequest.condition_id if condition_id else CareRequest.therapy_id
    for r in db.scalars(
        select(CareRequest).where(
            column == (condition_id or therapy_id), CareRequest.status != CareRequestStatus.CLOSED
        )
    ):
        r.status, r.resolution, r.handled_by_user_id = CareRequestStatus.CLOSED, note, cm.id


def set_condition_status(db: Session, cm: User, condition: PatientCondition, status: str) -> None:
    patient = db.get(Patient, condition.patient_id)
    require_real(patient)
    if status not in (ConditionStatus.CONFIRMED, ConditionStatus.RESOLVED):
        raise AuthError(422, "invalid_status", "Choose confirmed or resolved.")
    condition.status = status
    if status == ConditionStatus.CONFIRMED:
        condition.confirmed_by_user_id, condition.confirmed_at = cm.id, utcnow()
        _close_linked(db, cm, condition_id=condition.id, note="Condition confirmed.")
    _audit(db, cm, f"condition_{status}", patient.patient_id, condition=condition.id)


def _confirm_fields(
    db: Session,
    therapy: PatientTherapy,
    *,
    measure: str | None,
    days_supply: int,
    copay: float,
    prescriber_hcp_id: str | None,
) -> None:
    if measure is not None and measure not in vocabulary.DRUGS:
        raise AuthError(422, "invalid_measure", "Choose a supported adherence measure.")
    if days_supply not in vocabulary.DAYS_SUPPLY_CHOICES:
        raise AuthError(422, "invalid_days_supply", "Choose 30, 60 or 90 days.")
    if not 0 <= copay <= 10000:
        raise AuthError(422, "invalid_copay", "Enter a valid copay.")
    if prescriber_hcp_id is not None and db.get(Hcp, prescriber_hcp_id) is None:
        raise AuthError(422, "unknown_record", "Unknown HCP.")
    therapy.measure = measure or therapy.measure
    therapy.days_supply, therapy.copay = days_supply, round(copay, 2)
    if prescriber_hcp_id:
        therapy.prescriber_hcp_id = prescriber_hcp_id


def add_medication(
    db: Session,
    cm: User,
    patient: Patient,
    *,
    measure: str | None,
    days_supply: int,
    copay: float,
    prescriber_hcp_id: str | None,
    **fields,
) -> PatientTherapy:
    require_real(patient)
    therapy = _medication(db, patient, origin=CARE_MANAGER, **fields)
    _confirm_fields(
        db, therapy, measure=measure, days_supply=days_supply, copay=copay,
        prescriber_hcp_id=prescriber_hcp_id,
    )  # fmt: skip
    therapy.review_status = ReviewStatus.CONFIRMED
    therapy.confirmed_by_user_id, therapy.confirmed_at = cm.id, utcnow()
    _audit(db, cm, "medication_recorded", patient.patient_id, therapy=therapy.id)
    return therapy


def confirm_medication(db: Session, cm: User, therapy: PatientTherapy, **fields) -> None:
    patient = db.get(Patient, therapy.patient_id)
    require_real(patient)
    if therapy.review_status != ReviewStatus.REPORTED:
        raise AuthError(409, "not_reported", "Only a reported medication can be confirmed.")
    _confirm_fields(db, therapy, **fields)
    therapy.review_status = ReviewStatus.CONFIRMED
    therapy.confirmed_by_user_id, therapy.confirmed_at = cm.id, utcnow()
    _close_linked(db, cm, therapy_id=therapy.id, note="Medication confirmed.")
    _audit(db, cm, "medication_confirmed", patient.patient_id, therapy=therapy.id)


def stop_medication(db: Session, cm: User, therapy: PatientTherapy) -> None:
    patient = db.get(Patient, therapy.patient_id)
    require_real(patient)
    therapy.review_status, therapy.status = ReviewStatus.STOPPED, "stopped"
    therapy.end_date = therapy.end_date or clock.get_today(db)
    _close_linked(db, cm, therapy_id=therapy.id, note="Medication stopped.")
    _audit(db, cm, "medication_stopped", patient.patient_id, therapy=therapy.id)


def add_note(
    db: Session,
    cm: User,
    patient: Patient,
    *,
    kind: str,
    text: str,
    hcp_id: str | None,
    visible_to_patient: bool,
) -> CareNote:
    require_real(patient)
    if kind not in (CareNoteKind.HCP_INSTRUCTION, CareNoteKind.FOLLOW_UP):
        raise AuthError(422, "invalid_kind", "Choose instruction or follow-up.")
    if hcp_id is not None and db.get(Hcp, hcp_id) is None:
        raise AuthError(422, "unknown_record", "Unknown HCP.")
    note = CareNote(
        patient_id=patient.patient_id, author_user_id=cm.id, hcp_id=hcp_id, kind=kind,
        text=_text(text, "note", 1000, required=True), visible_to_patient=visible_to_patient,
        created_at=utcnow(),
    )  # fmt: skip
    db.add(note)
    db.flush()
    _audit(db, cm, "care_note_added", patient.patient_id, kind=kind)
    return note


# --- Routing to an HCP ------------------------------------------------------------------------


def hcp_options(db: Session, patient: Patient, condition_id: int | None, limit: int = 40):
    """HCPs with at least one specialty that suits the patient's condition (an HCP may hold
    several): specialist first, then primary care; invited HCPs before demo ones; then those
    in the patient's state. An HCP without a configured specialty is never offered for a
    condition. Never assigns anyone by itself."""
    condition = db.get(PatientCondition, condition_id) if condition_id else None
    if condition is not None and condition.patient_id != patient.patient_id:
        raise AuthError(404, "not_found", "Condition not found.")
    code = condition.condition if condition else vocabulary.OTHER
    specialties = vocabulary.specialties_for(code)
    rank = func.min(case({s: n for n, s in enumerate(specialties)}, value=HcpSpecialty.specialty))
    order = [rank, case((Hcp.origin == hcp_records.SYNTHETIC, 1), else_=0)]
    if patient.state:
        order.append(case((Hcp.state == patient.state, 0), else_=1))
    rows = db.execute(
        select(Hcp, rank)
        .join(HcpSpecialty, HcpSpecialty.hcp_id == Hcp.hcp_id)
        .where(HcpSpecialty.specialty.in_(specialties))
        .group_by(Hcp.hcp_id)
        .order_by(*order, Hcp.last_name, Hcp.hcp_id)
        .limit(limit)
    ).all()
    label = vocabulary.condition_label(code, condition.other_text if condition else None)
    items = []
    for h, best in rows:
        matched = vocabulary.specialty_label(specialties[best])
        items.append(
            {
                "hcp_id": h.hcp_id,
                "name": out.hcp_name(h),
                "specialties": hcp_records.specialty_out(db, h.hcp_id),
                "origin": h.origin,
                "organization": h.organization,
                "location": f"{h.city}, {h.state}" if h.city and h.state else None,
                "reason": f"{matched} · {label}" if condition else matched,
            }
        )
    return {
        "condition": condition_out(condition) if condition else None,
        "specialties": [vocabulary.specialty_label(s) for s in specialties],
        "items": items,
    }


def assign_hcp(
    db: Session,
    cm: User,
    patient: Patient,
    hcp_id: str,
    *,
    condition_id: int | None = None,
    request_id: int | None = None,
) -> None:
    require_real(patient)
    hcp = db.get(Hcp, hcp_id)
    if hcp is None:
        raise AuthError(422, "unknown_record", "Unknown HCP.")
    condition = db.get(PatientCondition, condition_id) if condition_id else None
    request = db.get(CareRequest, request_id) if request_id else None
    for item in (condition, request):
        if item is not None and item.patient_id != patient.patient_id:
            raise AuthError(404, "not_found", "Not found.")
    held = set(hcp_records.specialties_of(db, hcp.hcp_id))
    if condition is not None and not held & set(vocabulary.specialties_for(condition.condition)):
        raise AuthError(
            422, "specialty_mismatch", "None of this HCP's specialties suits the condition."
        )
    link = db.get(PatientHcp, (patient.patient_id, hcp.hcp_id))
    if link is None:
        has_primary = db.scalar(
            select(PatientHcp.hcp_id).where(
                PatientHcp.patient_id == patient.patient_id, PatientHcp.is_primary
            )
        )
        db.add(
            PatientHcp(patient_id=patient.patient_id, hcp_id=hcp.hcp_id, is_primary=not has_primary)
        )
    if condition is not None:
        measure = vocabulary.measure_of_condition(condition.condition)
        for t in db.scalars(
            select(PatientTherapy).where(
                PatientTherapy.patient_id == patient.patient_id,
                PatientTherapy.measure == measure,
                PatientTherapy.prescriber_hcp_id.is_(None),
            )
        ):
            t.prescriber_hcp_id = hcp.hcp_id
    if request is not None:
        request.assigned_hcp_id = hcp.hcp_id
        request.status = CareRequestStatus.CLOSED
        request.handled_by_user_id = cm.id
        request.resolution = request.resolution or f"Routed to {out.hcp_name(hcp)}."
    db.flush()
    _audit(
        db, cm, "hcp_assigned", patient.patient_id, hcp=hcp.hcp_id, specialties=sorted(held),
        condition=condition.condition if condition else None,
    )  # fmt: skip


def update_request(
    db: Session, cm: User, request: CareRequest, status: str, resolution: str | None
) -> None:
    if status not in tuple(CareRequestStatus):
        raise AuthError(422, "invalid_status", "Choose a valid status.")
    if request.status == CareRequestStatus.CLOSED:
        raise AuthError(409, "request_closed", "This request is already closed.")
    request.status = status
    request.handled_by_user_id = cm.id
    if resolution is not None:
        request.resolution = _text(resolution, "resolution", 500)
    _audit(db, cm, "care_request_updated", request.patient_id, request=request.id, status=status)


def requests_for(db: Session, patient_filter, status: str | None, type_: str | None) -> list:
    query = select(CareRequest).where(patient_filter(CareRequest.patient_id))
    if status:
        query = query.where(CareRequest.status == status)
    if type_:
        query = query.where(CareRequest.type == type_)
    return db.scalars(query.order_by(CareRequest.id.desc()).limit(300)).all()


def portal_access(db: Session, patient: Patient) -> dict:
    """Whether the clinic patient has an account yet, or a pending invitation."""
    account = db.scalar(select(User).where(User.patient_id == patient.patient_id))
    if account is not None:
        return {"state": "active" if account.verified else "pending", "email": account.email}
    from app.auth import invitations

    inv = db.scalar(
        select(Invitation)
        .where(Invitation.patient_id == patient.patient_id)
        .order_by(Invitation.id.desc())
    )
    if inv is None:
        return {"state": "none"}
    status = invitations.refresh(db, inv)
    return {
        "state": "invited" if status == InvitationStatus.PENDING else status,
        "email": inv.email,
        "invitation_id": inv.id,
        "expires_at": inv.expires_at,
        "delivery": inv.delivery,
    }


def invite(db: Session, cm: User, patient: Patient, email: str):
    """Invites a clinic patient to the portal; the invitation is bound to this record."""
    from app.auth import invitations

    if patient.origin != PatientOrigin.CLINIC:
        raise AuthError(409, "not_clinic_record", "Only a clinic patient is invited this way.")
    return invitations.create(db, cm, email, Role.PATIENT, patient.patient_id)
