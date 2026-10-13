"""The background worker: runs queued engine jobs and the scheduled daily cycle.

    python -m app.worker            run until stopped (Ctrl+C)
    python -m app.worker --once     run what is queued now, then exit

Run it next to the web server against the same database (NBA_DATABASE_URL). Several workers
may run at once: each job is claimed by one conditional UPDATE, and the engine lock keeps
engine operations one at a time. On start, running jobs left behind by a worker that stopped
are marked interrupted (never re-run automatically).

In a local run the web process can host one worker thread instead (`NBA_WORKER_MODE=embedded`,
the default for NBA_ENVIRONMENT=local), so a single `uvicorn` process is enough for a demo.
"""

import logging
import sys
import threading
import time

from app import jobs, scheduler
from app.core.config import get_settings
from app.core.db import SessionLocal

log = logging.getLogger("nba.worker")


def tick(session_factory=SessionLocal, worker_id: str | None = None) -> int:
    """One pass: recover abandoned jobs, queue the scheduled cycle if due, run what is
    queued. Returns how many jobs ran."""
    with session_factory() as db:
        recovered = jobs.recover_stale(db)
        if recovered:
            log.warning("Marked %s abandoned job(s) interrupted", recovered)
        scheduler.enqueue_if_due(db)
    return jobs.run_pending(session_factory, worker_id)


def loop(stop: threading.Event, poll_seconds: float, session_factory=SessionLocal) -> None:
    worker_id = jobs.worker_identity()
    log.info("Worker %s started (poll every %ss)", worker_id, poll_seconds)
    while not stop.is_set():
        try:
            tick(session_factory, worker_id)
        except Exception:  # noqa: BLE001 - a failed pass is logged and retried next poll
            log.exception("Worker pass failed")
        stop.wait(poll_seconds)


def start_embedded(poll_seconds: float) -> threading.Event:
    """A worker thread inside the web process (local runs). Returns its stop event."""
    stop = threading.Event()
    threading.Thread(target=loop, args=(stop, poll_seconds), daemon=True, name="nba-worker").start()
    return stop


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    get_settings().secret("jwt_secret")  # same configuration checks as the web process
    if "--once" in sys.argv:
        ran = tick()
        print(f"Ran {ran} job(s)")
        return 0
    stop = threading.Event()
    try:
        loop(stop, get_settings().worker_poll_seconds)
    except KeyboardInterrupt:
        stop.set()
        time.sleep(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
