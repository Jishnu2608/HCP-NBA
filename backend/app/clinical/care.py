"""Care management for real patients: what the patient reports, what the care manager
records and confirms, refills, instructions, consultations and routing to an HCP.

Everything is explicit: a condition exists because someone entered it, a medication counts
for the engine only once the care team confirms it, and an HCP is linked only when a care
manager chooses one. Synthetic demo records are read-only here; their history is generated.
"""

from datetime import date, timedelta

from sqlalchemy import and_, case, func, select
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
    MedicationFill,
    Nba,
    Patient,
    PatientCondition,
    PatientHcp,
    PatientTherapy,
    User,
)
from app.models.enums import (
    AccountStatus,
    ActionType,
    CareNoteKind,
    CareRequestStatus,
    CareRequestType,
    ConditionStatus,
    FillSource,
    InvitationStatus,
    NbaStatus,
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
        "dismissed_reason": c.dismissed_reason,
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
        "reported_last_fill": t.reported_last_fill,
        "dismissed_reason": t.dismissed_reason,
        "prescriber": out.hcp_name(prescriber) if prescriber else None,
    }


OPEN_STATES = (
    CareRequestStatus.OPEN,
    CareRequestStatus.IN_PROGRESS,
    CareRequestStatus.AWAITING_HCP,
    CareRequestStatus.HCP_RESPONDED,
)


def hcp_account(db: Session, hcp_id: str | None) -> User | None:
    """The portal account linked to an HCP record, if any."""
    if not hcp_id:
        return None
    return db.scalar(select(User).where(User.hcp_id == hcp_id))


def hcp_available(db: Session, hcp_id: str) -> bool:
    """False only for an HCP whose portal account was disabled: they can no longer act."""
    account = hcp_account(db, hcp_id)
    return account is None or account.status != AccountStatus.DISABLED


def _hcp_brief(db: Session, hcp: Hcp) -> dict:
    account = hcp_account(db, hcp.hcp_id)
    return {
        "hcp_id": hcp.hcp_id,
        "name": out.hcp_name(hcp),
        "specialties": hcp_records.specialty_out(db, hcp.hcp_id),
        # Responds in the app (an active portal account) or off-platform (the care manager
        # records the response); unavailable once the account is disabled.
        "in_app": account is not None and account.status == AccountStatus.ACTIVE,
        "available": account is None or account.status != AccountStatus.DISABLED,
    }


def _status_label(r: CareRequest, hcp: Hcp | None) -> str:
    """What the request means for the patient, in their words."""
    doctor = out.hcp_name(hcp) if hcp else "the healthcare professional"
    if r.status == CareRequestStatus.AWAITING_HCP:
        return f"Waiting for {doctor}"
    if r.status == CareRequestStatus.HCP_RESPONDED:
        return f"{doctor} responded"
    if r.type == CareRequestType.FOLLOW_UP and r.status != CareRequestStatus.CLOSED:
        due = f" by {r.due_date:%b} {r.due_date.day}" if r.due_date else ""
        return f"Your care team will follow up{due}"
    if r.status == CareRequestStatus.IN_PROGRESS:
        return "Your care manager is working on it"
    if r.status == CareRequestStatus.OPEN:
        return "With your care manager"
    if (r.resolution or "").startswith(WITHDRAWN):
        return "Withdrawn by the care manager"
    return "Closed"


