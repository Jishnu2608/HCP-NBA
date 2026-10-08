"""A representative's figures over their assigned HCPs: when each HCP may next be contacted
(the frequency rules, read only, never bypassed), the next fourteen days of HCP work, where
each HCP stands, what the last 90 days produced, and the on-time streak.

Nothing here counts contacts as an achievement: the streak counts follow-ups and meetings
completed on time, and the activity figures are counts with rates hidden below the
minimum sample."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import serializers as out
from app.commercial import contact, tasks
from app.core import rbac
from app.features import engagement as eng
from app.features.population import load_population
from app.insights import MIN_RATE_N, streaks
from app.models import Hcp, HcpTask, Interaction, Nba, User
from app.models.enums import NbaStatus, Outcome, TargetType

ACTIVITY_DAYS = 90
CALENDAR_DAYS = 14
INTERESTED = ("interested", "request_meeting", "need_info", "need_evidence")


def _assigned(db: Session, user: User) -> list[Hcp]:
    return db.scalars(
        select(Hcp).where(rbac.hcp_filter(user, Hcp.hcp_id)).order_by(Hcp.hcp_id)
    ).all()


def task_day(t: HcpTask) -> date | None:
    """The day a task falls on: a follow-up's due date, a meeting's day in its own time
    zone."""
    if t.scheduled_at is not None:
        when = t.scheduled_at.replace(tzinfo=ZoneInfo("UTC"))
        return when.astimezone(ZoneInfo(t.timezone or "UTC")).date()
    return t.due_date


def contact_windows(db: Session, user: User) -> dict:
    """Per assigned HCP: the last contact, the minimum gap and the first day the contact
    limits allow another touch (`commercial.contact.contact_window`, the rule the engine and
    proposals use). HCPs outside the engagement population are listed as not open."""
    pop = load_population(db)
    rows, closed = [], []
    for h in _assigned(db, user):
        if h.hcp_id not in pop.hcps:
            closed.append({"hcp_id": h.hcp_id, "name": out.hcp_name(h)})
            continue
        w = contact.contact_window(db, h, eng.hcp_state(pop, h.hcp_id))
        rows.append(
            {
                "hcp_id": h.hcp_id,
                "name": out.hcp_name(h),
                "last_contact": w["last_contact"],
                "next_allowed": w["next_allowed"],
                "allowed_now": w["allowed_now"],
                "min_gap_days": w["min_gap_days"],
                "reason": w["reason"],
            }
        )
    rows.sort(key=lambda r: (r["next_allowed"], r["name"]))
    return {"items": rows, "not_open": closed}


def upcoming(db: Session, user: User, today: date, days: int = CALENDAR_DAYS) -> dict:
    """Open HCP work per day for the next `days` days, plus what is already overdue."""
    open_ = db.scalars(tasks.visible(user).where(HcpTask.status.in_(tasks.OPEN))).all()
    cells = []
    for i in range(days):
        day = today + timedelta(days=i)
        on_day = [t for t in open_ if task_day(t) == day]
        cells.append(
            {
                "date": day,
                "follow_ups": sum(1 for t in on_day if t.kind == "follow_up"),
                "meetings": sum(1 for t in on_day if t.kind == "meeting"),
                "requests": sum(1 for t in on_day if t.kind == "hcp_request"),
            }
        )
    overdue = sum(1 for t in open_ if (d := task_day(t)) is not None and d < today)
    undated = sum(1 for t in open_ if task_day(t) is None)
    return {"days": cells, "overdue": overdue, "undated_requests": undated}


STATES = (
    ("meeting", "Meeting planned", "info"),
    ("request", "Request waiting for you", "warn"),
    ("follow_up_due", "Follow-up due", "warn"),
    ("follow_up_planned", "Follow-up planned", "info"),
    ("interested", "Interested or asked for more", "ok"),
    ("awaiting", "Awaiting a response", "neutral"),
    ("contacted", "Contacted, no further signal", "neutral"),
    ("not_now", "Not now", "neutral"),
    ("declined", "Not interested", "neutral"),
    ("none", "Not contacted in 90 days", "neutral"),
)


def engagement_states(db: Session, user: User, today: date) -> dict:
    """Where each assigned HCP stands: open work first (it decides what the representative
    does next), then the latest signal from the last 90 days."""
    hcps = _assigned(db, user)
    ids = [h.hcp_id for h in hcps]
    open_ = db.scalars(tasks.visible(user).where(HcpTask.status.in_(tasks.OPEN))).all()
    since = datetime.combine(today - timedelta(days=ACTIVITY_DAYS), datetime.min.time())
    latest: dict[str, Interaction] = {}
    for i in db.scalars(
        select(Interaction)
        .where(
            Interaction.target_type == TargetType.HCP,
            Interaction.target_id.in_(ids),
            Interaction.int_ts >= since,
        )
        .order_by(Interaction.int_ts)
    ):
        latest[i.target_id] = i
    members: dict[str, list[dict]] = {key: [] for key, _, _ in STATES}
    for h in hcps:
        mine = [t for t in open_ if t.hcp_id == h.hcp_id]
        last = latest.get(h.hcp_id)
        if any(t.kind == "meeting" for t in mine):
            key = "meeting"
        elif any(t.kind == "hcp_request" for t in mine):
            key = "request"
        elif any(t.kind == "follow_up" and t.due_date and t.due_date <= today for t in mine):
            key = "follow_up_due"
        elif any(t.kind == "follow_up" for t in mine):
            key = "follow_up_planned"
        elif last is None:
            key = "none"
        elif last.intent == "decline" or last.outcome == Outcome.DECLINED:
            key = "declined"
        elif last.intent == "not_now" or last.outcome_reason == "not_now":
            key = "not_now"
        elif last.intent in INTERESTED or last.outcome_reason in ("interested", "need_info"):
            key = "interested"
        elif last.outcome == Outcome.PENDING:
            key = "awaiting"
        else:
            key = "contacted"
        members[key].append({"hcp_id": h.hcp_id, "name": out.hcp_name(h)})
    segments = [
        {"key": key, "label": label, "tone": tone, "value": len(members[key]), "hcps": members[key]}
        for key, label, tone in STATES
    ]
    return {"total": len(hcps), "segments": segments}


def activity(db: Session, user: User, today: date, days: int = ACTIVITY_DAYS) -> dict:
    """What the representative's own work produced in the last `days` days, as counts.
    These are separate counts, not a strict funnel (a meeting may come from the HCP's own
    request). Response rates appear only from `MIN_RATE_N` sends."""
    since = datetime.combine(today - timedelta(days=days), datetime.min.time())
    approved = len(
        db.scalars(
            select(Nba.id).where(
                Nba.target_type == TargetType.HCP,
                Nba.reviewed_by_user_id == user.id,
                Nba.reviewed_ts >= since,
                Nba.status.in_((NbaStatus.APPROVED, NbaStatus.SENT, NbaStatus.RESPONDED)),
            )
        ).all()
    )
    sends = db.scalars(
        select(Interaction).where(
            Interaction.target_type == TargetType.HCP,
            Interaction.actor_user_id == user.id,
            Interaction.source == "nba",
            Interaction.int_ts >= since,
        )
    ).all()
    responded = [s for s in sends if s.outcome not in (Outcome.PENDING, Outcome.NO_RESPONSE)]
    interested = [
        s
        for s in sends
        if s.intent in INTERESTED
        or s.outcome_reason in ("interested", "need_info", "another_meeting")
    ]
    done = db.scalars(
        select(HcpTask).where(
            HcpTask.owner_user_id == user.id,
            HcpTask.status == "done",
            HcpTask.completed_at >= since,
        )
    ).all()
    stages = [
        {"key": "approved", "label": "Recommendations you approved", "value": approved},
        {"key": "sent", "label": "Content you sent", "value": len(sends)},
        {"key": "responded", "label": "Responses recorded", "value": len(responded)},
        {"key": "interested", "label": "Interested or asked for more", "value": len(interested)},
        {
            "key": "meetings",
            "label": "Meetings held",
            "value": sum(1 for t in done if t.kind == "meeting"),
        },
        {
            "key": "follow_ups",
            "label": "Follow-ups completed",
            "value": sum(1 for t in done if t.kind == "follow_up"),
        },
    ]
    rate = round(len(responded) / len(sends), 3) if len(sends) >= MIN_RATE_N else None
    return {"window_days": days, "stages": stages, "response_rate": rate, "min_rate_n": MIN_RATE_N}


def on_time_streak(db: Session, user: User) -> dict | None:
    done = db.scalars(
        select(HcpTask).where(
            HcpTask.owner_user_id == user.id,
            HcpTask.status == "done",
            HcpTask.kind.in_(("follow_up", "meeting")),
            HcpTask.completed_at.is_not(None),
        )
    ).all()
    pairs = [(t.completed_at, d) for t in done if (d := task_day(t)) is not None]
    return streaks.on_time_completions(pairs)
