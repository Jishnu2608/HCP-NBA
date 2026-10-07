"""Feature refresh and model training steps of the engine cycle.

Run both from the command line:  python -m app.pipeline
"""

import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.engine_config import DEFAULTS, get_config
from app.features import engagement as eng
from app.features.adherence import compute_adherence
from app.features.population import Population, load_population
from app.models import AdherenceSnapshot, FeatureSnapshot, ModelVersion
from app.models.enums import RiskSegment, TargetType
from app.scoring import propensity
from app.scoring.risk import score_risk

PATIENT_ENGAGE = propensity.Spec(
    "patient_engage", "engaged", eng.PATIENT_NUMERIC, eng.PATIENT_CATEGORICAL, "rate_channel"
)
PATIENT_FILL = propensity.Spec(
    "patient_fill", "filled", eng.PATIENT_NUMERIC, eng.PATIENT_CATEGORICAL, "fill_rate_action"
)
HCP_ENGAGE = propensity.Spec(
    "hcp_engage", "engaged", eng.HCP_NUMERIC, eng.HCP_CATEGORICAL, "rate_channel"
)

PATIENT_CHANNELS = ("sms", "email", "portal", "phone")
HCP_CHANNELS = ("email", "portal", "rep_visit")
_RISK_ORDER = {RiskSegment.LOW: 0, RiskSegment.MEDIUM: 1, RiskSegment.HIGH: 2}


def engine_settings(db: Session) -> dict:
    return {key: get_config(db, key) for key in DEFAULTS}


def _channel_summary(state: eng.EngagementState, channels, prior: float) -> dict:
    return {
        ch: {
            "sent": state.sent[("channel", ch)],
            "engaged": state.engaged[("channel", ch)],
            "rate": round(state.rate(("channel", ch), prior), 3),
        }
        for ch in channels
    }


def _best_channel(summary: dict) -> str | None:
    tried = {ch: s for ch, s in summary.items() if s["sent"]}
    return max(tried, key=lambda ch: tried[ch]["rate"]) if tried else None


def _patient_adherence(db: Session, pop: Population, pid: str, settings: dict) -> list[float]:
    """Writes today's adherence snapshot for each confirmed therapy and the patient's risk
    segment. Returns the days-covered figures."""
    patient = pop.patients[pid]
    state = eng.patient_state(pop, pid)
    worst = RiskSegment.LOW
    pdcs = []
    for therapy in pop.therapies.get(pid, []):
        adherence = compute_adherence(
            pop.fills.get(therapy.id, []),
            therapy.start_date,
            pop.as_of,
            settings["pdc_window_days"],
        )
        risk = score_risk(adherence, state.unresponsive_share(), settings)
        db.add(
            AdherenceSnapshot(
                patient_id=pid,
                therapy_id=therapy.id,
                as_of_date=pop.as_of,
                period_start=adherence.period_start,
                period_end=adherence.period_end,
                pdc=adherence.pdc,
                mpr=adherence.mpr,
                last_fill_date=adherence.last_fill_date,
                gap_days=adherence.gap_days,
                risk_score=risk.score,
                risk_segment=risk.segment,
            )
        )
        pdcs.append(adherence.pdc)
        if _RISK_ORDER[risk.segment] > _RISK_ORDER[worst]:
            worst = risk.segment
    # A patient with no confirmed medication has no adherence risk to speak of.
    patient.risk_segment = worst if pop.therapies.get(pid) else None
    return pdcs


def refresh_patient(db: Session, patient_id: str) -> None:
    """One patient's adherence and risk, now: after a confirmation, refill or stop, the
    patient, their care manager and their HCP see the same figures straight away, computed
    exactly as the engine cycle computes them."""
    pop = load_population(db, patient_id=patient_id)
    if patient_id not in pop.patients:
        return
    db.execute(
        delete(AdherenceSnapshot).where(
            AdherenceSnapshot.patient_id == patient_id, AdherenceSnapshot.as_of_date == pop.as_of
        )
    )
    _patient_adherence(db, pop, patient_id, engine_settings(db))
    db.flush()


def needs_refresh(db: Session, therapy, snapshot, today) -> bool:
    """A real patient's confirmed medication whose figures are not as of today."""
    from app.clinical import records
    from app.models import Patient
    from app.models.enums import ReviewStatus

    if therapy.review_status != ReviewStatus.CONFIRMED or not therapy.days_supply:
        return False
    if snapshot is not None and snapshot.as_of_date >= today:
        return False
    return records.is_real(db.get(Patient, therapy.patient_id))


def refresh_real_patients(db: Session) -> int:
    """Start-up repair: no snapshot dated after today can be read as current, and every real
    patient's adherence is recomputed as of today with the cycle's own code."""
    from sqlalchemy import select

    from app.core import clock
    from app.models import Patient
    from app.models.enums import PatientOrigin

    db.execute(delete(AdherenceSnapshot).where(AdherenceSnapshot.as_of_date > clock.today()))
    ids = db.scalars(
        select(Patient.patient_id).where(Patient.origin != PatientOrigin.SYNTHETIC)
    ).all()
    for pid in ids:
        refresh_patient(db, pid)
    return len(ids)


