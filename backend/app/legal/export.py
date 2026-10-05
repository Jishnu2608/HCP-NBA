"""A copy of your own data, generated on request (portability / access, self-service part).

Contains the account, its consent history and privacy requests, and for patients and HCPs
the record they see in their portal. Internal scores and recommendations prepared for staff
are not part of the self-service copy (the product does not show them to patients and
HCPs); a full access request, reviewed by a person, covers them. No other person's data is
included.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.api.serializers import location_label
from app.clinical import care, hcps
from app.legal import consents, requests
from app.models import (
    Consent,
    Hcp,
    Interaction,
    MedicationFill,
    Patient,
    PatientTherapy,
    User,
)
from app.models.enums import TargetType
from app.models.tables import utcnow

NOTE = (
    "Self-service copy of your data. Internal scores, model outputs and recommendations "
    "prepared for staff are not included here; submit an access request in Data & privacy "
    "to receive them after review."
)


def _patient(db: Session, patient_id: str) -> dict:
    p = db.get(Patient, patient_id)
    therapies = db.scalars(select(PatientTherapy).where(PatientTherapy.patient_id == patient_id))
    fills = db.scalars(
        select(MedicationFill)
        .where(MedicationFill.patient_id == patient_id)
        .order_by(MedicationFill.fill_date)
    )
    outreach = db.scalars(
        select(Consent).where(Consent.patient_id == patient_id).order_by(Consent.effective_from)
    )
    return {
        "record": {
            "name": f"{p.first_name} {p.last_name}".strip(),
            "location": location_label(p),
            "plan_type": p.plan_type,
            "preferred_channel": p.preferred_channel,
        },
        # Conditions, medications (reported and confirmed), care requests, instructions.
        "health_profile": {
            k: v
            for k, v in care.health_record(db, p, for_patient=True).items()
            if k in ("conditions", "medications", "requests", "notes", "care_team")
        },
        "therapies": [
            {
                "measure": t.measure,
                "drug_name": t.drug_name,
                "start_date": t.start_date,
                "days_supply": t.days_supply,
                "status": t.status,
                "review_status": t.review_status,
            }
            for t in therapies
        ],  # fmt: skip
        "fills": [
            {"drug_name": f.drug_name, "fill_date": f.fill_date, "days_supply": f.days_supply}
            for f in fills
        ],
        "contact_preferences": [
            {
                "purpose": c.purpose,
                "channel": c.channel,
                "granted": c.granted,
                "effective_from": c.effective_from,
                "effective_to": c.effective_to,
            }
            for c in outreach
        ],  # fmt: skip
        "messages": _messages(db, TargetType.PATIENT, patient_id),
    }


def _hcp(db: Session, hcp_id: str) -> dict:
    h = db.get(Hcp, hcp_id)
    return {
        "record": {
            "name": f"Dr. {h.first_name} {h.last_name}",
            "npi": h.npi,
            "specialties": hcps.specialties_of(db, h.hcp_id),
            "organization": h.organization,
            "city": h.city,
            "state": h.state,
        },
        "messages": _messages(db, TargetType.HCP, hcp_id),
    }


def _messages(db: Session, target_type: str, target_id: str) -> list[dict]:
    rows = db.scalars(
        select(Interaction)
        .where(
            Interaction.target_type == target_type,
            Interaction.target_id == target_id,
            Interaction.source == "nba",
        )
        .order_by(Interaction.int_ts)
    )
    return [{"received": i.int_ts, "channel": i.channel, "outcome": i.outcome} for i in rows]


def build(db: Session, user: User) -> dict:
    data = {
        "generated_at": utcnow(),
        "note": NOTE,
        "account": {
            "name": user.display_name,
            "email": user.email,
            "role": user.role,
            "date_of_birth": user.date_of_birth,
            "country": user.country,
            "region": user.region,
            "email_verified": user.verified,
            "professionally_verified": user.professionally_verified,
            "created_at": user.created_at,
            "last_login_at": user.last_login_at,
        },
        "consent_history": consents.history(db, user),
        "privacy_requests": requests.own(db, user),
    }
    if user.patient_id:
        data["patient"] = _patient(db, user.patient_id)
    if user.hcp_id:
        data["hcp"] = _hcp(db, user.hcp_id)
    audit.record(db, "data_exported", "user", user.id, actor=user.username, actor_role=user.role)
    return data
