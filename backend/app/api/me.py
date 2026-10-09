"""Portal endpoints: what an HCP or a patient may see and change about themselves.

Nothing here takes an id from the request. The record is always the one bound to the
signed-in user, so there is no id to tamper with.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import audit
from app.api import serializers as out
from app.api.deps import get_current_user, require_permission
from app.api.people import consent_out
from app.api.schemas import StrictBody
from app.clinical import activity, care, records, vocabulary, workload
from app.clinical import hcps as hcp_records
from app.core import clock, rbac
from app.core.db import get_db
from app.core.permissions import Permission
from app.engagement import delivery
from app.models import (
    CareNote,
    CareRequest,
    Consent,
    Content,
    ContentMessage,
    Hcp,
    Interaction,
    MessageDraft,
    Nba,
    Patient,
    PatientCondition,
    PatientHcp,
    PatientTherapy,
    PrivacyRequest,
    SpecialtyChangeRequest,
    User,
)
from app.models.enums import (
    CareNoteKind,
    CareRequestStatus,
    CareRequestType,
    Channel,
    ConditionStatus,
    ConsentPurpose,
    FillSource,
    MlrStatus,
    Outcome,
    ReviewStatus,
    TargetType,
)
from app.models.tables import utcnow

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
            "country": h.country,
            # From the practice (synthetic) or the residence the HCP gave when joining.
            "location": out.hcp_location_label(h),
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
    # Consent history is kept: close the old record and open a new one from today. A record
    # that began today is closed today too (a zero-length period): it stays in the history.
    if existing:
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
    activity.touch(db, user.patient_id)
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
    """Adherence summary of the patients whose care team this HCP is on (`patient_hcp`) and
    who currently consent to sharing with their doctors: every confirmed medication, whoever
    prescribed it. Being on the care team, not a prescriber field, is the relationship."""
    today = clock.get_today(db)
    result = []
    for pid in rbac.shared_patient_ids(db, own_hcp_id(user), today):
        p = db.get(Patient, pid)
        therapies = db.scalars(
            select(PatientTherapy).where(
                PatientTherapy.patient_id == pid,
                PatientTherapy.review_status == ReviewStatus.CONFIRMED,
            )
        ).all()
        result.append(
            {
                "patient_id": pid,
                "name": out.patient_name(p),
                "therapies": [
                    out.therapy_out(db, t, today, with_risk=False)
                    | {"prescribed_by_you": t.prescriber_hcp_id == user.hcp_id}
                    for t in therapies
                ],
            }
        )
    return result


# Messages a person would actually receive. Calls and visits are not inbox items.
INBOX_CHANNELS = (Channel.PORTAL, Channel.EMAIL, Channel.SMS)
# Delivery is simulated in this deployment: an email or text is shown here, never sent out.
CHANNEL_NOTE = {
    Channel.EMAIL: "Email (copy shown here; not sent outside this demo)",
    Channel.SMS: "Text message (copy shown here; not sent outside this demo)",
    Channel.PORTAL: "Portal message",
}


def _therapy_active(db: Session, therapy_id: int) -> bool:
    therapy = db.get(PatientTherapy, therapy_id)
    return therapy is not None and therapy.review_status == ReviewStatus.CONFIRMED


class InboxResponse(StrictBody):
    response: Literal["opened", "clicked", "refill"]


def _me(user: User) -> tuple[str, str]:
    """The single record this account is linked to. Never taken from the request."""
    if user.patient_id is not None:
        return TargetType.PATIENT, user.patient_id
    return TargetType.HCP, own_hcp_id(user)


def _inbox_item(db: Session, i: Interaction) -> dict:
    # The wording actually delivered (recorded at send); older sends fall back to the selection.
    draft = db.get(MessageDraft, i.draft_id) if i.draft_id else None
    draft = draft or db.scalar(
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
        "can_refill": i.therapy_id is not None
        and i.outcome != Outcome.FILLED
        and _therapy_active(db, i.therapy_id),
        "channel_note": CHANNEL_NOTE.get(i.channel),
        "content_notice": _content_notice(content),
        "product": content.product if content else None,
        # The HCP's own answer to the material (interested, request a meeting, ...).
        "intent": i.intent,
    } | _hcp_source(db, i, content)


def _content_notice(content: Content | None) -> str | None:
    """Material withdrawn or replaced after it was delivered stays readable, marked as such."""
    if content is None:
        return None
    if content.mlr_status == MlrStatus.WITHDRAWN:
        why = f" Reason: {content.withdrawn_reason}" if content.withdrawn_reason else ""
        return f"Withdrawn by the medical, legal and regulatory review: no longer current.{why}"
    if content.mlr_status == MlrStatus.SUPERSEDED:
        return "A newer approved version has replaced this material."
    return None


def _hcp_source(db: Session, i: Interaction, content: Content | None) -> dict:
    """An HCP sees which approved content an item is and who sent it (their representative)."""
    if i.target_type != TargetType.HCP:
        return {}
    nba = db.get(Nba, i.nba_id) if i.nba_id else None
    sender = db.get(User, nba.reviewed_by_user_id) if nba and nba.reviewed_by_user_id else None
    thread = list(
        db.scalars(
            select(ContentMessage)
            .where(ContentMessage.interaction_id == i.id, ContentMessage.hcp_id == i.target_id)
            .order_by(ContentMessage.id)
        )
    )
    return {
        "content_title": content.title if content else None,
        "content_version": content.version if content else None,
        "sent_by": sender.display_name if sender else None,
        "conversation": [
            {
                "id": m.id,
                "author": "You"
                if m.author_role == "hcp"
                else "Medical, legal and regulatory review"
                if m.author_role == "compliance"
                else (sender.display_name if sender else "Your representative"),
                "body": m.body,
                "ts": m.ts,
            }
            for m in thread
        ],
        "can_ask": content is not None,
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
    items = [_inbox_item(db, i) for i in rows]
    if user.hcp_id:
        # Replies about delivered material count as read once the inbox shows them.
        from app.content import governance

        governance.mark_read(
            db,
            user,
            list(db.scalars(select(ContentMessage).where(ContentMessage.hcp_id == user.hcp_id))),
        )
        db.commit()
    return items


class IntentBody(StrictBody):
    intent: Literal[
        "interested", "request_meeting", "need_info", "need_evidence", "not_now", "decline"
    ]
    note: str | None = Field(default=None, max_length=500)


@router.post("/inbox/{interaction_id}/intent")
def answer_content(
    interaction_id: int,
    body: IntentBody,
    user: User = Depends(require_permission(Permission.SELF_INBOX)),
    db: Session = Depends(get_db),
) -> dict:
    """An HCP's answer to material delivered to them. It reaches the representatives
    currently assigned to them as work (a request, or a follow-up for "not now"), and the
    engine reads it as a signal; declining stops that material being proposed again."""
    from app.commercial import tasks as hcp_tasks
    from app.engagement import delivery

    if not user.hcp_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    i = db.get(Interaction, interaction_id)
    if (
        i is None
        or i.target_type != TargetType.HCP
        or i.target_id != user.hcp_id
        or i.source != "nba"
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if i.intent is not None and i.intent == body.intent:
        return _inbox_item(db, i)
    outcome = Outcome.DECLINED if body.intent == "decline" else Outcome.REPLIED
    delivery.capture_response(
        db,
        i,
        outcome,
        delivery.now_on(db),
        actor=user.username,
        actor_role=user.role,
        note=body.note,
    )
    hcp_tasks.from_intent(db, user, i, body.intent, body.note)
    db.commit()
    return _inbox_item(db, i)


class QuestionBody(StrictBody):
    body: str = Field(min_length=3, max_length=2000)


@router.post("/inbox/{interaction_id}/question", status_code=201)
def ask_about_content(
    interaction_id: int,
    body: QuestionBody,
    user: User = Depends(require_permission(Permission.SELF_INBOX)),
    db: Session = Depends(get_db),
) -> dict:
    """An HCP's question or concern about material delivered to them. It reaches the MLR
    reviewers (who see the HCP without identity) and the representative who sent it."""
    from app.content import governance

    if not user.hcp_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    i = db.get(Interaction, interaction_id)
    if (
        i is None
        or i.target_type != TargetType.HCP
        or i.target_id != user.hcp_id
        or i.source != "nba"
        or i.content_id is None
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    content = db.get(Content, i.content_id)
    governance.post_message(db, user, content, body.body, hcp_id=user.hcp_id, interaction_id=i.id)
    db.commit()
    return _inbox_item(db, i)


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
        # The same checks as "I refilled today": confirmed, active, once per day. A refill
        # already logged today answers the message without counting the supply twice.
        care.log_refill(db, user, db.get(PatientTherapy, i.therapy_id), None, FillSource.PATIENT)
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
    # When a supply was last collected, if known: the care team confirms it.
    last_refill_date: str | None = Field(default=None, max_length=10)


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
    care.require_new_refill(care.log_refill(db, user, therapy, body.fill_date, FillSource.PATIENT))
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
    ).all()
    # Decisions shown here for the first time are marked new, and seen from now on (they
    # leave the menu count).
    fresh = {r.id for r in rows if r.decided_at is not None and r.seen_at is None}
    for r in rows:
        if r.id in fresh:
            r.seen_at = utcnow()
    if fresh:
        db.commit()
    return {
        "specialties": hcp_records.specialty_out(db, own_hcp_id(user)),
        "options": vocabulary.specialty_options(),
        "requests": [
            hcp_records.request_out(db, r) | {"new_decision": r.id in fresh} for r in rows
        ],
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


# --- Consultations routed to an HCP ------------------------------------------------------


consultation_handlers = require_permission(Permission.SELF_CONSULTATIONS_MANAGE)


# A consultation is the HCP's work, and its clinical context theirs to see, only while it is
# with them or waiting for the care manager to close it after their answer.
WITH_HCP = (CareRequestStatus.AWAITING_HCP, CareRequestStatus.HCP_RESPONDED)


def _consultation_out(db: Session, r: CareRequest, hcp_id: str) -> dict:
    """What the routed HCP needs to answer. The patient asked for this consultation, so its
    clinical details (confirmed conditions, current medications and adherence) are shared
    with the HCP while it is open, whatever the provider-sharing setting. Once it is closed,
    or after the HCP declined it, only the request itself, the HCP's own notes and the
    outcome remain: continuing access to clinical data needs the patient's sharing consent
    (My patients)."""
    today = clock.get_today(db)
    p = db.get(Patient, r.patient_id)
    current = r.status in WITH_HCP and r.assigned_hcp_id == hcp_id
    row = care.request_out(db, r, for_patient=False)
    # Only this HCP's own notes (another HCP's decline reason is not theirs to read).
    row["notes"] = [n for n in row["notes"] if n.get("hcp_id") == hcp_id]
    managers = [u.display_name for u in records.responsible_care_managers(db, r.patient_id)]
    declined = care.declines(db, r.id).get(hcp_id) if r.assigned_hcp_id != hcp_id else None
    row |= {
        "patient_age": int((today - p.birth_date).days / 365.25),
        "patient_location": out.location_label(p),
        "care_managers": managers,
        "context_available": current,
        "declined_by_you": declined is not None,
        "group": (
            "waiting"
            if current and r.status == CareRequestStatus.AWAITING_HCP
            else "with_care_manager"
            if current
            else "history"
        ),
        "conditions": [],
        "medications": [],
    }
    if declined is not None:
        row |= {"status_label": "You declined this consultation", "assigned_hcp": None}
    if current:
        therapies = db.scalars(
            select(PatientTherapy).where(
                PatientTherapy.patient_id == r.patient_id,
                PatientTherapy.review_status == ReviewStatus.CONFIRMED,
            )
        ).all()
        row["conditions"] = [
            care.condition_out(c)
            for c in db.scalars(
                select(PatientCondition).where(
                    PatientCondition.patient_id == r.patient_id,
                    PatientCondition.status == ConditionStatus.CONFIRMED,
                )
            )
        ]
        row["medications"] = [
            out.therapy_out(db, t, today, with_risk=False) | {"drug_name": t.drug_name}
            for t in therapies
        ]
    return row


def _routed_to_me(db: Session, user: User, request_id: int) -> CareRequest:
    r = db.get(CareRequest, request_id)
    if (
        r is None
        or r.type != CareRequestType.CONSULTATION
        or r.assigned_hcp_id is None
        or r.assigned_hcp_id != own_hcp_id(user)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Consultation not found")
    return r


@router.get("/consultations")
def my_consultations(
    user: User = Depends(consultation_handlers), db: Session = Depends(get_db)
) -> dict:
    """Consultations care managers routed to this HCP, in three groups: waiting for them,
    answered and with the care manager, and history (closed, withdrawn or declined by them).
    Clinical details are included only for the first two."""
    hcp_id = own_hcp_id(user)
    declined_ids = select(CareNote.request_id).where(
        CareNote.hcp_id == hcp_id, CareNote.kind == CareNoteKind.HCP_DECLINE
    )
    rows = db.scalars(
        select(CareRequest)
        .where(
            CareRequest.type == CareRequestType.CONSULTATION,
            or_(CareRequest.assigned_hcp_id == hcp_id, CareRequest.id.in_(declined_ids)),
        )
        .order_by(CareRequest.updated_at.desc(), CareRequest.id.desc())
    ).all()
    items = [_consultation_out(db, r, hcp_id) for r in rows]
    order = {"waiting": 0, "with_care_manager": 1, "history": 2}
    items.sort(key=lambda i: order[i["group"]])
    return {
        "waiting": sum(1 for i in items if i["group"] == "waiting"),
        "with_care_manager": sum(1 for i in items if i["group"] == "with_care_manager"),
        "history": sum(1 for i in items if i["group"] == "history"),
        "items": items,
    }


class ConsultationAnswer(StrictBody):
    response: Literal["advice", "decline"]
    # Advice for the patient (shown to them) or, when declining, the reason.
    message: str | None = Field(default=None, max_length=1000)
    note_to_care_team: str | None = Field(default=None, max_length=1000)


@router.post("/consultations/{request_id}/respond")
def answer_consultation(
    request_id: int,
    body: ConsultationAnswer,
    user: User = Depends(consultation_handlers),
    db: Session = Depends(get_db),
) -> dict:
    r = _routed_to_me(db, user, request_id)
    care.respond_consultation(
        db, user, r, response=body.response, message=body.message,
        note_to_care_team=body.note_to_care_team,
    )  # fmt: skip
    db.commit()
    return _consultation_out(db, r, own_hcp_id(user))


# --- What needs this person's attention ---------------------------------------------------


@router.get("/attention")
def attention(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Counts for the menu: work waiting for this account, by what it may do. A role learns
    that something arrived without having to know which page to open."""
    from app.core.permissions import can

    counts: dict[str, int] = {}
    if can(user, Permission.PATIENT_CARE_MANAGE):
        # The same definitions the Care requests page and the queue show (clinical.workload).
        counts["care_requests"] = workload.care_requests(db, user)["needs_you"]
        counts["outreach"] = sum(workload.outreach(db, user).values())
    if can(user, Permission.SELF_SPECIALTY_REQUEST):
        # Decisions on this HCP's specialty requests they have not looked at yet.
        counts["specialty_decisions"] = db.scalar(
            select(func.count())
            .select_from(SpecialtyChangeRequest)
            .where(
                SpecialtyChangeRequest.requested_by_user_id == user.id,
                SpecialtyChangeRequest.decided_at.is_not(None),
                SpecialtyChangeRequest.seen_at.is_(None),
            )
        )
    if can(user, Permission.SELF_CONSULTATIONS_MANAGE) and user.hcp_id:
        counts["consultations"] = db.scalar(
            select(func.count())
            .select_from(CareRequest)
            .where(
                CareRequest.assigned_hcp_id == user.hcp_id,
                CareRequest.status == CareRequestStatus.AWAITING_HCP,
            )
        )
    if can(user, Permission.SELF_INBOX) and (user.patient_id or user.hcp_id):
        target_type, target_id = _me(user)
        # A patient's messages; for an HCP, what their representative delivered (the
        # "Medical representative" tab), kept apart from consultations (the inbox).
        key = (
            "rep_messages"
            if user.hcp_id and can(user, Permission.SELF_COMMERCIAL_INBOX)
            else "messages"
        )
        counts[key] = db.scalar(
            select(func.count())
            .select_from(Interaction)
            .where(
                Interaction.target_type == target_type,
                Interaction.target_id == target_id,
                Interaction.source == "nba",
                Interaction.channel.in_(INBOX_CHANNELS),
                Interaction.outcome.in_((Outcome.PENDING, Outcome.NO_RESPONSE)),
            )
        )
    if (
        can(user, Permission.CONTENT_APPROVE)
        or can(user, Permission.CONTENT_PROPOSE)
        or user.hcp_id
    ):
        from app.content import governance
        from app.models import ContentReview

        unread = db.scalar(select(func.count()).select_from(governance.unread(db, user).subquery()))
        if can(user, Permission.CONTENT_APPROVE):
            # Submissions waiting for a decision, and new messages from authors or HCPs.
            pending = db.scalar(
                select(func.count())
                .select_from(Content)
                .where(Content.mlr_status == MlrStatus.PENDING)
            )
            counts["content"] = pending + unread
        elif can(user, Permission.CONTENT_PROPOSE):
            # MLR decisions on my material I have not opened yet, and new messages.
            unseen = db.scalar(
                select(func.count())
                .select_from(ContentReview)
                .join(Content, Content.content_id == ContentReview.content_id)
                .where(
                    Content.author_user_id == user.id,
                    Content.origin == "submitted",
                    ContentReview.seen_by_author_at.is_(None),
                )
            )
            counts["content"] = unseen + unread
        elif user.hcp_id and "rep_messages" in counts:
            # Replies from MLR about delivered material belong with the representative tab.
            counts["rep_messages"] += unread
    if can(user, Permission.NBA_REVIEW_HCP) and can(user, Permission.HCP_READ_ASSIGNED):
        # A representative: recommendations to review or send and visits awaiting an outcome
        # (the queue), and the HCP work owed (requests, follow-ups and meetings due).
        counts["outreach"] = sum(workload.hcp_outreach(db, user).values())
        counts["hcp_work"] = workload.hcp_work_due(db, user)
    if can(user, Permission.PRIVACY_MANAGE):
        counts["privacy_requests"] = db.scalar(
            select(func.count())
            .select_from(PrivacyRequest)
            .where(PrivacyRequest.status.in_(("submitted", "in_review")))
        )
    if can(user, Permission.USER_MANAGE):
        counts["specialty_requests"] = db.scalar(
            select(func.count())
            .select_from(SpecialtyChangeRequest)
            .where(SpecialtyChangeRequest.status == "pending")
        )
        counts["patients_without_care_manager"] = len(records.unassigned_real_patients(db))
    return counts
