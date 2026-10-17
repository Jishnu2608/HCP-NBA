"""Patient context: the scoped patient record and Patient 360."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import pipeline
from app.clinical import care, records
from app.clinical import hcps as hcp_records
from app.core import clock, rbac
from app.core.permissions import Permission, can_any
from app.insights import care as care_insights
from app.models import (
    EngineCycle,
    Hcp,
    MedicationFill,
    Patient,
    PatientHcp,
    PatientTherapy,
    User,
)
from app.models.enums import Channel, ConsentPurpose, ReviewStatus, TargetType
from app.services import common
from app.services import serializers as out
from app.services.errors import Forbidden, NotFound
from app.services.schemas import (
    CareTeamMember,
    FillOut,
    PatientProfile,
    PersonRef,
    SpecialtyOut,
    TherapyOut,
)

OUTREACH_CHANNELS = (Channel.SMS, Channel.EMAIL, Channel.PORTAL, Channel.PHONE)
READ = (Permission.PATIENT_READ_ALL, Permission.PATIENT_READ_ASSIGNED)


def scoped_patient(db: Session, user: User, patient_id: str) -> Patient:
    """The patient record, if this staff reader may see it in full."""
    if not can_any(user, *READ):
        raise Forbidden("Your account cannot read patient records.")
    p = db.scalar(
        select(Patient).where(
            Patient.patient_id == patient_id, rbac.patient_filter(user, Patient.patient_id)
        )
    )
    if p is None:
        raise NotFound("Patient not found")
    return p


def patient_profile(
    db: Session, user: User, patient_id: str, *, refresh: bool = True
) -> PatientProfile:
    """Patient 360 for a staff reader: profile, adherence per confirmed medicine (with
    persistence), fills, care team, consent, engagement, history, the open recommendation and
    the health record. Reported medicines appear only under `health` until confirmed.

    `refresh=False` reads without writing: a real patient's adherence figures that are not as
    of today are returned as stored instead of being recomputed (see `stale_therapies`)."""
    p = scoped_patient(db, user, patient_id)
    today = clock.get_today(db)
    therapies = db.scalars(
        select(PatientTherapy).where(
            PatientTherapy.patient_id == patient_id,
            PatientTherapy.review_status != ReviewStatus.REPORTED,
        )
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
    consents = common.consents(db, patient_id, today)
    granted = sum(
        1
        for ch in OUTREACH_CHANNELS
        if any(
            c.granted and c.in_effect and c.purpose == ConsentPurpose.OUTREACH and c.channel == ch
            for c in consents
        )
    )
    return PatientProfile(
        patient_id=p.patient_id,
        name=out.patient_name(p),
        age=int((today - p.birth_date).days / 365.25),
        sex=p.sex,
        plan_type=p.plan_type,
        **out.patient_brief(p),
        preferred_channel=p.preferred_channel,
        risk_segment=p.risk_segment,
        # The risk score on each snapshot date: the same readers already see it per therapy.
        risk_history=care_insights.risk_history(db, p.patient_id),
        as_of_date=today,
        therapies=[
            TherapyOut(**out.therapy_out(db, t, today, with_risk=True, refresh=refresh))
            for t in therapies
        ],
        fills=[
            FillOut(therapy_id=f.therapy_id, fill_date=f.fill_date, days_supply=f.days_supply)
            for f in fills
        ],
        care_team=[
            CareTeamMember(
                hcp_id=h.hcp_id,
                name=out.hcp_name(h),
                specialties=[SpecialtyOut(**s) for s in hcp_records.specialty_out(db, h.hcp_id)],
                is_primary=primary,
            )
            for h, primary in hcps
        ],
        consents=consents,
        outreach_channels_granted=granted,
        outreach_channels_total=len(OUTREACH_CHANNELS),
        care_managers=[
            PersonRef(id=u.id, name=u.display_name)
            for u in records.responsible_care_managers(db, patient_id, active_only=True)
        ],
        last_cycle_at=db.scalar(select(func.max(EngineCycle.finished_ts))),
        features=out.features_out(db, TargetType.PATIENT, patient_id, today),
        interactions=common.history(db, TargetType.PATIENT, patient_id),
        open_nba=common.open_recommendation(db, TargetType.PATIENT, patient_id),
        health=care.health_record(db, p, for_patient=False, refresh=refresh),
    )


def stale_therapies(db: Session, user: User, patient_id: str) -> list[int]:
    """Confirmed therapies of this patient whose stored adherence figures are older than today
    (real patients only; synthetic figures come from the engine cycle). A therapy with no
    figures at all is not stale: there is nothing to show (no fill yet). Read only."""
    p = scoped_patient(db, user, patient_id)
    today = clock.get_today(db)
    stale = []
    for t in db.scalars(select(PatientTherapy).where(PatientTherapy.patient_id == p.patient_id)):
        snap = out.latest_snapshot(db, t.id, today)
        if snap is not None and pipeline.needs_refresh(db, t, snap, today):
            stale.append(t.id)
    return stale


def refresh_if_stale(db: Session, user: User, patient_id: str) -> bool:
    """Brings a real patient's adherence figures up to today, exactly as opening Patient 360
    does. A screen's decision (the Case Brief route calls it before reading); the AI context
    layer itself never writes. Returns True when figures were recomputed."""
    if not stale_therapies(db, user, patient_id):
        return False
    pipeline.refresh_patient(db, patient_id)
    return True
