"""Tunable engine settings. Defaults live here; overrides are stored in the engine_config table."""

from typing import Any

from sqlalchemy.orm import Session

from app.models import EngineConfig

DEFAULTS: dict[str, Any] = {
    # Rolling window for proportion of days covered, and the adequacy threshold.
    "pdc_window_days": 180,
    "pdc_threshold": 0.80,
    # Points each driver can add to the 0-100 adherence risk score.
    "risk_weights": {
        "pdc_shortfall": 30,
        "gap_days": 35,
        "widening_gaps": 15,
        "unresponsive": 10,
        "never_filled": 10,
    },
    "risk_gap_days_cap": 21,
    "risk_cutoffs": {"high": 40, "medium": 15},
    # HCP value tiers by prescribing-volume percentile.
    "hcp_tiers": {"high": 0.80, "medium": 0.50},
    # Contact limits per target. A hard gate, like MLR status and consent.
    "frequency_caps": {
        "PATIENT": {"window_days": 14, "max_touches": 2, "min_gap_days": 3},
        "HCP": {"window_days": 30, "max_touches": 3, "min_gap_days": 5},
    },
    # Recommendation policy.
    "nba_due_soon_days": 5,
    # Score points deducted for channels that use staff time.
    "nba_channel_cost": {"phone": 2.0, "rep_visit": 8.0},
    "nba_withheld_margin": 1.10,
    "nba_hcp_min_score": 8.0,
    "nba_repeat_content_days": 90,
}


def get_config(db: Session, key: str) -> Any:
    row = db.get(EngineConfig, key)
    return row.value if row else DEFAULTS[key]


def set_config(db: Session, key: str, value: Any) -> None:
    if key not in DEFAULTS:
        raise KeyError(key)
    row = db.get(EngineConfig, key)
    if row:
        row.value = value
    else:
        db.add(EngineConfig(key=key, value=value))
    db.flush()
