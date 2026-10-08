"""Figures behind the role-specific charts (`app.insights`). One endpoint per working area,
each behind the permission of the page it serves and limited to the caller's own scope:

  /api/insights/patient     the patient's own medicines            self:health:manage
  /api/insights/hcp         consultations routed to the HCP        self:consultations:manage
  /api/insights/care        the care manager's panel               patient:care:manage
  /api/insights/content     MLR review                             content:approve
  /api/insights/rep         the representative's assigned HCPs     nba:review:hcp
  /api/insights/operations  platform counts, no health data        user:manage
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.me import own_hcp_id, own_patient_id
from app.core import clock
from app.core.db import get_db
from app.core.permissions import Permission
from app.insights import care, hcp, mlr, ops, patient, rep
from app.models import User
from app.models.tables import utcnow

router = APIRouter(prefix="/api/insights", tags=["insights"])


@router.get("/patient")
def patient_insights(
    user: User = Depends(require_permission(Permission.SELF_HEALTH_MANAGE)),
    db: Session = Depends(get_db),
) -> dict:
    return patient.medicine_on_hand(db, own_patient_id(user), clock.get_today(db))


@router.get("/hcp")
def hcp_insights(
    user: User = Depends(require_permission(Permission.SELF_CONSULTATIONS_MANAGE)),
    db: Session = Depends(get_db),
) -> dict:
    hcp_id = own_hcp_id(user)
    return {
        "workload": hcp.consultation_workload(db, hcp_id),
        "response_times": hcp.response_times(db, hcp_id),
        "streak": hcp.answered_streak(db, hcp_id),
    }


@router.get("/care")
def care_insights(
    user: User = Depends(require_permission(Permission.PATIENT_CARE_MANAGE)),
    db: Session = Depends(get_db),
) -> dict:
    today = clock.get_today(db)
    return {
        "attention": care.attention_points(db, user, today),
        "follow_ups": care.follow_up_load(db, user, today),
        "waiting_with_hcp": care.waiting_with_hcp(db, user, today),
        "streak": care.no_overdue_streak(db, user, today),
    }


@router.get("/content")
def content_insights(
    user: User = Depends(require_permission(Permission.CONTENT_APPROVE)),
    db: Session = Depends(get_db),
) -> dict:
    now = utcnow()
    return {
        "waiting": mlr.waiting(db, now),
        "turnaround": mlr.turnaround(db, now),
        "concerns": mlr.concerns(db, now),
        "expiring": mlr.expiring(db, clock.get_today(db)),
        "expiry_window_days": mlr.EXPIRY_WINDOW_DAYS,
    }


@router.get("/rep")
def rep_insights(
    user: User = Depends(require_permission(Permission.NBA_REVIEW_HCP)),
    db: Session = Depends(get_db),
) -> dict:
    today = clock.get_today(db)
    return {
        "contact_windows": rep.contact_windows(db, user),
        "upcoming": rep.upcoming(db, user, today),
        "engagement": rep.engagement_states(db, user, today),
        "activity": rep.activity(db, user, today),
        "streak": rep.on_time_streak(db, user),
        "today": today,
    }


@router.get("/operations")
def operations_insights(
    user: User = Depends(require_permission(Permission.USER_MANAGE)),
    db: Session = Depends(get_db),
) -> dict:
    today = clock.get_today(db)
    return {
        "workload": ops.role_workload(db),
        "failures": ops.failures(db, today),
        "active_users": ops.active_users(db, today),
    }
