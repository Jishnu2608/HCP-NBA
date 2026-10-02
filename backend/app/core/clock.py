"""Demo clock: the single "today" the whole system reads instead of wall-clock time.

Lets the demo advance time to show responses, refills and the next recommendation cycle.
"""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import EngineConfig

CLOCK_KEY = "demo_as_of_date"


def get_today(db: Session) -> date:
    row = db.get(EngineConfig, CLOCK_KEY)
    return date.fromisoformat(row.value) if row else date.today()


def set_today(db: Session, value: date) -> date:
    row = db.get(EngineConfig, CLOCK_KEY)
    if row:
        row.value = value.isoformat()
    else:
        db.add(EngineConfig(key=CLOCK_KEY, value=value.isoformat()))
    db.flush()
    return value


def advance(db: Session, days: int) -> date:
    if days < 1:
        raise ValueError("days must be >= 1")
    return set_today(db, get_today(db) + timedelta(days=days))
