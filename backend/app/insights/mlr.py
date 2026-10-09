"""Compliance figures: how long submissions have waited, how long decisions took, which
perspective raised concerns, and approved material about to expire.

There is no review-time target in the product, so nothing is marked late: times are shown
as they are. Statuses come from the content lifecycle (`content.governance`)."""

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import serializers as out
from app.insights import typical
from app.models import Content, ContentReview
from app.models.enums import MlrStatus

PERSPECTIVES = (("medical", "Medical"), ("legal", "Legal"), ("regulatory", "Regulatory"))
TURNAROUND_DAYS = 90
EXPIRY_WINDOW_DAYS = 60
TURNAROUND_BINS = (
    ("under_1", "Under 1 day", 0, 1),
    ("1_2", "1 to 2 days", 1, 3),
    ("3_5", "3 to 5 days", 3, 6),
    ("6_10", "6 to 10 days", 6, 11),
    ("over_10", "More than 10 days", 11, None),
)


def waiting(db: Session, now: datetime) -> list[dict]:
    """Every submitted version still waiting for a decision, longest wait first."""
    rows = db.scalars(select(Content).where(Content.mlr_status == MlrStatus.PENDING)).all()
    items = [
        {
            "content_id": c.content_id,
            "title": c.title,
            "version": c.version,
            "submitted_at": c.submitted_at,
            "days_waiting": round(max(0.0, (now - c.submitted_at).total_seconds() / 86400), 1)
            if c.submitted_at
            else None,
            "claimed": c.review_owner_user_id is not None,
        }
        for c in rows
    ]
    items.sort(key=lambda i: -(i["days_waiting"] or 0))
    return items


def turnaround(db: Session, now: datetime, days: int = TURNAROUND_DAYS) -> dict:
    """Days from submission to decision for versions decided in the last `days` days."""
    since = now - timedelta(days=days)
    rows = db.scalars(
        select(Content).where(
            Content.submitted_at.is_not(None),
            Content.decided_at.is_not(None),
            Content.decided_at >= since,
        )
    ).all()
    items = [
        {
            "content_id": c.content_id,
            "title": c.title,
            "version": c.version,
            "status": c.mlr_status,
            "submitted_at": c.submitted_at,
            "decided_at": c.decided_at,
            "days": round((c.decided_at - c.submitted_at).total_seconds() / 86400, 1),
        }
        for c in rows
        if c.decided_at >= c.submitted_at
    ]
    items.sort(key=lambda i: i["decided_at"])
    bins = [
        {
            "key": key,
            "label": label,
            "value": sum(1 for i in items if i["days"] >= lo and (hi is None or i["days"] < hi)),
        }
        for key, label, lo, hi in TURNAROUND_BINS
    ]
    return {
        "window_days": days,
        "items": items,
        "bins": bins,
        "summary": typical([i["days"] for i in items]),
    }


def concerns(db: Session, now: datetime, days: int = TURNAROUND_DAYS) -> dict:
    """How often each review perspective recorded a concern in the last `days` days, and
    on which content. Only decisions that recorded the perspectives are counted."""
    rows = db.scalars(
        select(ContentReview)
        .where(ContentReview.ts >= now - timedelta(days=days))
        .order_by(ContentReview.ts.desc())
    ).all()
    assessed = [r for r in rows if any(getattr(r, key) for key, _ in PERSPECTIVES)]
    perspectives = []
    for key, label in PERSPECTIVES:
        flagged = [r for r in assessed if (getattr(r, key) or {}).get("verdict") == "concern"]
        perspectives.append(
            {
                "key": key,
                "label": label,
                "value": len(flagged),
                "content_ids": list(dict.fromkeys(r.content_id for r in flagged)),
            }
        )
    return {"window_days": days, "assessed_reviews": len(assessed), "perspectives": perspectives}


def expiring(db: Session, today: date, within: int = EXPIRY_WINDOW_DAYS) -> list[dict]:
    """Approved, usable versions whose approval ends within `within` days: days remaining
    (not a percentage, since validity periods differ)."""
    rows = db.scalars(
        select(Content).where(
            Content.mlr_status == MlrStatus.APPROVED,
            Content.expiry_date.is_not(None),
            Content.expiry_date <= today + timedelta(days=within),
        )
    ).all()
    items = []
    for c in rows:
        if not out.content_out(c, today)["usable"]:
            continue
        items.append(
            {
                "content_id": c.content_id,
                "title": c.title,
                "version": c.version,
                "audience": c.audience,
                "jurisdictions": c.jurisdictions,
                "approved_at": c.decided_at or c.effective_date,
                "expiry_date": c.expiry_date,
                "days_remaining": (c.expiry_date - today).days,
            }
        )
    items.sort(key=lambda i: i["days_remaining"])
    return items


PIPELINE = (
    ("draft", "Draft"),
    ("pending", "In MLR review"),
    ("changes_requested", "Changes requested"),
    ("approved", "Approved, in date"),
    ("expired", "Approval expired"),
    ("rejected", "Rejected"),
    ("withdrawn", "Withdrawn"),
    ("superseded", "Superseded"),
)


def pipeline(db: Session, today: date) -> list[dict]:
    """Every content version by its current MLR status; approved versions are split by
    whether their approval is still in date (`usable`, as everywhere)."""
    counts = {key: 0 for key, _ in PIPELINE}
    for c in db.scalars(select(Content)):
        if c.mlr_status == MlrStatus.APPROVED:
            counts["approved" if out.content_out(c, today)["usable"] else "expired"] += 1
        elif c.mlr_status in counts:
            counts[c.mlr_status] += 1
    return [{"key": k, "label": label, "value": counts[k]} for k, label in PIPELINE]
