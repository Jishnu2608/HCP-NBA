"""Background jobs: long-running engine work, executed by the worker, never inside a request.

Lifecycle: queued -> running -> succeeded | failed | interrupted.

- **Submission** (`submit`) writes a `job` row and returns at once. Every engine operation
  takes the single "engine" lock (`job.lock_key`, unique): a second submission while one is
  queued or running is refused with the active job, so two cycles never interleave.
- **Claiming** (`claim_next`) is one conditional UPDATE (status queued -> running for that
  id), so any number of workers can poll the same database and each job runs once.
- **Execution** (`execute`) runs the handler in shorter transactions (catch-up, features,
  engine, drafts, summaries), heartbeating while it works. On an exception the open
  transaction is rolled back, the job is marked failed with a sanitised one-line summary
  (exception type and our own message only) and the lock is released. Nothing is retried
  automatically: the handlers are not blindly repeatable (a play-out resolves responses).
- **Recovery** (`recover_stale`): a running job whose heartbeat stopped (the worker died) is
  marked interrupted and its lock released, so it is visible and can be resubmitted.

Reset and bulk send stay synchronous (reset must hand the caller a new session cookie; bulk
send is a bounded demo shortcut) but take the same engine lock (`engine_lock`), so they can
never run alongside a queued or running job.
"""

import logging
import os
import socket
import threading
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit
from app.models import Job, User
from app.models.tables import utcnow

log = logging.getLogger("nba.jobs")

ENGINE_LOCK = "engine"
QUEUED, RUNNING, SUCCEEDED, FAILED, INTERRUPTED = (
    "queued", "running", "succeeded", "failed", "interrupted",
)  # fmt: skip
ACTIVE = (QUEUED, RUNNING)
HEARTBEAT_SECONDS = 15
# A running job whose heartbeat is older than this is treated as abandoned.
STALE_AFTER = timedelta(minutes=5)
# A synchronous operation (reset, bulk send) holding the lock longer than this was abandoned.
REQUEST_LOCK_LIMIT = timedelta(minutes=30)


class JobConflict(Exception):
    """Another engine operation is already queued or running."""

    def __init__(self, job: Job):
        super().__init__(f"Job {job.id} ({job.kind}) is already {job.status}")
        self.job = job


def worker_identity() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"


def active(db: Session) -> Job | None:
    return db.scalar(select(Job).where(Job.lock_key == ENGINE_LOCK))


def job_out(job: Job) -> dict:
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "params": job.params,
        "requested_at": job.requested_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "progress": job.progress,
        "result": job.result,
        "error": job.error,
    }


def submit(db: Session, kind: str, user: User | None, params: dict | None = None) -> Job:
    """Queues an engine job and commits. Raises JobConflict if one is already active."""
    if kind not in HANDLERS:
        raise ValueError(f"unknown job kind {kind}")
    job = Job(
        kind=kind,
        status=QUEUED,
        params=params or {},
        lock_key=ENGINE_LOCK,
        requested_by_user_id=user.id if user else None,
        requested_at=utcnow(),
    )
    db.add(job)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise JobConflict(active(db)) from None
    audit.record(
        db, "job_queued", "job", job.id,
        actor=user.username if user else audit.log.SYSTEM_ACTOR,
        actor_role=user.role if user else audit.log.SYSTEM_ROLE,
        detail={"kind": kind, "params": params or {}},
    )  # fmt: skip
    db.commit()
    return job


def acquire(db: Session, kind: str, user: User) -> int:
    """For the synchronous engine operations (reset, bulk send): take the engine lock for
    their duration, recorded as a running job, so no queued job starts alongside them.
    Raises JobConflict if a job holds it. Release with `release`."""
    job = Job(
        kind=kind, status=RUNNING, params={}, lock_key=ENGINE_LOCK,
        requested_by_user_id=user.id, requested_at=utcnow(), started_at=utcnow(),
        heartbeat_at=utcnow(), worker_id="request",
    )  # fmt: skip
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise JobConflict(active(db)) from None
    return job.id


def release(db: Session, job_id: int, *, ok: bool, result: dict | None = None) -> None:
    if ok:
        _finish(db, job_id, SUCCEEDED, result=result)
    else:
        _finish(db, job_id, FAILED, error="The operation failed and was rolled back.")


def _finish(db: Session, job_id: int, status: str, *, result=None, error=None) -> None:
    job = db.get(Job, job_id)
    if job is None:
        # A demo reset rebuilds every table, this one included: nothing left to close.
        return
    job.status, job.lock_key, job.finished_at = status, None, utcnow()
    job.result, job.error = result, error
    db.commit()


def claim_next(db: Session, worker_id: str) -> Job | None:
    """The oldest queued job, now running for this worker; None if there is none (or another
    worker took it first)."""
    candidate = db.scalar(
        select(Job.id).where(Job.status == QUEUED).order_by(Job.requested_at, Job.id).limit(1)
    )
    if candidate is None:
        return None
    now = utcnow()
    claimed = db.execute(
        update(Job)
        .where(Job.id == candidate, Job.status == QUEUED)
        .values(status=RUNNING, started_at=now, heartbeat_at=now, worker_id=worker_id)
    ).rowcount
    db.commit()
    return db.get(Job, candidate) if claimed == 1 else None


