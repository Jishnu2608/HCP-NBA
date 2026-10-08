"""A care manager's figures over their own panel: who may need attention (risk against time
since the last contact), dated work by due date, consultations waiting for an HCP, a
patient's risk over time, and the no-overdue streak.

The panel scope is the same filter every care page uses (`rbac.patient_filter`). Risk is
the feature layer's adherence risk score (0 to 100), shown as recorded, never reinterpreted."""

from datetime import date, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app import pipeline
from app.api import serializers as out
from app.core import rbac
from app.insights import streaks
from app.models import (
    AdherenceSnapshot,
    CareNote,
    CareRequest,
    Hcp,
    Interaction,
    Patient,
    User,
)
from app.models.enums import CareRequestStatus, CareRequestType, TargetType


def _latest_risk(db: Session, scope) -> dict[str, float]:
    """Each patient's risk score on their latest snapshot date (worst therapy)."""
    latest = (
        select(AdherenceSnapshot.patient_id, func.max(AdherenceSnapshot.as_of_date).label("d"))
        .where(scope(AdherenceSnapshot.patient_id))
        .group_by(AdherenceSnapshot.patient_id)
        .subquery()
    )
    rows = db.execute(
        select(AdherenceSnapshot.patient_id, func.max(AdherenceSnapshot.risk_score))
        .join(
            latest,
            and_(
                AdherenceSnapshot.patient_id == latest.c.patient_id,
                AdherenceSnapshot.as_of_date == latest.c.d,
            ),
        )
        .group_by(AdherenceSnapshot.patient_id)
    ).all()
    return {pid: score for pid, score in rows if score is not None}


def _last_contact(db: Session, scope) -> dict[str, object]:
    """The latest meaningful contact per patient: an outreach touch (sent or logged) or a
    note the care team recorded for them."""
    touches = dict(
        db.execute(
            select(Interaction.target_id, func.max(Interaction.int_ts))
            .where(Interaction.target_type == TargetType.PATIENT, scope(Interaction.target_id))
            .group_by(Interaction.target_id)
        ).all()
    )
    notes = dict(
        db.execute(
            select(CareNote.patient_id, func.max(CareNote.created_at))
            .where(scope(CareNote.patient_id))
            .group_by(CareNote.patient_id)
        ).all()
    )
    return {
        pid: max(v for v in (touches.get(pid), notes.get(pid)) if v is not None)
        for pid in set(touches) | set(notes)
    }


def attention_points(db: Session, user: User, today: date) -> dict:
    """One point per panel patient with a risk score: risk against days since the last
    contact. Patients with no contact on record are counted separately, not placed."""

    def scope(column):
        return rbac.patient_filter(user, column)

    risk = _latest_risk(db, scope)
    contact = _last_contact(db, scope)
    patients = db.scalars(select(Patient).where(scope(Patient.patient_id))).all()
    points, never = [], 0
    for p in patients:
        score = risk.get(p.patient_id)
        if score is None:
            continue
        last = contact.get(p.patient_id)
        if last is None:
            never += 1
            continue
        points.append(
            {
                "patient_id": p.patient_id,
                "name": out.patient_name(p),
                "risk_score": round(score, 1),
                "risk_segment": p.risk_segment,
                "last_contact": last,
                "days_since_contact": max(0, (today - last.date()).days),
            }
        )
    points.sort(key=lambda r: (-r["risk_score"], -r["days_since_contact"]))
    return {
        "points": points,
        "no_contact_on_record": never,
        "panel_size": len(patients),
        # The score bands the feature layer uses for low / medium / high (0 to 100).
        "cutoffs": pipeline.engine_settings(db)["risk_cutoffs"],
    }


def risk_history(db: Session, patient_id: str) -> list[dict]:
    """The patient's risk score on each snapshot date (worst therapy that day)."""
    rows = db.execute(
        select(AdherenceSnapshot.as_of_date, func.max(AdherenceSnapshot.risk_score))
        .where(AdherenceSnapshot.patient_id == patient_id)
        .group_by(AdherenceSnapshot.as_of_date)
        .order_by(AdherenceSnapshot.as_of_date)
    ).all()
    return [{"date": d, "risk_score": round(s, 1)} for d, s in rows if s is not None]


def _dated(db: Session, user: User):
    return db.scalars(
        select(CareRequest).where(
            rbac.patient_filter(user, CareRequest.patient_id), CareRequest.due_date.is_not(None)
        )
    ).all()


def follow_up_load(db: Session, user: User, today: date) -> dict:
    """Open dated work on the panel by when it is due, and dated work closed in the last
    seven days. "Overdue" means open after its due date, as everywhere else."""
    rows = _dated(db, user)
    open_ = [r for r in rows if r.status != CareRequestStatus.CLOSED]
    week = today + timedelta(days=7)
    segments = [
        {
            "key": "overdue",
            "label": "Overdue",
            "tone": "bad",
            "value": sum(1 for r in open_ if r.due_date < today),
        },
        {
            "key": "today",
            "label": "Due today",
            "tone": "warn",
            "value": sum(1 for r in open_ if r.due_date == today),
        },
        {
            "key": "week",
            "label": "Due in the next 7 days",
            "tone": "info",
            "value": sum(1 for r in open_ if today < r.due_date <= week),
        },
        {
            "key": "later",
            "label": "Due later",
            "tone": "neutral",
            "value": sum(1 for r in open_ if r.due_date > week),
        },
    ]
    done = sum(
        1
        for r in rows
        if r.status == CareRequestStatus.CLOSED
        and r.closed_at is not None
        and r.closed_at.date() > today - timedelta(days=7)
    )
    return {"segments": segments, "open": len(open_), "closed_last_7_days": done}


def waiting_with_hcp(db: Session, user: User, today: date) -> list[dict]:
    """Consultations on the panel waiting for an HCP's answer, longest wait first."""
    rows = db.scalars(
        select(CareRequest).where(
            rbac.patient_filter(user, CareRequest.patient_id),
            CareRequest.type == CareRequestType.CONSULTATION,
            CareRequest.status == CareRequestStatus.AWAITING_HCP,
        )
    ).all()
    items = []
    for r in rows:
        since = (r.routed_at or r.updated_at).date()
        hcp = db.get(Hcp, r.assigned_hcp_id) if r.assigned_hcp_id else None
        items.append(
            {
                "request_id": r.id,
                "patient_id": r.patient_id,
                "patient": out.patient_name(db.get(Patient, r.patient_id)),
                "hcp": out.hcp_name(hcp) if hcp else None,
                "routed_on": since,
                "days_waiting": max(0, (today - since).days),
            }
        )
    items.sort(key=lambda i: -i["days_waiting"])
    return items


def no_overdue_streak(db: Session, user: User, today: date) -> dict | None:
    return streaks.no_overdue_days(
        ((r.due_date, r.created_at, r.closed_at) for r in _dated(db, user)), today
    )
