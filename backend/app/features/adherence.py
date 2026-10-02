"""Medication adherence from fill history: PDC, MPR, gap days and refill-lateness trend.

Pure functions over (fill_date, days_supply) pairs so they can be evaluated at any past
date, which is what keeps model training free of look-ahead.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Adherence:
    period_start: date
    period_end: date
    pdc: float
    mpr: float
    last_fill_date: date | None
    gap_days: int
    n_fills: int
    never_filled: bool
    # Mean lateness of the latest refills minus the earlier ones. Positive = gaps are widening.
    late_trend: float
    mean_lateness: float


def coverage_intervals(fills: Iterable[tuple[date, int]]) -> list[tuple[date, date]]:
    """Covered [start, end) intervals. An early refill is carried forward, not double counted."""
    intervals: list[tuple[date, date]] = []
    cursor: date | None = None
    for fill_date, days_supply in sorted(fills):
        start = max(fill_date, cursor) if cursor else fill_date
        cursor = start + timedelta(days=days_supply)
        intervals.append((start, cursor))
    return intervals


def compute_adherence(
    fills: Iterable[tuple[date, int]], therapy_start: date, as_of: date, window_days: int = 180
) -> Adherence:
    """Adherence for one therapy using only fills on or before the as-of date."""
    known = sorted((d, s) for d, s in fills if d <= as_of)
    if not known:
        return Adherence(
            period_start=therapy_start,
            period_end=as_of,
            pdc=0.0,
            mpr=0.0,
            last_fill_date=None,
            gap_days=max(0, (as_of - therapy_start).days),
            n_fills=0,
            never_filled=True,
            late_trend=0.0,
            mean_lateness=0.0,
        )

    intervals = coverage_intervals(known)
    period_start = max(known[0][0], as_of - timedelta(days=window_days - 1))
    period_days = (as_of - period_start).days + 1
    period_close = as_of + timedelta(days=1)

    covered = sum(
        max(0, (min(end, period_close) - max(start, period_start)).days) for start, end in intervals
    )
    supplied = sum(s for d, s in known if d >= period_start)

    lateness = [(known[i + 1][0] - intervals[i][1]).days for i in range(len(known) - 1)]
    recent, earlier = lateness[-3:], lateness[:-3]
    late_trend = (
        sum(recent) / len(recent) - sum(earlier) / len(earlier) if recent and earlier else 0.0
    )

    return Adherence(
        period_start=period_start,
        period_end=as_of,
        pdc=round(min(1.0, covered / period_days), 4),
        mpr=round(supplied / period_days, 4),
        last_fill_date=known[-1][0],
        gap_days=max(0, (as_of - intervals[-1][1]).days),
        n_fills=len(known),
        never_filled=False,
        late_trend=round(late_trend, 2),
        mean_lateness=round(sum(lateness) / len(lateness), 2) if lateness else 0.0,
    )