def refresh_patient_features(db: Session, pop: Population, settings: dict) -> dict:
    as_of = pop.as_of
    db.execute(delete(AdherenceSnapshot).where(AdherenceSnapshot.as_of_date == as_of))
    db.execute(
        delete(FeatureSnapshot).where(
            FeatureSnapshot.as_of_date == as_of, FeatureSnapshot.target_type == TargetType.PATIENT
        )
    )
    segments: Counter = Counter()
    pdcs = []
    for pid, patient in pop.patients.items():
        state = eng.patient_state(pop, pid)
        pdcs.extend(_patient_adherence(db, pop, pid, settings))
        if patient.risk_segment:
            segments[patient.risk_segment] += 1

        channels = _channel_summary(state, PATIENT_CHANNELS, eng.PATIENT_ENGAGE_PRIOR)
        db.add(
            FeatureSnapshot(
                target_type=TargetType.PATIENT,
                target_id=pid,
                as_of_date=as_of,
                features={
                    "channels": channels,
                    "observed_channel_affinity": _best_channel(channels),
                    "touches_total": state.sent[("all",)],
                    "engagement_rate": round(state.rate(("all",), eng.PATIENT_ENGAGE_PRIOR), 3),
                    "recent_14": state.recent(as_of, 14),
                    "days_since_last": state.days_since_last(as_of),
                    "unresponsive_share": round(state.unresponsive_share(), 2),
                },
            )
        )
    db.flush()
    adherent = sum(1 for p in pdcs if p >= settings["pdc_threshold"])
    return {
        "patients_by_risk": {str(k): v for k, v in segments.items()},
        "therapies": len(pdcs),
        "mean_pdc": round(sum(pdcs) / len(pdcs), 3),
        "adherent_share": round(adherent / len(pdcs), 3),
    }


def refresh_hcp_features(db: Session, pop: Population, settings: dict) -> dict:
    as_of = pop.as_of
    db.execute(
        delete(FeatureSnapshot).where(
            FeatureSnapshot.as_of_date == as_of, FeatureSnapshot.target_type == TargetType.HCP
        )
    )
    states = {hcp_id: eng.hcp_state(pop, hcp_id) for hcp_id in pop.hcps}
    volumes = sorted(h.rx_volume_annual for h in pop.hcps.values())
    rates = sorted(s.rate(("all",), eng.HCP_ENGAGE_PRIOR) for s in states.values())
    median_rate = rates[len(rates) // 2]
    tiers = settings["hcp_tiers"]

    def percentile(sorted_values, x) -> float:
        return sum(1 for v in sorted_values if v <= x) / len(sorted_values)

    segments: Counter = Counter()
    for hcp_id, hcp in pop.hcps.items():
        state = states[hcp_id]
        rate = state.rate(("all",), eng.HCP_ENGAGE_PRIOR)
        vol_pct = percentile(volumes, hcp.rx_volume_annual)
        tier = (
            "high"
            if vol_pct >= tiers["high"]
            else "medium"
            if vol_pct >= tiers["medium"]
            else "low"
        )
        level = "engaged" if rate >= median_rate else "dormant"
        channels = _channel_summary(state, HCP_CHANNELS, eng.HCP_ENGAGE_PRIOR)

        if hcp.origin != "synthetic":
            # An invited HCP's prescribing volume is not known, and with no contact yet
            # nothing is known about engagement: neither is guessed.
            level = level if any(state.sent.values()) else "new"
            hcp.segment = f"unrated_{level}"
            hcp.value_score = None
        else:
            hcp.segment = f"{tier}_value_{level}"
            hcp.value_score = round(100 * (0.7 * vol_pct + 0.3 * percentile(rates, rate)), 1)
        hcp.channel_affinity = _best_channel(channels)
        segments[hcp.segment] += 1

        topics = {
            kind: {
                key[1]: round(state.rate(key, eng.HCP_ENGAGE_PRIOR), 3)
                for key in state.sent
                if key[0] == kind
            }
            for kind in ("measure", "subtopic")
        }
        db.add(
            FeatureSnapshot(
                target_type=TargetType.HCP,
                target_id=hcp_id,
                as_of_date=as_of,
                features={
                    "channels": channels,
                    "topic_rates": topics,
                    "volume_percentile": round(vol_pct, 3),
                    "touches_total": state.sent[("all",)],
                    "engagement_rate": round(rate, 3),
                    "recent_30": state.recent(as_of, 30),
                    "days_since_last": state.days_since_last(as_of),
                },
            )
        )
    db.flush()
    return {"hcps_by_segment": dict(sorted(segments.items()))}


def refresh_features(db: Session, pop: Population | None = None) -> dict:
    pop = pop or load_population(db)
    settings = engine_settings(db)
    stats = refresh_patient_features(db, pop, settings)
    stats.update(refresh_hcp_features(db, pop, settings))
    return stats


def train_models(
    db: Session, pop: Population | None = None, model_dir: Path | None = None
) -> list[ModelVersion]:
    pop = pop or load_population(db)
    model_dir = model_dir or propensity.MODEL_DIR
    patient_rows = eng.patient_training_rows(pop, get_config(db, "pdc_window_days"))
    hcp_rows = eng.hcp_training_rows(pop)
    versions = []
    for spec, rows in (
        (PATIENT_ENGAGE, patient_rows),
        (PATIENT_FILL, patient_rows),
        (HCP_ENGAGE, hcp_rows),
    ):
        model, metrics = propensity.train(spec, rows, pop.as_of)
        versions.append(propensity.register(db, spec, model, metrics, pop.as_of, model_dir))
    return versions


def main() -> int:
    with SessionLocal() as db:
        pop = load_population(db)
        stats = refresh_features(db, pop)
        versions = train_models(db, pop)
        db.commit()
        print(f"As of {pop.as_of}\n")
        for key, value in stats.items():
            print(f"  {key}: {value}")
        print("\nModels")
        for v in versions:
            m = v.metrics
            print(
                f"  {v.name} v{v.version}: AUC {m['auc']} (baseline {m['auc_baseline']}, "
                f"GBM {m['auc_challenger_gbm']}), Brier {m['brier']} "
                f"(base rate {m['brier_base_rate']}), n={m['n_train']}+{m['n_test']}, "
                f"positive {m['positive_rate']:.1%}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
