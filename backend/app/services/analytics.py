"""Analytics reads with their scope applied: aggregate figures only, never a person."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import pipeline
from app.analytics import measures, snapshots
from app.core import clock, rbac
from app.core.permissions import Permission, can, can_any
from app.models import Patient, User
from app.services.errors import Forbidden

MEASURE_READ = (
    Permission.ANALYTICS_READ,
    Permission.PATIENT_READ_ALL,
    Permission.PATIENT_READ_ASSIGNED,
)


def quality_measures(db: Session, user: User) -> dict:
    """STAR-style adherence measures and persistence (analytics/measures.py) for the reader's
    population: every patient for readers with an all-patient or analytics scope, the care
    manager's own panel otherwise. Groups below the minimum size are suppressed."""
    if not can_any(user, *MEASURE_READ):
        raise Forbidden("Your account cannot read quality measures.")
    scoped = not can(user, Permission.PATIENT_READ_ALL) and not can(user, Permission.ANALYTICS_READ)
    ids = (
        set(
            db.scalars(
                select(Patient.patient_id).where(rbac.patient_filter(user, Patient.patient_id))
            )
        )
        if scoped
        else None
    )
    stored = None if scoped else snapshots.measures_all(db)
    result = (
        dict(stored)
        if stored is not None
        else measures.measures(db, clock.get_today(db), pipeline.engine_settings(db), ids)
    )
    result["scope"] = "panel" if scoped else "all"
    return result
