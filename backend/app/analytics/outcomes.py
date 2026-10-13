"""Measure and learn: what worked for whom, how the models changed, and the holdout.

Three kinds of number appear on the learning screen and are never mixed up:

- **Observed**: counts of what happened after a touch (responded, refilled), with the number of
  touches behind every rate and a 95% Wilson interval. A rate from fewer than MIN_RATE_N
  touches is not shown.
- **Estimated**: the propensity models' predictions, stored on each recommendation when it was
  made. Shown beside the observed rate so the gap between them is visible (calibration).
- **Comparison**: only the randomised holdout supports a statement about effect, and on this
  synthetic population the outcomes come from the simulator's behaviour model, so even that is
  an estimate inside the simulation. Every other split (by segment, by period, by model
  version) is observational: groups differ in who was contacted and when.

Segments for patients are taken at the moment of the touch (point-in-time features, the same
rows the models train on). HCP segments are the HCP's current value segment.
"""

import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.engine_config import get_config
from app.features import engagement as eng
from app.features.adherence import compute_adherence
from app.features.population import load_population
from app.insights import MIN_RATE_N
from app.models import (
    EngineCycle,
    Interaction,
    MedicationFill,
    ModelVersion,
    Nba,
    Patient,
    PatientTherapy,
)
from app.models.enums import Outcome, PatientOrigin, ReviewStatus, TargetType
from app.nba import holdout
from app.nba.rationale import ACTION_LABEL, CHANNEL_LABEL, SEGMENT_LABEL

PATIENT_SITUATION = {
    "not_started": "Not started (no first fill)",
    "out": "Out of medication",
    "below": "Covered, PDC below threshold",
    "adherent": "Covered, PDC at or above threshold",
}
SEGMENT_DIMENSIONS = {
    "PATIENT": ("situation", "measure", "plan_type"),
    "HCP": ("segment", "specialty"),
}


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    if n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)]


def _rate(k: int, n: int) -> dict:
    if n < MIN_RATE_N:
        return {"rate": None, "ci": None}
    return {"rate": round(k / n, 3), "ci": wilson(k, n)}


def _situation(row: dict, threshold: float) -> str:
    if row["never_filled"]:
        return "not_started"
    if row["gap_days"] > 0:
        return "out"
    return "below" if row["pdc"] < threshold else "adherent"


def _source_matches(source: str, wanted: str) -> bool:
    if wanted == "all":
        return True
    return (source == "nba") if wanted == "engine" else (source == "history")


def training_rows(db: Session, pop, audience: str) -> list[dict]:
    """Every completed touch of the audience, with features as of the moment it was sent."""
    if audience == TargetType.PATIENT:
        return eng.patient_training_rows(pop, get_config(db, "pdc_window_days"))
    return eng.hcp_training_rows(pop)


def what_worked(
    db: Session,
    audience: str,
    segment_by: str,
    source: str,
    since: date | None,
    *,
    pop=None,
    rows: list[dict] | None = None,
) -> dict:
    """Observed response (and, for patients, fill) rates by segment x action x channel."""
    if segment_by not in SEGMENT_DIMENSIONS[audience]:
        raise ValueError(f"segment_by must be one of {', '.join(SEGMENT_DIMENSIONS[audience])}")
    pop = pop or load_population(db)
    threshold = get_config(db, "pdc_threshold")
    if rows is None:
        rows = training_rows(db, pop, audience)
    cells: dict[tuple, list[int]] = defaultdict(lambda: [0, 0, 0])
    months: dict[tuple, list[int]] = defaultdict(lambda: [0, 0, 0])
    for r in rows:
        if since and r["ts"].date() < since:
            continue
        key_m = (r["ts"].strftime("%Y-%m"), "engine" if r["source"] == "nba" else "earlier")
        months[key_m][0] += 1
        months[key_m][1] += r["engaged"]
        months[key_m][2] += r.get("filled", 0)
        if not _source_matches(r["source"], source):
            continue
        if audience == TargetType.PATIENT:
            segment = _situation(r, threshold) if segment_by == "situation" else r[segment_by]
        elif segment_by == "segment":
            hcp = pop.hcps.get(r["target_id"])
            segment = (hcp.segment if hcp else None) or "unknown"
        else:
            segment = r["specialty"] or "unknown"
        cell = cells[(segment, r["action"], r["channel"])]
        cell[0] += 1
        cell[1] += r["engaged"]
        cell[2] += r.get("filled", 0)

    def label(segment: str) -> str:
        if audience == TargetType.PATIENT and segment_by == "situation":
            return PATIENT_SITUATION[segment].replace("threshold", f"{threshold:.0%}")
        if audience == TargetType.HCP and segment_by == "segment":
            return SEGMENT_LABEL.get(segment, segment).capitalize()
        return str(segment).replace("_", " ").capitalize()

    out_rows = []
    for (segment, action, channel), (n, engaged, filled) in sorted(cells.items()):
        row = {
            "segment": segment,
            "segment_label": label(segment),
            "action": action,
            "action_label": ACTION_LABEL.get(action, action),
            "channel": channel,
            "channel_label": CHANNEL_LABEL.get(channel, channel),
            "n": n,
            "responded": engaged,
            "response": _rate(engaged, n),
        }
        if audience == TargetType.PATIENT:
            row.update(filled=filled, fill=_rate(filled, n))
        out_rows.append(row)

    # The best observed combination per segment (enough touches only). Observational.
    metric = "fill" if audience == TargetType.PATIENT else "response"
    best: dict[str, dict] = {}
    for row in out_rows:
        # Too few touches to rate, or nothing worked: no "best" to report.
        if not row[metric]["rate"]:
            continue
        current = best.get(row["segment"])
        if current is None or row[metric]["rate"] > current[metric]["rate"]:
            best[row["segment"]] = row
    trend = [
        {"month": m, "source": s, "n": n, "response": _rate(e, n)}
        | ({"fill": _rate(f, n)} if audience == TargetType.PATIENT else {})
        for (m, s), (n, e, f) in sorted(months.items())
    ]
    return {
        "audience": audience,
        "segment_by": segment_by,
        "dimensions": list(SEGMENT_DIMENSIONS[audience]),
        "source": source,
        "since": since,
        "min_n": MIN_RATE_N,
        "metric": metric,
        "touches": sum(r["n"] for r in out_rows),
        "rows": out_rows,
        "best": [
            {
                "segment": seg,
                "segment_label": row["segment_label"],
                "action_label": row["action_label"],
                "channel_label": row["channel_label"],
                "n": row["n"],
                metric: row[metric],
            }
            for seg, row in sorted(best.items())
        ],
        "trend": trend,
    }


