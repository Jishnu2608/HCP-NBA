"""Figures behind the role-specific charts, computed from the records in one place.

Every chart in the product draws what these functions return; no screen recomputes a
business figure on its own, so "overdue", "answered" or "approved" means the same thing on
every page. They read only what the caller may already see (the API passes the scope), and
they never invent a value: when there is too little data, they say so and the screen shows
an empty state instead of a trend.

Sample rules, shared by every chart:
  MIN_RATE_N        a rate (responded / sent) is shown only from this many observations
  MIN_MEDIAN_N      a median or typical time is shown only from this many observations
  MIN_TREND_POINTS  a trend line is drawn only from this many dated points
"""

from statistics import median

MIN_RATE_N = 20
MIN_MEDIAN_N = 3
MIN_TREND_POINTS = 3


def typical(values: list[float]) -> dict:
    """Median and range of a list, or only its size when it is too small to summarise."""
    n = len(values)
    if n < MIN_MEDIAN_N:
        return {"n": n, "median": None, "min": None, "max": None}
    return {
        "n": n,
        "median": round(median(values), 1),
        "min": round(min(values), 1),
        "max": round(max(values), 1),
    }
