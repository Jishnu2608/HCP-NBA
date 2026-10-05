"""Deterministic adherence-risk score (0-100) with the drivers that produced it.

Rule-based on purpose: every point is traceable to a named driver, which is what the
rationale and the audit trail quote.
"""

from dataclasses import dataclass

from app.features.adherence import Adherence
from app.models.enums import RiskSegment

PDC_SHORTFALL_SPAN = 0.20
TREND_SPAN_DAYS = 10


@dataclass(frozen=True)
class Risk:
    score: float
    segment: str
    drivers: list[dict]


def score_risk(adherence: Adherence, unresponsive_share: float, config: dict) -> Risk:
    """config needs: pdc_threshold, risk_weights, risk_gap_days_cap, risk_cutoffs."""
    weights = config["risk_weights"]
    threshold = config["pdc_threshold"]
    gap_cap = config["risk_gap_days_cap"]

    def clip(x: float) -> float:
        return max(0.0, min(1.0, x))

    parts = [
        (
            "pdc_shortfall",
            clip((threshold - adherence.pdc) / PDC_SHORTFALL_SPAN),
            f"Days covered {adherence.pdc:.0%}, below the {threshold:.0%} adherence threshold",
        ),
        (
            "gap_days",
            clip(adherence.gap_days / gap_cap),
            f"{adherence.gap_days} days without medication on hand",
        ),
        (
            "widening_gaps",
            clip(adherence.late_trend / TREND_SPAN_DAYS),
            f"Recent refills {adherence.late_trend:.0f} days later than earlier ones",
        ),
        (
            "unresponsive",
            clip(unresponsive_share),
            f"No response to {unresponsive_share:.0%} of the last outreach attempts",
        ),
        (
            "never_filled",
            1.0 if adherence.never_filled else 0.0,
            "No fill on record since the medication started",
        ),
    ]
    drivers = [
        {"code": code, "points": round(weights[code] * level, 1), "detail": detail}
        for code, level, detail in parts
        if level > 0
    ]
    drivers.sort(key=lambda d: d["points"], reverse=True)
    score = round(min(100.0, sum(d["points"] for d in drivers)), 1)

    cutoffs = config["risk_cutoffs"]
    if score >= cutoffs["high"]:
        segment = RiskSegment.HIGH
    elif score >= cutoffs["medium"]:
        segment = RiskSegment.MEDIUM
    else:
        segment = RiskSegment.LOW
    return Risk(score=score, segment=segment, drivers=drivers)
