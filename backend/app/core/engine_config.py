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
    # Timing. A refill reminder for a supply that has not yet run out is scheduled this many
    # days before the run-out date. A target held back only by the contact limits gets a
    # recommendation scheduled for the first allowed day, if that day is within the horizon
    # (0 = defer as before). Every gate is checked again at approval and at send.
    "refill_lead_days": 3,
    "nba_schedule_ahead_days": 7,
    # De-identified aggregates (HCP panel adherence): a group smaller than this is never shown
    # or used, and the share of patients at the PDC threshold below which adherence-support
    # material for that measure is favoured for the HCP.
    "aggregate_min_cohort": 11,
    "panel_adherence_target": 0.80,
    # Persistence: a gap in supply longer than this ends a course of therapy.
    "persistence_gap_days": 60,
    # Randomised holdout of synthetic patients (never real people): a fixed share receives no
    # engine outreach so outcomes can be compared. Off unless an administrator turns it on.
    "holdout": {"enabled": False, "share": 0.10},
    # Scheduled engine cycle: once a day after this UTC hour. Off unless turned on.
    "auto_cycle": {"enabled": False, "hour_utc": 6},
}


# Settings whose value is a fraction between 0 and 1 (inclusive).
FRACTIONS = {"pdc_threshold", "hcp_tiers", "panel_adherence_target", "holdout"}
NUMBER_MAX = 100_000


def validate_value(key: str, value: Any) -> str | None:
    """None if `value` has the same shape as the default (same keys, numbers where numbers
    are expected, within bounds); otherwise a message saying what is wrong. Frequency caps
    are a hard gate, so a malformed value must never reach the engine."""

    def check(default: Any, given: Any, path: str) -> str | None:
        where = path or key
        if isinstance(default, dict):
            if not isinstance(given, dict) or set(given) != set(default):
                return f"{where} must have exactly the keys: {', '.join(sorted(default))}."
            for name, sub in default.items():
                problem = check(sub, given[name], f"{where}.{name}")
                if problem:
                    return problem
            return None
        if isinstance(default, bool) or isinstance(given, bool):
            return None if type(given) is type(default) else f"{where} has the wrong type."
        if isinstance(default, int | float):
            if not isinstance(given, int | float) or given != given:  # NaN check
                return f"{where} must be a number."
            if given < 0 or given > NUMBER_MAX:
                return f"{where} must be between 0 and {NUMBER_MAX}."
            if isinstance(default, int) and not isinstance(given, int) and given != int(given):
                return f"{where} must be a whole number."
            if key in FRACTIONS and given > 1:
                return f"{where} must be between 0 and 1."
            return None
        return None if type(given) is type(default) else f"{where} has the wrong type."

    problem = check(DEFAULTS[key], value, "")
    if problem is None and key == "auto_cycle" and value["hour_utc"] > 23:
        return "auto_cycle.hour_utc must be between 0 and 23."
    if problem is None and key == "aggregate_min_cohort" and value < 5:
        # Below this, de-identified figures could point at individuals.
        return "aggregate_min_cohort must be at least 5."
    return problem


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
