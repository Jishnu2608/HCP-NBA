"""Portal endpoints: what an HCP or a patient may see and change about themselves.

Nothing here takes an id from the request. The record is always the one bound to the
signed-in user, so there is no id to tamper with.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.api import serializers as out
from app.api.deps import require_permission
from app.api.people import consent_out
from app.api.schemas import StrictBody
from app.clinical import care, vocabulary
from app.clinical import hcps as hcp_records
from app.core import clock, rbac
from app.core.db import get_db
from app.core.permissions import Permission
from app.engagement import delivery, simulator
from app.models import (
    Consent,
    Content,
    Hcp,
    Interaction,
    MessageDraft,
    Patient,
    PatientHcp,
    PatientTherapy,
    SpecialtyChangeRequest,
    User,
)
from app.models.enums import Channel, ConsentPurpose, FillSource, Outcome, ReviewStatus, TargetType

router = APIRouter(prefix="/api/me", tags=["portal"])

profile_readers = require_permission(Permission.SELF_PROFILE_READ)
consent_managers = require_permission(Permission.SELF_CONSENT_MANAGE)
panel_readers = require_permission(Permission.SELF_PATIENTS_READ)
portal_users = require_permission(Permission.SELF_INBOX)
health_managers = require_permission(Permission.SELF_HEALTH_MANAGE)
specialty_requesters = require_permission(Permission.SELF_SPECIALTY_REQUEST)


def no_assignment() -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT,
        {
            "code": "no_assignment",
            "message": "No record is linked to this account yet. Ask an administrator.",
        },
    )


def own_patient_id(user: User) -> str:
    if user.patient_id is None:
        raise no_assignment()
    return user.patient_id


def own_hcp_id(user: User) -> str:
    if user.hcp_id is None:
        raise no_assignment()
    return user.hcp_id


OUTREACH_CHANNELS = (Channel.SMS, Channel.EMAIL, Channel.PORTAL, Channel.PHONE)


class ConsentChange(StrictBody):
    granted: bool


@router.get("/profile")
def my_profile(user: User = Depends(profile_readers), db: Session = Depends(get_db)) -> dict:
    today = clock.get_today(db)
    if user.hcp_id is None and user.patient_id is None:
        raise no_assignment()
    if user.hcp_id is not None:
        h = db.get(Hcp, user.hcp_id)
        # Commercial fields (segment, value score, prescribing volume) are not shown to the HCP.
        return {
            "kind": "hcp",
            "hcp_id": h.hcp_id,
            "name": out.hcp_name(h),
            "npi": h.npi,
            # Set by an administrator; empty means "Specialty not configured".
            "specialties": hcp_records.specialty_out(db, h.hcp_id),
            "origin": h.origin,
            "organization": h.organization,
            "city": h.city,
            "state": h.state,
        }
    p = db.get(Patient, user.patient_id)
    therapies = db.scalars(
        select(PatientTherapy).where(
            PatientTherapy.patient_id == p.patient_id,
            PatientTherapy.review_status == ReviewStatus.CONFIRMED,
        )
    ).all()
    care_team = db.execute(
        select(Hcp, PatientHcp.is_primary)
        .join(PatientHcp, PatientHcp.hcp_id == Hcp.hcp_id)
        .where(PatientHcp.patient_id == p.patient_id)
    ).all()
    return {
        "kind": "patient",
        "patient_id": p.patient_id,
        "name": out.patient_name(p),
        "plan_type": p.plan_type,
        "preferred_channel": p.preferred_channel,
        **out.patient_brief(p),
        "as_of_date": today,
        # Internal risk scores and model outputs are not shown to the patient.
        "therapies": [out.therapy_out(db, t, today, with_risk=False) for t in therapies],
        "care_team": [
            {
                "name": out.hcp_name(h),
                "specialties": hcp_records.specialty_out(db, h.hcp_id),
                "is_primary": primary,
            }
            for h, primary in care_team
        ],
    }


def _current_consents(db: Session, patient_id: str, today) -> dict[tuple, Consent]:
    """The record in effect today for each (purpose, channel)."""
    current: dict[tuple, Consent] = {}
    for c in db.scalars(
        select(Consent).where(Consent.patient_id == patient_id).order_by(Consent.effective_from)
    ):
        if c.effective_from <= today and (c.effective_to is None or today < c.effective_to):
            current[(c.purpose, c.channel)] = c
    return current


@router.get("/consents")
def my_consents(
    user: User = Depends(consent_managers), db: Session = Depends(get_db)
) -> list[dict]:
    today = clock.get_today(db)
    current = _current_consents(db, own_patient_id(user), today)
    keys = [(ConsentPurpose.OUTREACH, ch) for ch in OUTREACH_CHANNELS]
    keys.append((ConsentPurpose.PROVIDER_SHARING, None))
    return [
        consent_out(current[k], today)
        if k in current
        else {"purpose": k[0], "channel": k[1], "granted": False, "in_effect": False}
        for k in keys
    ]


def _change_consent(db: Session, user: User, purpose: str, channel: str | None, granted: bool):
    today = clock.get_today(db)
    existing = _current_consents(db, own_patient_id(user), today).get((purpose, channel))
    if existing and existing.granted == granted:
        return consent_out(existing, today)
    # Consent history is kept: close the old record and open a new one from today.
    if existing:
        if existing.effective_from == today:
            db.delete(existing)
        else:
            existing.effective_to = today
    record = Consent(
        patient_id=user.patient_id,
        purpose=purpose,
        channel=channel,
        granted=granted,
        effective_from=today,
        source="patient_portal",
    )
    db.add(record)
    audit.record(
        db,
        "consent_granted" if granted else "consent_withdrawn",
        "patient",
        user.patient_id,
        actor=user.username,
        actor_role=user.role,
        consent_ok=granted,
        detail={"purpose": purpose, "channel": channel},
    )
    db.commit()
    return consent_out(record, today)


@router.put("/consents/outreach/{channel}")
def set_outreach_consent(
    channel: str,
    body: ConsentChange,
    user: User = Depends(consent_managers),
    db: Session = Depends(get_db),
) -> dict:
    if channel not in OUTREACH_CHANNELS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown channel")
    return _change_consent(db, user, ConsentPurpose.OUTREACH, channel, body.granted)


@router.put("/consents/provider-sharing")
def set_sharing_consent(
    body: ConsentChange, user: User = Depends(consent_managers), db: Session = Depends(get_db)
) -> dict:
    return _change_consent(db, user, ConsentPurpose.PROVIDER_SHARING, None, body.granted)


@router.get("/patients")
def my_patients(user: User = Depends(panel_readers), db: Session = Depends(get_db)) -> list[dict]:
    """Adherence summary for this HCP's patients who consent to sharing with their provider."""
    today = clock.get_today(db)
    result = []
    for pid in rbac.shared_patient_ids(db, own_hcp_id(user), today):
        p = db.get(Patient, pid)
        therapies = db.scalars(
            select(PatientTherapy).where(
                PatientTherapy.patient_id == pid, PatientTherapy.prescriber_hcp_id == user.hcp_id
            )
        ).all()
        if therapies:
            result.append(
                {
                    "patient_id": pid,
                    "name": out.patient_name(p),
                    "therapies": [
                        out.therapy_out(db, t, today, with_risk=False) for t in therapies
                    ],
                }
            )
    return result