def recover_stale(db: Session, now=None) -> int:
    """Running jobs abandoned by a worker that stopped: interrupted, lock released."""
    cutoff = (now or utcnow()) - STALE_AFTER
    now = now or utcnow()
    request_cutoff = now - REQUEST_LOCK_LIMIT
    stale = list(
        db.scalars(
            select(Job).where(
                Job.status == RUNNING,
                or_(
                    and_(Job.worker_id != "request", Job.heartbeat_at < cutoff),
                    # A web request that held the lock and never released it (the process
                    # died mid-reset, for example).
                    and_(Job.worker_id == "request", Job.started_at < request_cutoff),
                ),
            )
        )
    )
    for job in stale:
        job.status, job.lock_key, job.finished_at = INTERRUPTED, None, utcnow()
        job.error = "The worker stopped before the job finished. Nothing was retried."
        audit.record(db, "job_interrupted", "job", job.id, detail={"kind": job.kind})
    db.commit()
    return len(stale)


def sanitize(exc: BaseException) -> str:
    """Exception type and, for our own errors, the message: never a stack trace, SQL, or
    record contents that a driver message might carry."""
    name = type(exc).__name__
    own = type(exc).__module__.startswith("app.") and str(exc)
    return f"{name}: {str(exc)[:200]}" if own else f"{name} (see the worker log)"


class _Heartbeat(threading.Thread):
    def __init__(self, session_factory, job_id: int):
        super().__init__(daemon=True)
        self.session_factory, self.job_id, self.stop = session_factory, job_id, threading.Event()

    def run(self) -> None:
        while not self.stop.wait(HEARTBEAT_SECONDS):
            try:
                with self.session_factory() as db:
                    db.execute(
                        update(Job)
                        .where(Job.id == self.job_id, Job.status == RUNNING)
                        .values(heartbeat_at=utcnow())
                    )
                    db.commit()
            except Exception:  # noqa: BLE001 - a missed beat is not fatal
                log.warning("Heartbeat for job %s failed", self.job_id)


def execute(session_factory, job_id: int) -> str:
    """Runs one claimed job to its end state. Returns the final status."""
    beat = _Heartbeat(session_factory, job_id)
    beat.start()
    try:
        with session_factory() as db:
            job = db.get(Job, job_id)
            handler = HANDLERS[job.kind]

            def progress(text: str) -> None:
                job.progress = text[:120]
                job.heartbeat_at = utcnow()

            try:
                result = handler(db, job, progress)
            except Exception as exc:  # noqa: BLE001 - every failure ends the job the same way
                log.exception("Job %s (%s) failed", job_id, job.kind)
                db.rollback()
                _finish(db, job_id, FAILED, error=sanitize(exc))
                audit.record(db, "job_failed", "job", job_id, detail={"kind": job.kind})
                db.commit()
                return FAILED
            _finish(db, job_id, SUCCEEDED, result=result)
            return SUCCEEDED
    finally:
        beat.stop.set()


def run_pending(session_factory, worker_id: str | None = None) -> int:
    """Claims and runs queued jobs until none is left. Returns how many ran."""
    worker_id = worker_id or worker_identity()
    ran = 0
    while True:
        with session_factory() as db:
            job = claim_next(db, worker_id)
            job_id = job.id if job else None
        if job_id is None:
            return ran
        execute(session_factory, job_id)
        ran += 1


# --- Handlers -------------------------------------------------------------------------------


def _actor(db: Session, job: Job) -> tuple[str, str]:
    user = db.get(User, job.requested_by_user_id) if job.requested_by_user_id else None
    return (user.username, user.role) if user else (audit.log.SYSTEM_ACTOR, audit.log.SYSTEM_ROLE)


def _cycle(db: Session, job: Job, progress: Callable[[str], None]) -> dict:
    from app import cycle
    from app.engagement import simulator

    progress("Catching up the synthetic population")
    simulator.catch_up(db)
    db.commit()
    result = cycle.run_in_steps(
        db, retrain=bool((job.params or {}).get("retrain")), progress=progress
    )
    actor, role = _actor(db, job)
    audit.record(
        db, "cycle_scheduled" if (job.params or {}).get("scheduled") else "cycle_requested",
        "engine_cycle", result.cycle.id, actor=actor, actor_role=role,
        detail={"job": job.id},
    )  # fmt: skip
    db.commit()
    return {
        "cycle_id": result.cycle.id,
        "as_of_date": result.cycle.as_of_date.isoformat(),
        "stats": result.stats,
    }


def _play_out(db: Session, job: Job, progress: Callable[[str], None]) -> dict:
    from app.engagement import simulator

    progress("Resolving simulated responses and running the engine")
    result = simulator.play_out(db)
    actor, role = _actor(db, job)
    audit.record(
        db, "responses_played_out", "engine_cycle", result["cycle_id"], actor=actor,
        actor_role=role, detail={"responses": result["responses"], "job": job.id},
    )  # fmt: skip
    db.commit()
    progress("Publishing summaries")
    from app.analytics import snapshots

    snapshots.publish_safely(db, result["cycle_id"])
    return {**result, "as_of_date": result["as_of_date"].isoformat()}


HANDLERS: dict[str, Callable] = {"cycle": _cycle, "play_out": _play_out}
