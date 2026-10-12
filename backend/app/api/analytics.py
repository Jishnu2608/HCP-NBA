"""Leadership and learning analytics. Aggregates only: no individual HCP or patient is
identifiable in any response here."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import pipeline
from app.analytics import measures, outcomes, queries
from app.api.deps import require_permission
from app.core import clock, rbac
from app.core.db import get_db
from app.core.permissions import Permission, can
from app.models import Patient, User

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

analysts = require_permission(Permission.ANALYTICS_READ)
# The quality-measure view also serves care managers, for their own panel only.
measure_readers = require_permission(
    Permission.ANALYTICS_READ, Permission.PATIENT_READ_ALL, Permission.PATIENT_READ_ASSIGNED
)


def _invalid(message: str) -> HTTPException:
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT, {"code": "invalid_request", "message": message}
    )


@router.get("/overview")
def overview(_: User = Depends(analysts), db: Session = Depends(get_db)) -> dict:
    """Aggregate metrics only: no individual HCP or patient is identifiable here."""
    return queries.overview(db)


@router.get("/learning")
def learning(
    audience: Literal["PATIENT", "HCP"] = "PATIENT",
    segment_by: str = Query("situation", max_length=16),
    source: Literal["all", "engine", "earlier"] = "all",
    since: date | None = None,
    _: User = Depends(analysts),
    db: Session = Depends(get_db),
) -> dict:
    """What worked for whom: observed rates by segment, action and channel, with sample sizes."""
    today = clock.get_today(db)
    if audience == "HCP" and segment_by == "situation":
        segment_by = "segment"
    try:
        since = outcomes.since_param(since, today)
        return outcomes.what_worked(
            db, audience, segment_by, "baseline" if source == "earlier" else source, since
        )
    except ValueError as exc:
        raise _invalid(str(exc)) from exc


@router.get("/models")
def models(_: User = Depends(analysts), db: Session = Depends(get_db)) -> dict:
    """Model versions with their metrics, outcomes observed under each, and calibration."""
    return {"models": outcomes.model_history(db), "calibration": outcomes.calibration(db)}


@router.get("/holdout")
def holdout(_: User = Depends(analysts), db: Session = Depends(get_db)) -> dict:
    return outcomes.holdout_comparison(db, clock.get_today(db))


@router.get("/measures")
def quality_measures(user: User = Depends(measure_readers), db: Session = Depends(get_db)) -> dict:
    """STAR-style adherence measures and persistence for the reader's population: every
    patient for readers with an all-patient or analytics scope, the care manager's own panel
    otherwise. Groups below the minimum size are suppressed."""
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
    result = measures.measures(db, clock.get_today(db), pipeline.engine_settings(db), ids)
    result["scope"] = "panel" if scoped else "all"
    return result
