"""The scheduled engine cycle: the loop turns once a day without anyone pressing a button.

Off by default (`auto_cycle.enabled` in the engine settings). When on, the first check after
`auto_cycle.hour_utc` on a day with no cycle yet runs the same steps as the Engine page's
"run cycle": the synthetic population's natural refills are caught up to today, features are
refreshed and the engine runs. No retraining (an administrator chooses when models change),
nothing is approved or sent (people do that), and every run is audited as
`cycle_scheduled`. A cycle someone already ran today counts: the schedule never adds a second.

The check is a plain function (`run_if_due`), so it is tested without a clock or a thread; the
application starts a small background loop that calls it every `scheduler_poll_seconds`.
"""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit, cycle
from app.core.engine_config import get_config
from app.engagement import simulator
from app.models import EngineCycle

log = logging.getLogger("nba.scheduler")


def due(db: Session, now: datetime) -> bool:
    config = get_config(db, "auto_cycle")
    if not config.get("enabled") or now.hour < config["hour_utc"]:
        return False
    ran_today = db.scalar(
        select(func.count()).select_from(EngineCycle).where(EngineCycle.as_of_date == now.date())
    )
    return not ran_today


def run_if_due(db: Session, now: datetime | None = None) -> int | None:
    """Runs today's cycle if the schedule says so; returns its id, or None. Commits."""
    now = now or datetime.now(UTC).replace(tzinfo=None)
    if not due(db, now):
        return None
    hour = get_config(db, "auto_cycle")["hour_utc"]
    simulator.catch_up(db)
    result = cycle.run(db, retrain=False)
    audit.record(
        db,
        "cycle_scheduled",
        "engine_cycle",
        result.cycle.id,
        reason=f"Scheduled daily cycle (after {hour:02d}:00 UTC)",
        detail=result.stats,
    )
    db.commit()
    return result.cycle.id


async def loop(session_factory, poll_seconds: int) -> None:
    """Background loop started by the application. Errors are logged, never raised: a failed
    run is retried at the next poll."""
    while True:
        await asyncio.sleep(poll_seconds)
        try:
            await asyncio.to_thread(_tick, session_factory)
        except Exception:  # noqa: BLE001 - keep the loop alive; the failure is logged
            log.exception("Scheduled engine cycle failed")


def _tick(session_factory) -> None:
    with session_factory() as db:
        cycle_id = run_if_due(db)
        if cycle_id:
            log.info("Scheduled engine cycle %s completed", cycle_id)
