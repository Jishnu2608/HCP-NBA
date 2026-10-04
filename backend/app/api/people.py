"""HCP 360 and Patient 360 for staff roles. Portal users have their own endpoints in me.py."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import audit
from app.api import serializers as out
from app.api.deps import not_found, require_permission
from app.core import clock, rbac
from app.core.db import get_db
from app.core.permissions import Permission
from app.models import (
    Consent,
    Content,
    Hcp,
    Interaction,
    MedicationFill,
    Nba,
    Patient,
    PatientHcp,
    PatientTherapy,
    User,
)
from app.models.enums import NbaStatus, TargetType

router = APIRouter(prefix="/api", tags=["profiles"])

patient_staff = require_permission(Permission.PATIENT_READ_ALL, Permission.PATIENT_READ_ASSIGNED)
hcp_staff = require_permission(Permission.HCP_READ_ALL, Permission.HCP_READ_ASSIGNED)
LIVE = (NbaStatus.READY_FOR_REVIEW, NbaStatus.APPROVED, NbaStatus.BLOCKED)


def _history(db: Session, target_type: str, target_id: str, limit: int = 40) -> list[dict]:
    rows = db.scalars(
        select(Interaction)
        .where(Interaction.target_type == target_type, Interaction.target_id == target_id)
        .order_by(Interaction.int_ts.desc())
        .limit(limit)
    ).all()
    contents = {
        c.content_id: c
        for c in db.scalars(
            select(Content).where(Content.content_id.in_({r.content_id for r in rows}))
        )
    }
    return [out.interaction_out(r, contents) for r in rows]


def _open_nba(db: Session, target_type: str, target_id: str) -> dict | None:
    nba = db.scalar(
        select(Nba)
        .where(Nba.target_type == target_type, Nba.target_id == target_id, Nba.status.in_(LIVE))
        .order_by(Nba.id.desc())
    )
    return out.nba_summary(db, nba) if nba else None


@router.get("/patients")
def list_patients(
    risk: str | None = None,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(patient_staff),
    db: Session = Depends(get_db),
) -> dict:
    where = [rbac.patient_filter(user, Patient.patient_id)]
    if risk:
        where.append(Patient.risk_segment == risk)
    if q:
        like = f"%{q}%"
        where.append(
            or_(
                Patient.patient_id.ilike(like),
                Patient.first_name.ilike(like),
                Patient.last_name.ilike(like),
            )
        )
    total = db.scalar(select(func.count()).select_from(Patient).where(*where))
    by_risk = dict(
        db.execute(
            select(Patient.risk_segment, func.count())
            .where(rbac.patient_filter(user, Patient.patient_id))
            .group_by(Patient.risk_segment)
        ).all()
    )
    rows = db.scalars(
        select(Patient).where(*where).order_by(Patient.patient_id).limit(limit).offset(offset)
    ).all()
    return {
        "total": total,
        "by_risk": by_risk,
        "items": [
            {
                "patient_id": p.patient_id,
                "name": out.patient_name(p),
                "risk_segment": p.risk_segment,
                "plan_type": p.plan_type,
                "city": p.city,
                "state": p.state,
                "preferred_channel": p.preferred_channel,
            }
            for p in rows
        ],
    }


def _viewed(db: Session, user: User, target_type: str, target_id: str) -> None:
    """Access to a full profile is recorded, as a health-data system must."""
    audit.record(
        db, "profile_viewed", target_type.lower(), target_id, actor=user.username,
        actor_role=user.role,
    )  # fmt: skip
    db.commit()


@router.get("/patients/{patient_id}")
def patient_360(
    patient_id: str, user: User = Depends(patient_staff), db: Session = Depends(get_db)
) -> dict:
    p = db.scalar(
        select(Patient).where(
            Patient.patient_id == patient_id, rbac.patient_filter(user, Patient.patient_id)
        )
    )
    if p is None:
        raise not_found("Patient not found")
    _viewed(db, user, TargetType.PATIENT, patient_id)
    today = clock.get_today(db)
    therapies = db.scalars(
        select(PatientTherapy).where(PatientTherapy.patient_id == patient_id)
    ).all()
    fills = db.scalars(
        select(MedicationFill)
        .where(MedicationFill.patient_id == patient_id)
        .order_by(MedicationFill.fill_date)
    ).all()
    hcps = db.execute(
        select(Hcp, PatientHcp.is_primary)
        .join(PatientHcp, PatientHcp.hcp_id == Hcp.hcp_id)
        .where(PatientHcp.patient_id == patient_id)
    ).all()
    consents = db.scalars(
        select(Consent).where(Consent.patient_id == patient_id).order_by(Consent.effective_from)
    ).all()
    return {
        "patient_id": p.patient_id,
        "name": out.patient_name(p),
        "age": int((today - p.birth_date).days / 365.25),
        "sex": p.sex,
        "city": p.city,
        "state": p.state,
        "plan_type": p.plan_type,
        "preferred_channel": p.preferred_channel,
        "risk_segment": p.risk_segment,
        "as_of_date": today,
        "therapies": [out.therapy_out(db, t, today, with_risk=True) for t in therapies],
        "fills": [
            {"therapy_id": f.therapy_id, "fill_date": f.fill_date, "days_supply": f.days_supply}
            for f in fills
        ],
        "care_team": [
            {
                "hcp_id": h.hcp_id,
                "name": out.hcp_name(h),
                "specialty": h.specialty,
                "is_primary": primary,
            }
            for h, primary in hcps
        ],
        "consents": [consent_out(c, today) for c in consents],
        "features": out.features_out(db, TargetType.PATIENT, patient_id, today),
        "interactions": _history(db, TargetType.PATIENT, patient_id),
        "open_nba": _open_nba(db, TargetType.PATIENT, patient_id),
    }


def consent_out(c: Consent, today) -> dict:
    in_effect = c.effective_from <= today and (c.effective_to is None or today < c.effective_to)
    return {
        "purpose": c.purpose,
        "channel": c.channel,
        "granted": c.granted,
        "effective_from": c.effective_from,
        "effective_to": c.effective_to,
        "in_effect": in_effect,
        "source": c.source,
    }


@router.get("/hcps")
def list_hcps(
    segment: str | None = None,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(hcp_staff),
    db: Session = Depends(get_db),
) -> dict:
    where = [rbac.hcp_filter(user, Hcp.hcp_id)]
    if segment:
        where.append(Hcp.segment == segment)
    if q:
        like = f"%{q}%"
        where.append(
            or_(Hcp.hcp_id.ilike(like), Hcp.last_name.ilike(like), Hcp.specialty.ilike(like))
        )
    total = db.scalar(select(func.count()).select_from(Hcp).where(*where))
    rows = db.scalars(
        select(Hcp)
        .where(*where)
        .order_by(Hcp.value_score.desc().nulls_last(), Hcp.hcp_id)
        .limit(limit)
        .offset(offset)
    ).all()
    return {
        "total": total,
        "items": [
            {
                "hcp_id": h.hcp_id,
                "name": out.hcp_name(h),
                "specialty": h.specialty,
                "organization": h.organization,
                "city": h.city,
                "state": h.state,
                "segment": h.segment,
                "value_score": h.value_score,
                "channel_affinity": h.channel_affinity,
            }
            for h in rows
        ],
    }


@router.get("/hcps/{hcp_id}")
def hcp_360(hcp_id: str, user: User = Depends(hcp_staff), db: Session = Depends(get_db)) -> dict:
    h = db.scalar(select(Hcp).where(Hcp.hcp_id == hcp_id, rbac.hcp_filter(user, Hcp.hcp_id)))
    if h is None:
        raise not_found("HCP not found")
    _viewed(db, user, TargetType.HCP, hcp_id)
    today = clock.get_today(db)
    return {
        "hcp_id": h.hcp_id,
        "npi": h.npi,
        "name": out.hcp_name(h),
        "specialty": h.specialty,
        "taxonomy_code": h.taxonomy_code,
        "organization": h.organization,
        "city": h.city,
        "state": h.state,
        "rx_volume_annual": h.rx_volume_annual,
        "segment": h.segment,
        "value_score": h.value_score,
        "channel_affinity": h.channel_affinity,
        "as_of_date": today,
        "features": out.features_out(db, TargetType.HCP, hcp_id, today),
        "interactions": _history(db, TargetType.HCP, hcp_id),
        "open_nba": _open_nba(db, TargetType.HCP, hcp_id),
    }
