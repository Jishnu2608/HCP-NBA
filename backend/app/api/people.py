"""HCP 360 and Patient 360 for staff roles. Portal users have their own endpoints in me.py."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app import audit, unify
from app.api.deps import require_permission
from app.clinical import activity, records
from app.clinical import hcps as hcp_records
from app.core import rbac
from app.core.db import get_db
from app.core.permissions import Permission, can
from app.core.pii import PatientViewer
from app.models import (
    Hcp,
    HcpSpecialty,
    Patient,
    User,
)
from app.models.enums import Channel, TargetType
from app.services import common, patients
from app.services import hcps as hcps_service
from app.services import serializers as out
from app.services.schemas import HcpProfile, PatientProfile

OUTREACH_CHANNELS = (Channel.SMS, Channel.EMAIL, Channel.PORTAL, Channel.PHONE)

router = APIRouter(prefix="/api", tags=["profiles"])

patient_staff = require_permission(Permission.PATIENT_READ_ALL, Permission.PATIENT_READ_ASSIGNED)
hcp_staff = require_permission(Permission.HCP_READ_ALL, Permission.HCP_READ_ASSIGNED)


@router.get("/patients")
def list_patients(
    risk: str | None = None,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(patient_staff),
    db: Session = Depends(get_db),
) -> dict:
    viewer = PatientViewer(db, user)
    where = [rbac.patient_filter(user, Patient.patient_id)]
    if risk:
        where.append(Patient.risk_segment == risk)
    if q:
        like = f"%{q}%"
        # A name matches only patients this reader sees unmasked: otherwise a search for a
        # full name returning one masked row would reveal who the record is. Everyone can
        # search by patient id.
        named = viewer.visible_ids()
        where.append(
            or_(
                Patient.patient_id.ilike(like),
                and_(
                    Patient.patient_id.in_(named),
                    or_(Patient.first_name.ilike(like), Patient.last_name.ilike(like)),
                ),
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
        # Latest activity first (the system-wide order for patient lists), then patient id.
        select(Patient)
        .where(*where)
        .order_by(*activity.patient_order())
        .limit(limit)
        .offset(offset)
    ).all()
    return {
        "total": total,
        "by_risk": by_risk,
        "items": [
            {
                "patient_id": p.patient_id,
                "name": out.patient_name(p, viewer),
                "risk_segment": p.risk_segment,
                "plan_type": p.plan_type,
                "preferred_channel": p.preferred_channel,
                "last_activity_at": p.last_activity_at,
                # A real patient's responsible care manager: an administrator adding them to
                # another panel sees whom they move from before saving.
                "care_managers": [
                    {"id": u.id, "name": u.display_name}
                    for u in records.responsible_care_managers(db, p.patient_id)
                ]
                if records.is_real(p) and can(user, Permission.USER_MANAGE)
                else None,
                **out.patient_brief(p),
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


@router.get(
    "/patients/{patient_id}", response_model=PatientProfile, response_model_exclude_unset=True
)
def patient_360(
    patient_id: str, user: User = Depends(patient_staff), db: Session = Depends(get_db)
) -> PatientProfile:
    """Patient 360 (services/patients.py). Reading a full profile is audited."""
    profile = patients.patient_profile(db, user, patient_id)
    _viewed(db, user, TargetType.PATIENT, patient_id)
    return profile


consent_out = common.consent_out


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
            or_(
                Hcp.hcp_id.ilike(like),
                Hcp.last_name.ilike(like),
                Hcp.hcp_id.in_(
                    select(HcpSpecialty.hcp_id).where(HcpSpecialty.specialty.ilike(like))
                ),
            )
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
                "specialties": hcp_records.specialty_out(db, h.hcp_id),
                "origin": h.origin,
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


@router.get("/hcps/{hcp_id}", response_model=HcpProfile, response_model_exclude_unset=True)
def hcp_360(
    hcp_id: str, user: User = Depends(hcp_staff), db: Session = Depends(get_db)
) -> HcpProfile:
    """HCP 360 (services/hcps.py). Reading a full profile is audited."""
    profile = hcps_service.hcp_profile(db, user, hcp_id)
    _viewed(db, user, TargetType.HCP, hcp_id)
    return profile


@router.get("/unification")
def unification(
    _: User = Depends(require_permission(Permission.HCP_READ_ALL)), db: Session = Depends(get_db)
) -> dict:
    """How the synthetic upstream records resolved to HCP profiles: counts per source and
    status, and every record that was not merged (held for review, ambiguous, unmatched)."""
    return unify.summary(db)
