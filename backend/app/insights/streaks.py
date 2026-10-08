"""Streaks: a run of consecutive good days or good outcomes, derived only from records.

Each function documents exactly what counts. A streak is never kept in the browser and
never reset or created by anything other than the underlying records. None means there is
nothing to measure yet (the screen then shows no streak at all); 0 means the latest day or
outcome broke it.

Streaks reward finishing legitimate work on time. None of them counts sends, contacts or
sign-ins, so none can be raised by contacting people more often.
"""

from collections.abc import Iterable
from datetime import date, datetime, timedelta

# How far back a streak looks. Longer runs are shown as "365+ days".
HORIZON_DAYS = 365


def medicine_on_hand(
    coverage: list[tuple[date, list[tuple[date, date]]]], today: date
) -> dict | None:
    """Patient: "days in a row with your medicine on hand".

    The number of consecutive days, ending today, on which every confirmed active medication
    that had started was covered by a recorded fill (a refill the patient logged, the care
    team recorded or claims show). It measures supply on hand, never doses taken: the
    product has no dose records.

    `coverage` is one (start_date, covered [start, end) intervals) pair per medication.
    """
    if not coverage:
        return None
    days = 0
    day = today
    while days < HORIZON_DAYS:
        started = [intervals for start, intervals in coverage if start <= day]
        if not started:
            break
        if not all(any(a <= day < b for a, b in intervals) for intervals in started):
            break
        days += 1
        day -= timedelta(days=1)
    return {
        "value": days,
        "unit": "days",
        "capped": days >= HORIZON_DAYS,
        "definition": "Consecutive days, up to today, on which every confirmed medication was "
        "covered by a recorded refill. Supply on hand, not doses taken.",
    }


def no_overdue_days(
    items: Iterable[tuple[date, datetime, datetime | None]], today: date
) -> dict | None:
    """Care manager: "days in a row without an overdue item".

    The number of consecutive days, ending today, at the end of which none of the care
    manager's dated work (follow-ups and other care requests with a due date, on their panel)
    was still open after its due date. An item is open on a day from the day it was created
    until the day it was closed. Counting stops at the first day with an overdue item, or
    at the day the earliest dated item was created.

    `items` are (due_date, created_at, closed_at) triples.
    """
    items = list(items)
    if not items:
        return None
    first = min(created.date() for _, created, _ in items)
    days = 0
    day = today
    while day >= first and days < HORIZON_DAYS:
        overdue = any(
            due < day and created.date() <= day and (closed is None or closed.date() > day)
            for due, created, closed in items
        )
        if overdue:
            break
        days += 1
        day -= timedelta(days=1)
    return {
        "value": days,
        "unit": "days",
        "capped": days >= HORIZON_DAYS,
        "definition": "Consecutive days, up to today, that ended with no follow-up or care "
        "request on your panel still open after its due date.",
    }


def on_time_completions(done: Iterable[tuple[datetime, date]]) -> dict | None:
    """Representative: "follow-ups and meetings completed on time".

    Counted back from the most recently completed follow-up or meeting: how many in a row
    were completed on or before their due date (follow-ups) or scheduled day (meetings). A
    late completion ends the run. Content sends never count.

    `done` are (completed_at, due_day) pairs.
    """
    rows = sorted(done, key=lambda r: r[0], reverse=True)
    if not rows:
        return None
    count = 0
    for completed, due in rows:
        if completed.date() > due:
            break
        count += 1
    return {
        "value": count,
        "unit": "in a row",
        "capped": False,
        "definition": "Your most recent follow-ups and meetings completed on or before the "
        "day they were due, counted back until one was late.",
    }


def answered_in_a_row(outcomes: Iterable[tuple[datetime, bool]]) -> dict | None:
    """HCP: "consultations answered in a row".

    Counted back from the most recent consultation outcome: how many in a row the HCP
    answered rather than returned (declined) to the care manager. It is not about speed;
    consultations withdrawn by the care team or returned automatically do not count either
    way.

    `outcomes` are (when, answered) pairs.
    """
    rows = sorted(outcomes, key=lambda r: r[0], reverse=True)
    if not rows:
        return None
    count = 0
    for _, answered in rows:
        if not answered:
            break
        count += 1
    return {
        "value": count,
        "unit": "in a row",
        "capped": False,
        "definition": "Consultations routed to you that you answered, counted back from the "
        "latest until one was declined. Speed is not counted.",
    }
