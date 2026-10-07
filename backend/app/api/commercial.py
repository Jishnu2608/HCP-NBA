"""A representative's commercial work: proposed contacts, HCP requests, follow-ups, meetings,
and the day's view of it. Scope is the HCPs currently assigned to the account."""

from datetime import datetime, time, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import serializers as out
from app.api.deps import not_found, require_permission
from app.api.schemas import StrictBody
from app.commercial import contact, tasks
from app.core import clock, rbac
from app.core.db import get_db
from app.core.permissions import Permission
from app.models import Content, Hcp, HcpTask, Interaction, Nba, User
from app.models.enums import TargetType

router = APIRouter(prefix="/api", tags=["commercial"])
reps = require_permission(Permission.NBA_REVIEW_HCP)


class ContactBody(StrictBody):
    content_id: str = Field(max_length=16)
    channel: Literal["email", "portal", "rep_visit"]


class TaskBody(StrictBody):
    hcp_id: str = Field(max_length=16)
    kind: Literal["follow_up", "meeting"]
    reason: str = Field(max_length=500)
    note: str | None = Field(default=None, max_length=500)
    content_id: str | None = Field(default=None, max_length=16)
    due_date: str | None = None
    scheduled_at: str | None = None
    timezone: str | None = Field(default=None, max_length=64)
    mode: Literal["in_person", "phone", "video"] | None = None
    # The HCP request (or earlier follow-up) this answers.
    source_task_id: int | None = None


class TaskUpdate(StrictBody):
    action: Literal["complete", "reschedule", "cancel"]
    outcome: Literal["completed", "no_response", "declined", "answered"] | None = None
    outcome_reason: str | None = Field(default=None, max_length=24)
    note: str | None = Field(default=None, max_length=500)
    due_date: str | None = None
    scheduled_at: str | None = None
    timezone: str | None = Field(default=None, max_length=64)


@router.get("/hcps/{hcp_id}/contact-options")
def contact_options(hcp_id: str, user: User = Depends(reps), db: Session = Depends(get_db)) -> dict:
    hcp = tasks.assigned_hcp(db, user, hcp_id)
    return contact.contact_options(db, hcp)


@router.post("/hcps/{hcp_id}/contact", status_code=201)
def propose_contact(
    hcp_id: str, body: ContactBody, user: User = Depends(reps), db: Session = Depends(get_db)
) -> dict:
    """The representative chooses approved content and a channel for an assigned HCP. Every
    gate runs as for the engine; the result is a recommendation to review and send."""
    hcp = tasks.assigned_hcp(db, user, hcp_id)
    nba = contact.propose(db, user, hcp, body.content_id, body.channel)
    db.commit()
    return out.nba_detail(db, nba, clock.get_today(db))


def _rows(db: Session, user: User, query) -> list[dict]:
    return [tasks.task_out(db, user, t) for t in db.scalars(query)]


@router.get("/hcp-work")
def work(
    view: Literal["today", "requests", "follow_ups", "meetings", "overdue", "done_today"] = Query(
        "today"
    ),
    user: User = Depends(reps),
    db: Session = Depends(get_db),
) -> dict:
    today = clock.get_today(db)
    base = tasks.visible(user)
    open_ = base.where(HcpTask.status.in_(tasks.OPEN))
    start = datetime.combine(today, time.min)
    end = start + timedelta(days=1)
    queries = {
        "requests": open_.where(HcpTask.kind == "hcp_request"),
        "follow_ups": open_.where(HcpTask.kind == "follow_up"),
        "meetings": open_.where(HcpTask.kind == "meeting"),
        "overdue": open_.where(HcpTask.due_date < today),
        "today": open_.where(HcpTask.due_date <= today),
        "done_today": base.where(
            HcpTask.status.in_(("done", "cancelled")), HcpTask.completed_at >= start
        ),
    }
    items = _rows(db, user, queries[view].order_by(HcpTask.due_date, HcpTask.id))
    counts = {k: len(list(db.scalars(q))) for k, q in queries.items()}
    # What I did today with my HCPs: sends, visits, calls and meetings, with their outcome.
    mine = rbac.assigned_hcp_ids(user)
    done = list(
        db.scalars(
            select(Interaction)
            .where(
                Interaction.target_type == TargetType.HCP,
                Interaction.target_id.in_(mine),
                Interaction.actor_user_id == user.id,
                Interaction.int_ts >= start,
                Interaction.int_ts < end,
            )
            .order_by(Interaction.int_ts.desc())
        )
    )
    contents = {c.content_id: c for c in db.scalars(select(Content))}
    engagements = []
    for i in done:
        row = out.interaction_out(i, contents)
        hcp = db.get(Hcp, i.target_id)
        row["hcp_id"], row["hcp_name"] = i.target_id, out.hcp_name(hcp) if hcp else i.target_id
        engagements.append(row)
    waiting = db.scalar(
        select(Nba.id).where(
            Nba.target_type == TargetType.HCP,
            Nba.target_id.in_(mine),
            Nba.status.in_(("ready_for_review", "approved")),
        )
    )
    return {
        "view": view,
        "items": items,
        "counts": counts,
        "engagements_today": engagements,
        "recommendations_open": waiting is not None,
    }


@router.post("/hcp-work", status_code=201)
def create_task(body: TaskBody, user: User = Depends(reps), db: Session = Depends(get_db)) -> dict:
    task = tasks.create(db, user, body.model_dump())
    db.commit()
    return tasks.task_out(db, user, task)


@router.patch("/hcp-work/{task_id}")
def update_task(
    task_id: int, body: TaskUpdate, user: User = Depends(reps), db: Session = Depends(get_db)
) -> dict:
    task = db.scalar(tasks.visible(user).where(HcpTask.id == task_id))
    if task is None:
        raise not_found("Not found")
    tasks.update(db, user, task, body.model_dump())
    db.commit()
    return tasks.task_out(db, user, task)