# Messages a person would actually receive. Calls and visits are not inbox items.
INBOX_CHANNELS = (Channel.PORTAL, Channel.EMAIL, Channel.SMS)


class InboxResponse(StrictBody):
    response: Literal["opened", "clicked", "refill"]


def _me(user: User) -> tuple[str, str]:
    """The single record this account is linked to. Never taken from the request."""
    if user.patient_id is not None:
        return TargetType.PATIENT, user.patient_id
    return TargetType.HCP, own_hcp_id(user)


def _inbox_item(db: Session, i: Interaction) -> dict:
    draft = db.scalar(
        select(MessageDraft).where(MessageDraft.nba_id == i.nba_id, MessageDraft.is_selected)
    )
    content = db.get(Content, i.content_id) if i.content_id else None
    return {
        "id": i.id,
        "received": i.int_ts,
        "channel": i.channel,
        # A text message has no subject line; never show the internal content title instead.
        "subject": draft.subject if draft else (content.title if content else None),
        "body": draft.body if draft else (content.body if content else ""),
        "status": i.outcome,
        "responded": i.outcome_ts,
        "can_refill": i.therapy_id is not None and i.outcome != Outcome.FILLED,
    }


@router.get("/inbox")
def my_inbox(user: User = Depends(portal_users), db: Session = Depends(get_db)) -> list[dict]:
    """Messages sent to me through the engine, newest first."""
    target_type, target_id = _me(user)
    rows = db.scalars(
        select(Interaction)
        .where(
            Interaction.target_type == target_type,
            Interaction.target_id == target_id,
            Interaction.source == "nba",
            Interaction.channel.in_(INBOX_CHANNELS),
        )
        .order_by(Interaction.int_ts.desc(), Interaction.id.desc())
    )
    return [_inbox_item(db, i) for i in rows]


