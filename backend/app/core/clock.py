"""Today. The application always works on the real date of the server.

There is no adjustable "demo date": recommendations, adherence, medication dates, consent and
analytics all use the actual current date. Synthetic history keeps its own historical dates
(anchored to the day it was generated); `simulated_through` only records how far the outcome
simulator has played the synthetic population forward, it is not "today".

One time base: every stored instant is UTC (`tables.utcnow`), and "today" is the UTC date,
so an event's timestamp, its ordering and every date-based rule (refill dates, gaps,
frequency caps, consent periods) refer to the same moment. The browser shows instants in
the viewer's local time.

Tests may pin the date with `override`; application code never does.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

from sqlalchemy.orm import Session

from app.models import EngineConfig
from app.models.tables import utcnow

SIMULATED_KEY = "simulated_through"

_override: date | None = None


def today() -> date:
    return _override or utcnow().date()


def get_today(_db: Session | None = None) -> date:
    """The real current date. (Takes the session for call-site compatibility.)"""
    return today()


@contextmanager
def override(value: date) -> Iterator[date]:
    """Tests only: pretend the current date is `value` inside the block."""
    global _override
    previous, _override = _override, value
    try:
        yield value
    finally:
        _override = previous


def simulated_through(db: Session) -> date | None:
    row = db.get(EngineConfig, SIMULATED_KEY)
    return date.fromisoformat(row.value) if row else None


def set_simulated_through(db: Session, value: date) -> None:
    row = db.get(EngineConfig, SIMULATED_KEY)
    if row:
        row.value = value.isoformat()
    else:
        db.add(EngineConfig(key=SIMULATED_KEY, value=value.isoformat()))
    db.flush()
