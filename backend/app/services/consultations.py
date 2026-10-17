"""Consultations routed to the signed-in HCP (moved here from `api/me.py`, unchanged in output).

A consultation is the HCP's work, and its clinical context theirs to see, only while it is with
them or waiting for the care manager to close it after their answer. The record comes from the
session (`user.hcp_id`), never from a request. Read only: `refresh=False` lets a caller read
adherence figures without recomputing them (the AI context layer does).
"""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.clinical import care, records
from app.core import clock
from app.core.permissions import Permission, can
from app.models import CareNote, CareRequest, Patient, PatientCondition, PatientTherapy, User
from app.models.enums import (
    CareNoteKind,
    CareRequestStatus,
    CareRequestType,
    ConditionStatus,
    ReviewStatus,
)
from app.services import serializers as out
from app.services.errors import Forbidden, NoRecord

WITH_HCP = (CareRequestStatus.AWAITING_HCP, CareRequestStatus.HCP_RESPONDED)
GROUP_ORDER = {"waiting": 0, "with_care_manager": 1, "history": 2}


def own_hcp(user: User) -> str:
    if not can(user, Permission.SELF_CONSULTATIONS_MANAGE):
        raise Forbidden("Your account has no consultations.")
    if user.hcp_id is None:
        raise NoRecord("No record is linked to this account yet. Ask an administrator.")
    return user.hcp_id


def consultation_out(db: Session, r: CareRequest, hcp_id: str, *, refresh: bool = True) -> dict:
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
            out.therapy_out(db, t, today, with_risk=False, refresh=refresh)
            | {"drug_name": t.drug_name}
            for t in therapies
        ]
    return row


def hcp_consultations(db: Session, user: User, *, refresh: bool = True) -> dict:
    """Consultations care managers routed to this HCP, in three groups: waiting for them,
    answered and with the care manager, and history (closed, withdrawn or declined by them).
    Clinical details are included only for the first two."""
    hcp_id = own_hcp(user)
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
    items = [consultation_out(db, r, hcp_id, refresh=refresh) for r in rows]
    items.sort(key=lambda i: GROUP_ORDER[i["group"]])
    return {
        "waiting": sum(1 for i in items if i["group"] == "waiting"),
        "with_care_manager": sum(1 for i in items if i["group"] == "with_care_manager"),
        "history": sum(1 for i in items if i["group"] == "history"),
        "items": items,
    }
