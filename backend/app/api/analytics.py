"""Leadership and learning analytics. Aggregates only: no individual HCP or patient is
identifiable in any response here."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.analytics import outcomes, queries, snapshots
from app.api.deps import require_permission
from app.core import clock
from app.core.db import get_db
from app.core.permissions import Permission
from app.models import User
from app.services import analytics as analytics_service

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
    source_key = "baseline" if source == "earlier" else source
    if since is None and segment_by in snapshots.LEARNING_DIMENSIONS.get(audience, ()):
        stored = snapshots.learning_for(db, audience, segment_by, source_key)
        if stored is not None:
            return stored
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
    """STAR-style adherence measures and persistence for the reader's population
    (services/analytics.py)."""
    return analytics_service.quality_measures(db, user)
