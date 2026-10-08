"""Platform figures for administrators: open work per staff account, sign-in and safeguard
failures per day, and who signed in per day by role.

Counts only: no patient or HCP identity and no health information leaves this module. Each
account's open work uses the same definitions as that account's own menu counts
(`clinical.workload`), so the administrator sees what the user sees."""

from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.invitations import FAILED
from app.clinical import workload
from app.core.permissions import ROLE_LABELS, can
from app.core.permissions import Permission as P
from app.models import AuditLog, CareRequest, Content, Invitation, RateLimitHit, User
from app.models.enums import AccountStatus, CareRequestStatus, CareRequestType, MlrStatus

WINDOW_DAYS = 14


def role_workload(db: Session) -> list[dict]:
    """Open work per active staff account, by the permissions it holds. Administrators
    (who manage users) are not listed: they have no work queue of their own."""
    pending_content = db.scalar(
        select(func.count()).select_from(Content).where(Content.mlr_status == MlrStatus.PENDING)
    )
    rows = []
    for u in db.scalars(select(User).where(User.status == AccountStatus.ACTIVE).order_by(User.id)):
        if can(u, P.USER_MANAGE) or can(u, P.SELF_HEALTH_MANAGE):
            continue
        parts = []
        if can(u, P.PATIENT_CARE_MANAGE):
            parts.append(("Care requests needing them", workload.care_requests(db, u)["needs_you"]))
            o = workload.outreach(db, u)
            parts.append(
                (
                    "Patient outreach to review or send",
                    o["to_review"] + o["to_send"] + o["call_outcomes"],
                )
            )
        if can(u, P.NBA_REVIEW_HCP):
            o = workload.hcp_outreach(db, u)
            parts.append(
                (
                    "HCP outreach to review or send",
                    o["to_review"] + o["to_send"] + o["visit_outcomes"],
                )
            )
            parts.append(("HCP work due", workload.hcp_work_due(db, u)))
        if can(u, P.CONTENT_APPROVE):
            parts.append(("Content waiting for review", pending_content))
        if can(u, P.SELF_CONSULTATIONS_MANAGE) and u.hcp_id:
            parts.append(
                (
                    "Consultations waiting for their answer",
                    db.scalar(
                        select(func.count())
                        .select_from(CareRequest)
                        .where(
                            CareRequest.type == CareRequestType.CONSULTATION,
                            CareRequest.assigned_hcp_id == u.hcp_id,
                            CareRequest.status == CareRequestStatus.AWAITING_HCP,
                        )
                    ),
                )
            )
        if not parts:
            continue
        rows.append(
            {
                "user_id": u.id,
                "name": u.display_name,
                "role": u.role,
                "role_label": ROLE_LABELS.get(u.role, u.role),
                "open_work": sum(n or 0 for _, n in parts),
                "parts": [{"label": label, "value": n or 0} for label, n in parts],
            }
        )
    rows.sort(key=lambda r: (-r["open_work"], r["name"] or ""))
    return rows


def _days(today: date, days: int) -> list[date]:
    return [today - timedelta(days=days - 1 - i) for i in range(days)]


def _series(key: str, label: str, dates: list[date], counts: dict[date, int]) -> dict:
    points = [{"date": d, "value": counts.get(d, 0)} for d in dates]
    return {"key": key, "label": label, "points": points, "total": sum(p["value"] for p in points)}


def failures(db: Session, today: date, days: int = WINDOW_DAYS) -> list[dict]:
    """Per day for the last `days` days: failed sign-ins, failed verification codes,
    rate-limited requests, recommendations a reviewer could not approve because a safeguard
    failed, and invitation emails that could not be sent."""
    dates = _days(today, days)
    since = datetime.combine(dates[0], datetime.min.time())

    def audit_counts(action: str) -> dict[date, int]:
        counts: dict[date, int] = defaultdict(int)
        for (ts,) in db.execute(
            select(AuditLog.ts).where(AuditLog.action == action, AuditLog.ts >= since)
        ):
            counts[ts.date()] += 1
        return counts

    limited: dict[date, int] = defaultdict(int)
    for (ts,) in db.execute(select(RateLimitHit.ts).where(RateLimitHit.ts >= since)):
        limited[ts.date()] += 1
    invites: dict[date, int] = defaultdict(int)
    for (ts,) in db.execute(
        select(Invitation.created_at).where(
            Invitation.delivery == FAILED, Invitation.created_at >= since
        )
    ):
        invites[ts.date()] += 1
    return [
        _series("login_failed", "Failed sign-ins", dates, audit_counts("login_failed")),
        _series("otp_failed", "Wrong verification codes", dates, audit_counts("otp_failed")),
        _series("rate_limited", "Requests slowed by rate limits", dates, limited),
        _series(
            "blocked_at_review",
            "Blocked at review by a safeguard",
            dates,
            audit_counts("nba_blocked_at_review"),
        ),
        _series("invitation_failed", "Invitation emails not sent", dates, invites),
    ]


def active_users(db: Session, today: date, days: int = WINDOW_DAYS) -> dict:
    """Distinct accounts that signed in each day, by role."""
    dates = _days(today, days)
    since = datetime.combine(dates[0], datetime.min.time())
    seen: dict[str, dict[date, set]] = defaultdict(lambda: defaultdict(set))
    for ts, role, uid in db.execute(
        select(AuditLog.ts, AuditLog.actor_role, AuditLog.actor_user_id).where(
            AuditLog.action == "login_succeeded", AuditLog.ts >= since
        )
    ):
        seen[role][ts.date()].add(uid)
    roles = [r for r in ROLE_LABELS if r in seen]
    return {
        "dates": dates,
        "roles": [
            {
                "role": r,
                "label": ROLE_LABELS[r],
                "points": [{"date": d, "value": len(seen[r].get(d, ()))} for d in dates],
            }
            for r in roles
        ],
    }