def model_history(db: Session) -> list[dict]:
    """Each model's versions, how their holdout metrics changed, and what was observed for
    engine touches made while each version was the one in use (observational)."""
    versions = db.scalars(
        select(ModelVersion).order_by(ModelVersion.name, ModelVersion.version)
    ).all()
    engine_touches = db.execute(
        select(Interaction.target_type, Interaction.int_ts, Interaction.outcome).where(
            Interaction.source == "nba", Interaction.outcome != Outcome.PENDING
        )
    ).all()
    by_name: dict[str, list[ModelVersion]] = defaultdict(list)
    for v in versions:
        by_name[v.name].append(v)
    out = []
    for name, rows in by_name.items():
        audience = TargetType.HCP if name.startswith("hcp") else TargetType.PATIENT
        items = []
        for i, v in enumerate(rows):
            until = rows[i + 1].trained_ts if i + 1 < len(rows) else None
            touched = [
                o
                for t, ts, o in engine_touches
                if t == audience and ts >= v.trained_ts and (until is None or ts < until)
            ]
            n = len(touched)
            m = v.metrics or {}
            previous = (rows[i - 1].metrics or {}).get("auc") if i else None
            items.append(
                {
                    "version": v.version,
                    "algorithm": v.algorithm,
                    "trained_ts": v.trained_ts,
                    "as_of_date": v.as_of_date,
                    "is_active": v.is_active,
                    "auc": m.get("auc"),
                    "auc_baseline": m.get("auc_baseline"),
                    "auc_change": (
                        round(m["auc"] - previous, 3)
                        if previous is not None and m.get("auc") is not None
                        else None
                    ),
                    "brier": m.get("brier"),
                    "n_train": m.get("n_train"),
                    "n_test": m.get("n_test"),
                    "positive_rate": m.get("positive_rate"),
                    "engine_touches": n,
                    "observed_response": _rate(sum(eng.is_engaged(o) for o in touched), n),
                    "observed_fill": (
                        _rate(sum(o == Outcome.FILLED for o in touched), n)
                        if audience == TargetType.PATIENT
                        else None
                    ),
                }
            )
        out.append({"name": name, "audience": audience, "versions": items})
    return out


def calibration(db: Session) -> list[dict]:
    """Estimated (stored on the recommendation) against observed, for engine touches, by
    audience and action."""
    groups: dict[tuple, list] = defaultdict(list)
    for nba, outcome in db.execute(
        select(Nba, Interaction.outcome)
        .join(Interaction, Interaction.nba_id == Nba.id)
        .where(Interaction.source == "nba", Interaction.outcome != Outcome.PENDING)
    ):
        groups[(nba.target_type, nba.action)].append((nba.predicted or {}, outcome))
    out = []
    for (audience, action), items in sorted(groups.items()):
        n = len(items)
        key = "p_outcome" if audience == TargetType.PATIENT else "p_engage"
        estimates = [p.get(key) for p, _ in items if p.get(key) is not None]
        hit = (
            sum(o == Outcome.FILLED for _, o in items)
            if audience == TargetType.PATIENT
            else sum(eng.is_engaged(o) for _, o in items)
        )
        out.append(
            {
                "audience": audience,
                "action": action,
                "action_label": ACTION_LABEL.get(action, action),
                "outcome": "fill" if audience == TargetType.PATIENT else "response",
                "n": n,
                "estimated": (
                    round(sum(estimates) / len(estimates), 3)
                    if len(estimates) >= MIN_RATE_N
                    else None
                ),
                "observed": _rate(hit, n),
            }
        )
    return out


