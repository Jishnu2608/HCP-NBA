"""Background job states for operational context: counts per kind and status and the age of the
oldest active job. No parameters, results, errors, worker names or requesters. Read only."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.permissions import Permission, can_any
from app.models import Job, User
from app.models.tables import utcnow
from app.services.errors import Forbidden
from app.services.schemas import Contract

READ = (Permission.ENGINE_OPERATE, Permission.AI_DIAGNOSTICS)
ACTIVE = ("queued", "running")


class JobQueueRow(Contract):
    kind: str
    status: str
    count: int
    oldest_minutes: int | None  # active rows only: minutes since the oldest was requested


def job_queue(db: Session, user: User, *, now: datetime | None = None) -> list[JobQueueRow]:
    if not can_any(user, *READ):
        raise Forbidden("Your account cannot read background jobs.")
    now = now or utcnow()
    rows = db.execute(
        select(Job.kind, Job.status, func.count(), func.min(Job.requested_at))
        .group_by(Job.kind, Job.status)
        .order_by(Job.kind, Job.status)
    ).all()
    return [
        JobQueueRow(
            kind=kind,
            status=status,
            count=n,
            oldest_minutes=int((now - oldest).total_seconds() // 60)
            if status in ACTIVE and oldest is not None
            else None,
        )
        for kind, status, n, oldest in rows
    ]
