"""The scheduled engine cycle: the loop turns once a day without anyone pressing a button.

Off by default (`auto_cycle.enabled` in the engine settings). When on, the worker's first
check after `auto_cycle.hour_utc` on a day with no cycle yet *queues* a cycle job, the same
job as the Engine page's "run cycle", marked as scheduled. Nothing runs inside a web process:
the check lives in the worker loop (app/worker.py), and because queuing takes the single
engine lock, two workers can never queue it twice. No retraining (an administrator chooses
when models change), nothing is approved or sent, and the run is audited as
`cycle_scheduled`. A cycle someone already ran today counts: the schedule never adds a second.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import jobs
from app.core.engine_config import get_config
from app.models import EngineCycle, Job


def due(db: Session, now: datetime) -> bool:
    config = get_config(db, "auto_cycle")
    if not config.get("enabled") or now.hour < config["hour_utc"]:
        return False
    ran_today = db.scalar(
        select(func.count()).select_from(EngineCycle).where(EngineCycle.as_of_date == now.date())
    )
    queued_today = db.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.kind == "cycle", Job.status.in_(jobs.ACTIVE))
    )
    return not ran_today and not queued_today


def enqueue_if_due(db: Session, now: datetime | None = None) -> int | None:
    """Queues today's scheduled cycle if the schedule says so; returns the job id, or None.
    Commits."""
    now = now or datetime.now(UTC).replace(tzinfo=None)
    if not due(db, now):
        return None
    try:
        job = jobs.submit(db, "cycle", None, {"retrain": False, "scheduled": True})
    except jobs.JobConflict:
        return None
    return job.id


def run_if_due(session_factory, now: datetime | None = None) -> int | None:
    """Queues the scheduled cycle if due and runs it to completion in this process (the
    worker, or a test). Returns the cycle id, or None."""
    with session_factory() as db:
        job_id = enqueue_if_due(db, now)
    if job_id is None:
        return None
    jobs.run_pending(session_factory)
    with session_factory() as db:
        job = db.get(Job, job_id)
        return (job.result or {}).get("cycle_id") if job.status == jobs.SUCCEEDED else None