def request_out(db: Session, r: CareRequest, *, for_patient: bool = False) -> dict:
    hcp = db.get(Hcp, r.assigned_hcp_id) if r.assigned_hcp_id else None
    condition = db.get(PatientCondition, r.condition_id) if r.condition_id else None
    therapy = db.get(PatientTherapy, r.therapy_id) if r.therapy_id else None
    owner = db.get(User, r.owner_user_id) if r.owner_user_id else None
    notes = db.scalars(
        select(CareNote).where(CareNote.request_id == r.id).order_by(CareNote.created_at)
    ).all()
    if for_patient:
        notes = [n for n in notes if n.visible_to_patient]
    today = clock.today()
    row = {
        "id": r.id,
        "type": r.type,
        "status": r.status,
        "status_label": _status_label(r, hcp),
        "reason": r.reason,
        "resolution": r.resolution,
        "condition": condition_out(condition) if condition else None,
        "medication": therapy.drug_name if therapy else None,
        "assigned_hcp": _hcp_brief(db, hcp) if hcp else None,
        "due_date": r.due_date,
        "overdue": bool(r.due_date and r.status != CareRequestStatus.CLOSED and r.due_date < today),
        "notes": [note_out(db, n) for n in notes],
        # A review request whose entry still waits for confirm or dismiss (it closes then).
        "entry_pending": entry_unresolved(db, r) if r.status != CareRequestStatus.CLOSED else False,
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
            "owner": owner.display_name if owner else None,
            "visible_to_patient": r.visible_to_patient,
        }
    return row