@router.post("/inbox/{interaction_id}/respond")
def respond(
    interaction_id: int,
    body: InboxResponse,
    user: User = Depends(portal_users),
    db: Session = Depends(get_db),
) -> dict:
    """The recipient's own reaction: the response signal the engine learns from."""
    target_type, target_id = _me(user)
    i = db.get(Interaction, interaction_id)
    if (
        i is None
        or (i.target_type, i.target_id) != (target_type, target_id)
        or i.source != "nba"
        or i.channel not in INBOX_CHANNELS
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    when = delivery.now_on(db)
    if body.response == "refill":
        if target_type != TargetType.PATIENT or i.therapy_id is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "This message has no refill action")
        if i.outcome == Outcome.FILLED:
            raise HTTPException(status.HTTP_409_CONFLICT, "Refill already recorded")
        simulator.apply_fill(db, db.get(PatientTherapy, i.therapy_id), when, FillSource.PATIENT)
        outcome = Outcome.FILLED
    else:
        # A weaker signal never overwrites a stronger one already captured.
        order = [Outcome.PENDING, Outcome.NO_RESPONSE, Outcome.OPENED, Outcome.CLICKED]
        if i.outcome not in order or order.index(body.response) <= order.index(i.outcome):
            return _inbox_item(db, i)
        outcome = body.response
    delivery.capture_response(db, i, outcome, when, actor=user.username, actor_role=user.role)
    db.commit()
    return _inbox_item(db, i)


# --- The patient's own health profile ----------------------------------------------------


class ConditionReport(StrictBody):
    condition: str = Field(max_length=32)
    other_text: str | None = Field(default=None, max_length=120)


class MedicationReport(StrictBody):
    name: str = Field(max_length=64)
    dose_instructions: str | None = Field(default=None, max_length=300)
    schedule: str | None = Field(default=None, max_length=64)
    start_date: str = Field(max_length=10)
    end_date: str | None = Field(default=None, max_length=10)
    # Explicit: true = still being taken (no end date); false = an end date is required.
    ongoing: bool


class RefillReport(StrictBody):
    fill_date: str | None = Field(default=None, max_length=10)


class ConsultationRequest(StrictBody):
    reason: str = Field(max_length=500)


def _own_patient(db: Session, user: User) -> Patient:
    return db.get(Patient, own_patient_id(user))


def _health(db: Session, user: User) -> dict:
    return care.health_record(db, _own_patient(db, user), for_patient=True)


@router.get("/health")
def my_health(user: User = Depends(health_managers), db: Session = Depends(get_db)) -> dict:
    """Conditions, medications, care requests, instructions and care team. Nothing here is
    inferred: every entry was made by the patient or their care team."""
    return _health(db, user)


@router.post("/conditions", status_code=201)
def report_condition(
    body: ConditionReport, user: User = Depends(health_managers), db: Session = Depends(get_db)
) -> dict:
    care.report_condition(db, user, _own_patient(db, user), body.condition, body.other_text)
    db.commit()
    return _health(db, user)


@router.post("/medications", status_code=201)
def report_medication(
    body: MedicationReport, user: User = Depends(health_managers), db: Session = Depends(get_db)
) -> dict:
    care.report_medication(db, user, _own_patient(db, user), **body.model_dump())
    db.commit()
    return _health(db, user)


@router.post("/medications/{therapy_id}/refill", status_code=201)
def log_refill(
    therapy_id: int,
    body: RefillReport,
    user: User = Depends(health_managers),
    db: Session = Depends(get_db),
) -> dict:
    therapy = db.get(PatientTherapy, therapy_id)
    if therapy is None or therapy.patient_id != own_patient_id(user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Medication not found")
    care.require_real(_own_patient(db, user))
    care.log_refill(db, user, therapy, body.fill_date, FillSource.PATIENT)
    db.commit()
    return _health(db, user)


@router.post("/care-requests", status_code=201)
def request_consultation(
    body: ConsultationRequest, user: User = Depends(health_managers), db: Session = Depends(get_db)
) -> dict:
    """Consult an HCP: the request goes to the patient's care manager, who routes it."""
    care.request_consultation(db, user, _own_patient(db, user), body.reason)
    db.commit()
    return _health(db, user)


# --- An HCP's specialty change requests --------------------------------------------------


class SpecialtyRequest(StrictBody):
    action: Literal["add", "remove", "replace"]
    specialties: list[str] = Field(max_length=10)
    notes: str | None = Field(default=None, max_length=500)


@router.get("/specialty-requests")
def my_specialty_requests(
    user: User = Depends(specialty_requesters), db: Session = Depends(get_db)
) -> dict:
    """Current specialties (set by an administrator) and this HCP's requests."""
    rows = db.scalars(
        select(SpecialtyChangeRequest)
        .where(SpecialtyChangeRequest.requested_by_user_id == user.id)
        .order_by(SpecialtyChangeRequest.id.desc())
    )
    return {
        "specialties": hcp_records.specialty_out(db, own_hcp_id(user)),
        "options": vocabulary.specialty_options(),
        "requests": [hcp_records.request_out(db, r) for r in rows],
    }


@router.post("/specialty-requests", status_code=201)
def request_specialty_change(
    body: SpecialtyRequest,
    user: User = Depends(specialty_requesters),
    db: Session = Depends(get_db),
) -> dict:
    """Asks an administrator to change this HCP's specialties. Changes nothing by itself."""
    own_hcp_id(user)
    row = hcp_records.submit_request(db, user, body.action, body.specialties, body.notes)
    db.commit()
    return hcp_records.request_out(db, row)
