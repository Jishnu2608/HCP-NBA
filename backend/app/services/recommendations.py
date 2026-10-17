"""Recommendation context: a scoped recommendation with its reasons, options considered,
safeguards now, wording variants (identified readers only) and audit trail."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock, rbac
from app.core.permissions import Permission, can_any
from app.core.pii import PatientViewer
from app.models import Nba, User
from app.services import serializers as out
from app.services.errors import Forbidden, NotFound
from app.services.schemas import RecommendationDetail

READ = (
    Permission.NBA_READ_ALL,
    Permission.NBA_READ_GATED,
    Permission.NBA_READ_HCP_ASSIGNED,
    Permission.NBA_READ_PATIENT_ASSIGNED,
)


def scoped_recommendation(db: Session, user: User, nba_id: int) -> Nba:
    if not can_any(user, *READ):
        raise Forbidden("Your account cannot read recommendations.")
    nba = db.scalar(select(Nba).where(Nba.id == nba_id, rbac.nba_filter(user)))
    if nba is None:
        raise NotFound("Recommendation not found")
    return nba


def recommendation_detail(db: Session, user: User, nba_id: int) -> RecommendationDetail:
    """Gate-outcome readers (Compliance) get the same structure with the person's identity,
    the personalised wording and the drafts left out, and the audit trail redacted."""
    nba = scoped_recommendation(db, user, nba_id)
    detail = out.nba_detail(
        db,
        nba,
        clock.get_today(db),
        with_identity=rbac.can_see_target_profile(user),
        viewer=PatientViewer(db, user),
    )
    detail["can_review"] = rbac.can_review(user, nba)
    return RecommendationDetail(**detail)