def note_out(db: Session, n: CareNote) -> dict:
    hcp = db.get(Hcp, n.hcp_id) if n.hcp_id else None
    author = db.get(User, n.author_user_id) if n.author_user_id else None
    request = db.get(CareRequest, n.request_id) if n.request_id else None
    return {
        "id": n.id,
        "kind": n.kind,
        "text": n.text,
        "hcp": out.hcp_name(hcp) if hcp else None,
        "author": author.display_name if author else None,
        # Written by the HCP themselves (in the app), or recorded by the care team.
        "by_hcp": bool(author and author.hcp_id and author.hcp_id == n.hcp_id),
        "visible_to_patient": n.visible_to_patient,
        "request": (
            {
                "id": request.id,
                "type": request.type,
                "reason": request.reason,
                "created_at": request.created_at,
            }
            if request
            else None
        ),
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
        # Only care managers who can act; none means one is being assigned.
        "care_managers": [
            {"id": u.id, "name": u.display_name}
            for u in records.responsible_care_managers(db, patient_id, active_only=True)
        ],
        "hcps": [_hcp_brief(db, h) | {"is_primary": primary} for h, primary in hcps],
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
            if not for_patient or r.visible_to_patient
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


def _same_condition(
    db: Session,
    patient_id: str,
    code: str,
    other_text: str | None,
    statuses=(ConditionStatus.REPORTED, ConditionStatus.CONFIRMED),
    *,
    excluding: int | None = None,
) -> PatientCondition | None:
    """The patient's existing entry for the same condition ("Other" entries compare their
    text), whoever recorded it."""
    for existing in db.scalars(
        select(PatientCondition).where(
            PatientCondition.patient_id == patient_id,
            PatientCondition.condition == code,
            PatientCondition.status.in_(statuses),
        )
    ):
        same_other = (existing.other_text or "").casefold() == (other_text or "").casefold()
        if existing.id != excluding and (code != vocabulary.OTHER or same_other):
            return existing
    return None


def report_condition(
    db: Session, user: User, patient: Patient, code: str, other_text: str | None
) -> PatientCondition:
    require_real(patient)
    code, other_text = _condition_code(code, other_text)
    if _same_condition(db, patient.patient_id, code, other_text):
        label = vocabulary.condition_label(code, other_text)
        raise AuthError(
            409,
            "duplicate_condition",
            f"{label} is already on your profile, so your care team already has it.",
        )
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


def parse_last_refill(value: str | None, start: date) -> date | None:
    """When a supply was last collected: optional, between the start date and today."""
    day = _date(value, "last_refill_date")
    if day is not None and not start <= day <= clock.today():
        raise AuthError(
            422, "invalid_last_refill_date", "Use a date between the start date and today."
        )
    return day


ACTIVE_MEDICATION = (ReviewStatus.REPORTED, ReviewStatus.CONFIRMED)


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
    drug_name = name.lower() if measure else name
    duplicate = db.scalar(
        select(PatientTherapy.id).where(
            PatientTherapy.patient_id == patient.patient_id,
            func.lower(PatientTherapy.drug_name) == drug_name.lower(),
            PatientTherapy.review_status.in_(ACTIVE_MEDICATION),
        )
    )
    if duplicate:
        raise AuthError(
            409,
            "duplicate_medication",
            f"{drug_name[:1].upper()}{drug_name[1:]} is already on the medication list.",
        )
    therapy = PatientTherapy(
        patient_id=patient.patient_id,
        measure=measure,
        drug_name=drug_name,
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


def report_medication(
    db: Session, user: User, patient: Patient, *, last_refill_date: str | None = None, **fields
) -> PatientTherapy:
    require_real(patient)
    therapy = _medication(db, patient, origin=PATIENT_REPORTED, **fields)
    # The patient's own word; it becomes a fill only when the care team confirms it.
    therapy.reported_last_fill = parse_last_refill(last_refill_date, therapy.start_date)
    _open_request(db, patient.patient_id, CareRequestType.MEDICATION_REVIEW, therapy_id=therapy.id)
    _audit(db, user, "medication_reported", patient.patient_id, therapy=therapy.id)
    return therapy


def request_consultation(db: Session, user: User, patient: Patient, reason: str) -> CareRequest:
    require_real(patient)
    reason = _text(reason, "reason", 500, required=True)
    if db.scalar(
        select(CareRequest.id).where(
            CareRequest.patient_id == patient.patient_id,
            CareRequest.type == CareRequestType.CONSULTATION,
            CareRequest.status != CareRequestStatus.CLOSED,
        )
    ):
        raise AuthError(
            409,
            "consultation_open",
            "You already have a consultation request in progress. Its progress is under "
            "Your requests.",
        )
    request = _open_request(db, patient.patient_id, CareRequestType.CONSULTATION, reason=reason)
    _audit(db, user, "consultation_requested", patient.patient_id, request=request.id)
    return request


# Recommendations about a medication's supply: pointless once the patient refilled it.
SUPPLY_ACTIONS = (ActionType.REFILL_NUDGE, ActionType.CHECK_IN, ActionType.COST_SUPPORT)
OPEN_NBA = (NbaStatus.READY_FOR_REVIEW, NbaStatus.APPROVED, NbaStatus.BLOCKED)


def expire_recommendations(
    db: Session, actor: User, therapy: PatientTherapy, reason: str, actions=None
) -> int:
    """Open recommendations for this medication that no longer make sense (it was refilled,
    stopped or dismissed) leave every queue, with the reason audited."""
    query = select(Nba).where(Nba.therapy_id == therapy.id, Nba.status.in_(OPEN_NBA))
    if actions is not None:
        query = query.where(Nba.action.in_(actions))
    count = 0
    for nba in db.scalars(query):
        nba.status, nba.block_reason = NbaStatus.EXPIRED, reason
        audit.record(
            db, "nba_expired", "nba", nba.id, nba_id=nba.id, actor=actor.username,
            actor_role=actor.role, reason=reason,
        )  # fmt: skip
        count += 1
    return count


def refresh_adherence(db: Session, patient_id: str) -> None:
    """Days covered, gap and risk for this patient, now (not at the next engine cycle)."""
    from app import pipeline

    db.flush()
    pipeline.refresh_patient(db, patient_id)


def log_refill(
    db: Session, actor: User, therapy: PatientTherapy, fill_date: str | None, source: str
) -> bool:
    """A refill of a confirmed, active medication, logged by the patient or the care team.
    Returns False, recording nothing, when a refill of it is already recorded that day."""
    if therapy.review_status in (ReviewStatus.STOPPED, ReviewStatus.DISMISSED):
        raise AuthError(409, "not_active", "This medication is no longer active.")
    if therapy.review_status != ReviewStatus.CONFIRMED or not therapy.days_supply:
        raise AuthError(
            409, "not_confirmed", "Your care team has to confirm this medication first."
        )
    today = clock.get_today(db)
    day = _date(fill_date, "fill_date") or today
    if day > today or day < therapy.start_date:
        raise AuthError(422, "invalid_fill_date", "Use a date between the start date and today.")
    if db.scalar(
        select(MedicationFill.id).where(
            MedicationFill.therapy_id == therapy.id, MedicationFill.fill_date == day
        )
    ):
        return False
    simulator.apply_fill(db, therapy, clock.day_instant(day), source)
    _audit(db, actor, "refill_logged", therapy.patient_id, therapy=therapy.id, source=source)
    expire_recommendations(db, actor, therapy, "Patient refilled", SUPPLY_ACTIONS)
    refresh_adherence(db, therapy.patient_id)
    return True


def require_new_refill(recorded: bool) -> None:
    if not recorded:
        raise AuthError(
            409, "refill_already_recorded", "This refill is already recorded for that day."
        )


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
    existing = _same_condition(db, patient.patient_id, code, other_text)
    if existing is not None:
        label = vocabulary.condition_label(code, other_text)
        hint = (
            "Confirm or dismiss the patient's report instead."
            if existing.status == ConditionStatus.REPORTED
            else "It is already confirmed."
        )
        raise AuthError(
            409, "duplicate_condition", f"{label} is already on this patient's record. {hint}"
        )
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


def set_condition_status(
    db: Session, cm: User, condition: PatientCondition, status: str, reason: str | None = None
) -> None:
    patient = db.get(Patient, condition.patient_id)
    require_real(patient)
    if status not in (
        ConditionStatus.CONFIRMED,
        ConditionStatus.RESOLVED,
        ConditionStatus.DISMISSED,
    ):
        raise AuthError(422, "invalid_status", "Choose confirmed, resolved or dismissed.")
    if status == ConditionStatus.DISMISSED:
        if condition.status != ConditionStatus.REPORTED:
            raise AuthError(409, "not_reported", "Only a reported condition can be dismissed.")
        why = _text(reason, "reason", 300, required=True)
        condition.status, condition.dismissed_reason = status, why
        _close_linked(db, cm, condition_id=condition.id, note=f"Not added: {why}")
        _audit(db, cm, "condition_dismissed", patient.patient_id, condition=condition.id)
        return
    if status == ConditionStatus.CONFIRMED and _same_condition(
        db, patient.patient_id, condition.condition, condition.other_text,
        (ConditionStatus.CONFIRMED,), excluding=condition.id,
    ):  # fmt: skip
        raise AuthError(
            409,
            "duplicate_condition",
            "This condition is already confirmed on the record. Dismiss this report instead.",
        )
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
    if prescriber_hcp_id is not None:
        # The prescriber is someone on the patient's care team, never an arbitrary HCP.
        if db.get(PatientHcp, (therapy.patient_id, prescriber_hcp_id)) is None:
            raise AuthError(422, "not_on_care_team", "Choose an HCP on the patient's care team.")
    therapy.measure = measure or therapy.measure
    therapy.days_supply, therapy.copay = days_supply, round(copay, 2)
    if prescriber_hcp_id:
        therapy.prescriber_hcp_id = prescriber_hcp_id


def _record_last_refill(db: Session, cm: User, therapy: PatientTherapy, day: date | None) -> None:
    """A medication already being taken: the last collected supply becomes its first fill, so
    it is not mistaken for a prescription that was never filled."""
    if day is not None:
        log_refill(db, cm, therapy, day.isoformat(), FillSource.CARE_MANAGER)


def add_medication(
    db: Session,
    cm: User,
    patient: Patient,
    *,
    measure: str | None,
    days_supply: int,
    copay: float,
    prescriber_hcp_id: str | None,
    last_refill_date: str | None = None,
    **fields,
) -> PatientTherapy:
    require_real(patient)
    therapy = _medication(db, patient, origin=CARE_MANAGER, **fields)
    _confirm_fields(
        db, therapy, measure=measure, days_supply=days_supply, copay=copay,
        prescriber_hcp_id=prescriber_hcp_id,
    )  # fmt: skip
    last = parse_last_refill(last_refill_date, therapy.start_date)
    therapy.review_status = ReviewStatus.CONFIRMED
    therapy.confirmed_by_user_id, therapy.confirmed_at = cm.id, utcnow()
    _audit(db, cm, "medication_recorded", patient.patient_id, therapy=therapy.id)
    _record_last_refill(db, cm, therapy, last)
    refresh_adherence(db, patient.patient_id)
    return therapy


def confirm_medication(
    db: Session, cm: User, therapy: PatientTherapy, *, last_refill_date: str | None = None, **fields
) -> None:
    patient = db.get(Patient, therapy.patient_id)
    require_real(patient)
    if therapy.review_status != ReviewStatus.REPORTED:
        raise AuthError(409, "not_reported", "Only a reported medication can be confirmed.")
    _confirm_fields(db, therapy, **fields)
    # The care manager's date, or else what the patient said when reporting it.
    last = parse_last_refill(last_refill_date, therapy.start_date) or therapy.reported_last_fill
    therapy.review_status = ReviewStatus.CONFIRMED
    therapy.confirmed_by_user_id, therapy.confirmed_at = cm.id, utcnow()
    _close_linked(db, cm, therapy_id=therapy.id, note="Medication confirmed.")
    _audit(db, cm, "medication_confirmed", patient.patient_id, therapy=therapy.id)
    _record_last_refill(db, cm, therapy, last)
    refresh_adherence(db, patient.patient_id)


def stop_medication(db: Session, cm: User, therapy: PatientTherapy) -> None:
    patient = db.get(Patient, therapy.patient_id)
    require_real(patient)
    therapy.review_status, therapy.status = ReviewStatus.STOPPED, "stopped"
    therapy.end_date = therapy.end_date or clock.get_today(db)
    _close_linked(db, cm, therapy_id=therapy.id, note="Medication stopped.")
    expire_recommendations(db, cm, therapy, "Medication stopped")
    _audit(db, cm, "medication_stopped", patient.patient_id, therapy=therapy.id)
    refresh_adherence(db, patient.patient_id)


def dismiss_medication(db: Session, cm: User, therapy: PatientTherapy, reason: str | None) -> None:
    """A reported medication that does not belong on the list (a duplicate, an entry error)."""
    patient = db.get(Patient, therapy.patient_id)
    require_real(patient)
    if therapy.review_status != ReviewStatus.REPORTED:
        raise AuthError(409, "not_reported", "Only a reported medication can be dismissed.")
    why = _text(reason, "reason", 300, required=True)
    therapy.review_status, therapy.status = ReviewStatus.DISMISSED, "dismissed"
    therapy.dismissed_reason = why
    _close_linked(db, cm, therapy_id=therapy.id, note=f"Not added: {why}")
    expire_recommendations(db, cm, therapy, "Medication dismissed")
    _audit(db, cm, "medication_dismissed", patient.patient_id, therapy=therapy.id)


def _patient_request(db: Session, patient: Patient, request_id: int | None) -> CareRequest | None:
    if request_id is None:
        return None
    request = db.get(CareRequest, request_id)
    if request is None or request.patient_id != patient.patient_id:
        raise AuthError(404, "not_found", "Request not found.")
    return request


def add_note(
    db: Session,
    cm: User,
    patient: Patient,
    *,
    kind: str,
    text: str,
    hcp_id: str | None,
    visible_to_patient: bool,
    request_id: int | None = None,
    due_date: str | None = None,
) -> CareNote | CareRequest:
    """An HCP instruction (a note the patient can read), or a follow-up, which is work: an
    open request owned by this care manager with a due date, in their queue until closed."""
    require_real(patient)
    if kind not in (CareNoteKind.HCP_INSTRUCTION, CareNoteKind.FOLLOW_UP):
        raise AuthError(422, "invalid_kind", "Choose instruction or follow-up.")
    if hcp_id is not None and db.get(PatientHcp, (patient.patient_id, hcp_id)) is None:
        raise AuthError(422, "not_on_care_team", "Choose an HCP on the patient's care team.")
    text = _text(text, "note", 1000, required=True)
    if kind == CareNoteKind.FOLLOW_UP:
        due = _date(due_date, "due_date", required=True)
        if due < clock.today():
            raise AuthError(422, "invalid_due_date", "The due date cannot be in the past.")
        follow_up = CareRequest(
            patient_id=patient.patient_id, type=CareRequestType.FOLLOW_UP,
            status=CareRequestStatus.OPEN, reason=text[:500], owner_user_id=cm.id,
            due_date=due, visible_to_patient=visible_to_patient, created_at=utcnow(),
            updated_at=utcnow(),
        )  # fmt: skip
        db.add(follow_up)
        db.flush()
        _audit(db, cm, "follow_up_scheduled", patient.patient_id, request=follow_up.id)
        return follow_up
    request = _patient_request(db, patient, request_id)
    note = CareNote(
        patient_id=patient.patient_id, author_user_id=cm.id, hcp_id=hcp_id, kind=kind,
        text=text, visible_to_patient=visible_to_patient, created_at=utcnow(),
        request_id=request.id if request else None,
    )  # fmt: skip
    db.add(note)
    db.flush()
    _audit(db, cm, "care_note_added", patient.patient_id, kind=kind)
    return note


# --- Routing to an HCP ------------------------------------------------------------------------


def _hcp_country(h: Hcp) -> str | None:
    """Synthetic HCPs are practices in US states; an invited HCP's location is not recorded."""
    return "US" if h.origin == hcp_records.SYNTHETIC and h.state else None


def hcp_options(db: Session, patient: Patient, condition_id: int | None, limit: int = 40):
    """HCPs with at least one specialty that suits the patient's condition (an HCP may hold
    several): those in the patient's country first, then specialist before primary care,
    HCPs who answer in the app before the rest, then those in the patient's state. An HCP
    without a configured specialty, or whose account is disabled, is never offered. Never
    assigns anyone by itself."""
    condition = db.get(PatientCondition, condition_id) if condition_id else None
    if condition is not None and condition.patient_id != patient.patient_id:
        raise AuthError(404, "not_found", "Condition not found.")
    code = condition.condition if condition else vocabulary.OTHER
    specialties = vocabulary.specialties_for(code)
    rank = func.min(case({s: n for n, s in enumerate(specialties)}, value=HcpSpecialty.specialty))
    disabled = select(User.hcp_id).where(
        User.hcp_id.is_not(None), User.status == AccountStatus.DISABLED
    )
    active_account = select(User.hcp_id).where(
        User.hcp_id.is_not(None), User.status == AccountStatus.ACTIVE
    )
    in_country = (
        case((Hcp.origin == hcp_records.SYNTHETIC, 0), else_=1)
        if patient.country == "US"
        else case((Hcp.origin == hcp_records.SYNTHETIC, 1), else_=0)
    )
    order = [in_country, rank, case((Hcp.hcp_id.in_(active_account), 0), else_=1)]
    if patient.state:
        order.append(case((Hcp.state == patient.state, 0), else_=1))
    rows = db.execute(
        select(Hcp, rank)
        .join(HcpSpecialty, HcpSpecialty.hcp_id == Hcp.hcp_id)
        .where(HcpSpecialty.specialty.in_(specialties), Hcp.hcp_id.not_in(disabled))
        .group_by(Hcp.hcp_id)
        .order_by(*order, Hcp.last_name, Hcp.hcp_id)
        .limit(limit)
    ).all()
    label = vocabulary.condition_label(code, condition.other_text if condition else None)
    items = []
    for h, best in rows:
        matched = vocabulary.specialty_label(specialties[best])
        country = _hcp_country(h)
        place = ", ".join(x for x in (h.city, h.state) if x)
        items.append(
            _hcp_brief(db, h)
            | {
                "origin": h.origin,
                "organization": h.organization,
                "country": country,
                "country_label": jurisdiction.label(country, None) if country else None,
                "location": (
                    f"{place}, {jurisdiction.label(country, None)}" if place and country else None
                ),
                "same_country": country is not None and country == patient.country,
                "reason": f"{matched} · {label}" if condition else matched,
            }
        )
    return {
        "condition": condition_out(condition) if condition else None,
        "specialties": [vocabulary.specialty_label(s) for s in specialties],
        "patient_country": patient.country,
        "patient_location": out.location_label(patient),
        # False when no suitable HCP is known to practise in the patient's country.
        "local_match": any(i["same_country"] for i in items),
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
    if not hcp_available(db, hcp.hcp_id):
        raise AuthError(409, "hcp_unavailable", "This healthcare professional is not available.")
    condition = db.get(PatientCondition, condition_id) if condition_id else None
    request = db.get(CareRequest, request_id) if request_id else None
    for item in (condition, request):
        if item is not None and item.patient_id != patient.patient_id:
            raise AuthError(404, "not_found", "Not found.")
    if request is not None and request.status == CareRequestStatus.CLOSED:
        raise AuthError(409, "request_closed", "This request is already closed.")
    if condition is not None and condition.status == ConditionStatus.DISMISSED:
        raise AuthError(409, "condition_not_added", "This condition was not added to the record.")
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
        request.handled_by_user_id = cm.id
        if request.type == CareRequestType.CONSULTATION:
            # Routing starts the consultation; it stays open until the HCP has answered and
            # the care manager has closed it.
            request.status = CareRequestStatus.AWAITING_HCP
            request.resolution = f"Routed to {out.hcp_name(hcp)}."
        elif not entry_unresolved(db, request):
            # A review request closes only once its entry is confirmed or dismissed.
            request.status = CareRequestStatus.CLOSED
            request.resolution = request.resolution or f"Routed to {out.hcp_name(hcp)}."
    db.flush()
    _audit(
        db, cm, "hcp_assigned", patient.patient_id, hcp=hcp.hcp_id, specialties=sorted(held),
        condition=condition.condition if condition else None,
    )  # fmt: skip


def respond_consultation(
    db: Session,
    actor: User,
    request: CareRequest,
    *,
    response: str,
    message: str | None,
    note_to_care_team: str | None,
) -> None:
    """The routed HCP answers (in the app, or through the care manager who records it):
    advice for the patient goes back to the care manager to close; a decline returns the
    consultation to the care manager to route again."""
    if (
        request.type != CareRequestType.CONSULTATION
        or request.status != CareRequestStatus.AWAITING_HCP
    ):
        raise AuthError(409, "not_awaiting_hcp", "This consultation is not waiting for a response.")
    hcp = db.get(Hcp, request.assigned_hcp_id)
    now = utcnow()
    if response == "advice":
        text = _text(message, "message", 1000, required=True)
        db.add(
            CareNote(
                patient_id=request.patient_id, author_user_id=actor.id, hcp_id=hcp.hcp_id,
                kind=CareNoteKind.HCP_INSTRUCTION, text=text, visible_to_patient=True,
                request_id=request.id, created_at=now,
            )
        )  # fmt: skip
        internal = _text(note_to_care_team, "note", 1000)
        if internal:
            db.add(
                CareNote(
                    patient_id=request.patient_id, author_user_id=actor.id, hcp_id=hcp.hcp_id,
                    kind=CareNoteKind.HCP_INSTRUCTION, text=internal, visible_to_patient=False,
                    request_id=request.id, created_at=now,
                )
            )  # fmt: skip
        request.status = CareRequestStatus.HCP_RESPONDED
        request.resolution = f"{out.hcp_name(hcp)} responded."
    elif response == "decline":
        why = _text(note_to_care_team or message, "reason", 500, required=True)
        request.status = CareRequestStatus.OPEN
        request.resolution = f"{out.hcp_name(hcp)} could not take this consultation: {why}"
        request.assigned_hcp_id = None
    else:
        raise AuthError(422, "invalid_response", "Choose advice or decline.")
    db.flush()
    _audit(
        db, actor, f"consultation_{'answered' if response == 'advice' else 'declined'}",
        request.patient_id, request=request.id, hcp=hcp.hcp_id,
    )  # fmt: skip


def return_consultations(db: Session, actor: User, hcp_id: str) -> int:
    """An HCP is no longer available: their waiting consultations go back to the care
    manager to route again, with the reason the patient can read."""
    count = 0
    hcp = db.get(Hcp, hcp_id)
    for r in db.scalars(
        select(CareRequest).where(
            CareRequest.assigned_hcp_id == hcp_id,
            CareRequest.status == CareRequestStatus.AWAITING_HCP,
        )
    ):
        r.status, r.assigned_hcp_id = CareRequestStatus.OPEN, None
        r.resolution = (
            f"{out.hcp_name(hcp)} is no longer available; your care manager will arrange another."
        )
        _audit(db, actor, "consultation_returned", r.patient_id, request=r.id, hcp=hcp_id)
        count += 1
    return count


# Statuses a care manager may set by hand. Routing sets "awaiting HCP"; the HCP's answer sets
# "HCP responded".
MANUAL_STATUSES = (CareRequestStatus.OPEN, CareRequestStatus.IN_PROGRESS, CareRequestStatus.CLOSED)


def entry_unresolved(db: Session, request: CareRequest) -> bool:
    """A review request whose condition or medication is still only reported: closing it
    would leave the entry outside every queue."""
    if request.type == CareRequestType.CONDITION_REVIEW and request.condition_id:
        entry = db.get(PatientCondition, request.condition_id)
        return entry is not None and entry.status == ConditionStatus.REPORTED
    if request.type == CareRequestType.MEDICATION_REVIEW and request.therapy_id:
        entry = db.get(PatientTherapy, request.therapy_id)
        return entry is not None and entry.review_status == ReviewStatus.REPORTED
    return False


WITHDRAWN = "Withdrawn by the care manager: "


def update_request(
    db: Session, cm: User, request: CareRequest, status: str, resolution: str | None
) -> None:
    if status not in MANUAL_STATUSES:
        raise AuthError(422, "invalid_status", "Choose a valid status.")
    if request.status == CareRequestStatus.CLOSED:
        raise AuthError(409, "request_closed", "This request is already closed.")
    if request.status == CareRequestStatus.AWAITING_HCP and status != CareRequestStatus.CLOSED:
        raise AuthError(
            409, "awaiting_hcp", "This consultation is with the healthcare professional."
        )
    if status == CareRequestStatus.CLOSED:
        if entry_unresolved(db, request):
            what = "condition" if request.type == CareRequestType.CONDITION_REVIEW else "medication"
            raise AuthError(
                409,
                "entry_unresolved",
                f"The reported {what} is still waiting for review. Confirm or dismiss it; "
                "that closes this request.",
            )
        # Closing always says what was done: the patient reads it as the outcome.
        outcome = _text(resolution, "outcome", 500, required=True)
        if request.status == CareRequestStatus.AWAITING_HCP:
            # Taken back from the HCP before they answered: said plainly to the HCP and the
            # patient, never a silent disappearance.
            request.resolution = WITHDRAWN + outcome
            _audit(
                db, cm, "consultation_withdrawn", request.patient_id, request=request.id,
                hcp=request.assigned_hcp_id,
            )  # fmt: skip
        else:
            request.resolution = outcome
    elif resolution is not None:
        request.resolution = _text(resolution, "resolution", 500)
    request.status = status
    request.handled_by_user_id = cm.id
    request.updated_at = utcnow()
    _audit(db, cm, "care_request_updated", request.patient_id, request=request.id, status=status)


def requests_for(db: Session, patient_filter, status: str | None, type_: str | None) -> list:
    """A care manager's queue. "active" = everything not closed. Work that needs the care
    manager comes first (overdue follow-ups, HCP answers, new requests), then the rest."""
    query = select(CareRequest).where(patient_filter(CareRequest.patient_id))
    if status == "active":
        query = query.where(CareRequest.status != CareRequestStatus.CLOSED)
    elif status:
        query = query.where(CareRequest.status == status)
    if type_:
        query = query.where(CareRequest.type == type_)
    today = clock.today()
    urgency = case(
        (and_(CareRequest.due_date.is_not(None), CareRequest.due_date <= today), 0),
        (CareRequest.status == CareRequestStatus.HCP_RESPONDED, 1),
        (CareRequest.status == CareRequestStatus.OPEN, 2),
        (CareRequest.status == CareRequestStatus.IN_PROGRESS, 3),
        (CareRequest.status == CareRequestStatus.AWAITING_HCP, 4),
        else_=5,
    )
    return db.scalars(query.order_by(urgency, CareRequest.id.desc()).limit(300)).all()


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