MIN_HOLDOUT_DAYS = 14


def holdout_comparison(db: Session, today: date) -> dict:
    """Synthetic patients in the holdout against the rest, since the holdout first applied."""
    config = get_config(db, "holdout")
    window = get_config(db, "pdc_window_days")
    threshold = get_config(db, "pdc_threshold")
    started = None
    for cycle in db.scalars(select(EngineCycle).order_by(EngineCycle.id)):
        if (cycle.stats or {}).get("patient_holdout"):
            started = cycle.as_of_date
            break
    base = {
        "enabled": bool(config.get("enabled")),
        "share": config["share"],
        "started": started,
        "min_days": MIN_HOLDOUT_DAYS,
        "min_n": MIN_RATE_N,
        "basis": (
            "Randomised by a fixed hash of the patient id; synthetic patients only. Outcomes "
            "come from the simulator's behaviour model, so a difference estimates the effect "
            "inside the simulation, not real-world lift."
        ),
    }
    if started is None:
        return base | {"status": "not_started", "groups": []}
    days = (today - started).days
    period = max(days, 1)
    patients = db.scalars(select(Patient).where(Patient.origin == PatientOrigin.SYNTHETIC)).all()
    group_of = {p.patient_id: holdout.assigned(p, config) for p in patients}
    therapies = db.scalars(
        select(PatientTherapy).where(
            PatientTherapy.patient_id.in_(group_of),
            PatientTherapy.review_status == ReviewStatus.CONFIRMED,
            PatientTherapy.measure.is_not(None),
        )
    ).all()
    fills: dict[int, list[tuple[date, int]]] = defaultdict(list)
    for therapy_id, day, supply in db.execute(
        select(MedicationFill.therapy_id, MedicationFill.fill_date, MedicationFill.days_supply)
    ):
        fills[therapy_id].append((day, supply))
    contacts: dict[str, int] = defaultdict(int)
    for target_id in db.scalars(
        select(Interaction.target_id).where(
            Interaction.target_type == TargetType.PATIENT,
            Interaction.source == "nba",
            Interaction.int_ts >= datetime.combine(started, time.min),
        )
    ):
        contacts[target_id] += 1
    stats = {True: [0, 0, 0, set()], False: [0, 0, 0, set()]}  # therapies, adherent, fills, pts
    for t in therapies:
        g = stats[group_of[t.patient_id]]
        own = fills.get(t.id, [])
        if not own:
            continue
        a = compute_adherence(own, t.start_date, today, min(window, period))
        g[0] += 1
        g[1] += a.pdc >= threshold
        g[2] += sum(1 for d, _ in own if started <= d <= today)
        g[3].add(t.patient_id)

    def group(flag: bool) -> dict:
        n, adherent, new_fills, pts = stats[flag]
        return {
            "group": "holdout" if flag else "engine",
            "label": "Held out (no engine outreach)" if flag else "Engine outreach as usual",
            "patients": len(pts),
            "therapies": n,
            "adherent": _rate(adherent, n),
            "fills_per_therapy": round(new_fills / n, 2) if n else None,
            "engine_contacts": sum(contacts[p] for p in pts),
        }

    held, rest = group(True), group(False)
    enough = (
        days >= MIN_HOLDOUT_DAYS
        and held["therapies"] >= MIN_RATE_N
        and rest["therapies"] >= MIN_RATE_N
    )
    difference = None
    if enough:
        p1, n1 = rest["adherent"]["rate"], rest["therapies"]
        p0, n0 = held["adherent"]["rate"], held["therapies"]
        se = math.sqrt(p1 * (1 - p1) / n1 + p0 * (1 - p0) / n0)
        diff = p1 - p0
        difference = {
            "adherent_share": round(diff, 3),
            "ci": [round(diff - 1.96 * se, 3), round(diff + 1.96 * se, 3)],
        }
    return base | {
        "status": "ready" if enough else "insufficient",
        "days": days,
        "groups": [rest, held],
        "difference": difference,
    }


def since_param(value: date | None, today: date) -> date | None:
    if value and value > today:
        raise ValueError("since must not be in the future.")
    return value if value else today - timedelta(days=365)
